"""Controlled runtime migration operations for Konnaxion instances.

The Agent runs database migrations as an allowlisted lifecycle step. This module
never executes arbitrary shell supplied by the Manager or UI. It only builds
known Docker Compose commands against the canonical Django service.

Production capsules must contain migration files already. The Agent runs:

    python manage.py migrate --noinput

It must not run ``makemigrations`` during normal capsule startup/update.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Mapping, Sequence

from kx_shared.konnaxion_constants import (
    DockerService,
    docker_project_name,
    instance_compose_file,
)


DEFAULT_TIMEOUT_SECONDS = 900


_POSTGRES_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


def reconcile_postgres_credentials(
    instance_id: str,
    *,
    compose_file: str | Path | None = None,
    project_name: str | None = None,
    timeout_seconds: int = 120,
) -> dict[str, object]:
    """Align the live PostgreSQL role password with the canonical runtime env.

    PostgreSQL only consumes ``POSTGRES_PASSWORD`` when a data directory is
    initialized.  During an update an existing volume can therefore keep an
    older role password while freshly rendered env files carry the current
    secret.  Legacy/update paths can also leave postgres.env, the password in
    django.env::DATABASE_URL, and runtime.env out of sync.  The database may be
    healthy in that state, but Django fails authentication before migrations
    can even begin.

    This reconciliation is deliberately non-destructive: it starts only the
    canonical postgres service, connects over the container-local Unix socket,
    and rotates the existing role password to the already-generated runtime
    secret.  The password is supplied on psql stdin and is never placed in the
    process argv or returned diagnostics.
    """

    from kx_agent.instances import secrets as secret_env

    plan = build_migration_plan(
        instance_id,
        compose_file=compose_file,
        project_name=project_name,
    )

    # IMPORTANT: do not use read_instance_env_files() here.  That helper is an
    # aggregate intended for diagnostics/Security Gate and later files such as
    # runtime.env overwrite earlier values.  Docker Compose does *not* inject
    # that aggregate into django-api: postgres.env is the source of truth for
    # POSTGRES_* and django.env is the source of truth for DATABASE_URL.
    env_dir = secret_env.instance_env_dir(instance_id)
    postgres_path = env_dir / secret_env.POSTGRES_ENV_FILE
    django_path = env_dir / secret_env.DJANGO_ENV_FILE
    runtime_path = env_dir / secret_env.RUNTIME_ENV_FILE

    if not postgres_path.is_file():
        raise MigrationError(f"Canonical postgres.env is missing for instance {instance_id!r}")
    if not django_path.is_file():
        raise MigrationError(f"Canonical django.env is missing for instance {instance_id!r}")

    postgres_env = secret_env.read_env_file(postgres_path)
    django_env = secret_env.read_env_file(django_path)
    runtime_env = secret_env.read_env_file(runtime_path) if runtime_path.is_file() else {}

    user = str(postgres_env.get("POSTGRES_USER") or "konnaxion").strip()
    database = str(postgres_env.get("POSTGRES_DB") or "konnaxion").strip()
    password = str(postgres_env.get("POSTGRES_PASSWORD") or "").strip()
    host = str(postgres_env.get("POSTGRES_HOST") or DockerService.POSTGRES.value).strip()
    port = str(postgres_env.get("POSTGRES_PORT") or "5432").strip()

    if not password:
        raise MigrationError("POSTGRES_PASSWORD is missing from canonical instance env")
    if not _POSTGRES_IDENTIFIER_RE.fullmatch(user):
        raise MigrationError("POSTGRES_USER is not a safe PostgreSQL identifier")
    if not _POSTGRES_IDENTIFIER_RE.fullmatch(database):
        raise MigrationError("POSTGRES_DB is not a safe PostgreSQL identifier")

    # Keep the env files that actually feed the containers coherent before
    # touching the live role.  This repairs legacy/update states where
    # postgres.env, django.env::DATABASE_URL and runtime.env drifted apart.
    database_url = secret_env.build_database_url(
        user=user,
        password=password,
        host=host,
        port=port,
        database=database,
    )

    env_files_rewritten: list[str] = []

    def rewrite_if_encoding_changed(path: Path, values: Mapping[str, str], filename: str) -> None:
        """Normalize legacy env-file encoding before any Compose command runs."""

        expected = secret_env.serialize_env_file(values)
        try:
            current = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            current = ""

        if current == expected:
            return

        secret_env.write_env_file_atomic(path, values)
        if filename not in env_files_rewritten:
            env_files_rewritten.append(filename)

    # django-api and Celery load django.env before postgres.env.  A legacy
    # DATABASE_URL in postgres.env therefore wins and can silently override the
    # repaired Django URL.  Remove it from the PostgreSQL service env entirely.
    if secret_env.DATABASE_URL in postgres_env:
        postgres_env.pop(secret_env.DATABASE_URL, None)
        rewrite_if_encoding_changed(
            postgres_path,
            postgres_env,
            secret_env.POSTGRES_ENV_FILE,
        )

    if django_env.get(secret_env.DATABASE_URL) != database_url:
        django_env[secret_env.DATABASE_URL] = database_url
        rewrite_if_encoding_changed(
            django_path,
            django_env,
            secret_env.DJANGO_ENV_FILE,
        )

    runtime_updates = {
        secret_env.POSTGRES_USER: user,
        secret_env.POSTGRES_PASSWORD: password,
        secret_env.POSTGRES_DB: database,
        secret_env.POSTGRES_HOST: host,
        secret_env.POSTGRES_PORT: port,
        secret_env.DATABASE_URL: database_url,
    }
    if any(runtime_env.get(k) != v for k, v in runtime_updates.items()):
        runtime_env.update(runtime_updates)
        rewrite_if_encoding_changed(
            runtime_path,
            runtime_env,
            secret_env.RUNTIME_ENV_FILE,
        )

    # v0.1.5: even when credential values are already coherent, normalize
    # legacy dotenv serialization before the first Docker Compose command.
    # This preserves the logical secrets but encodes each '$' as '$$' on disk
    # so Compose cannot mistake random secret fragments for variable names.
    rewrite_if_encoding_changed(
        postgres_path,
        postgres_env,
        secret_env.POSTGRES_ENV_FILE,
    )
    rewrite_if_encoding_changed(
        django_path,
        django_env,
        secret_env.DJANGO_ENV_FILE,
    )
    if runtime_path.is_file() or runtime_env:
        rewrite_if_encoding_changed(
            runtime_path,
            runtime_env,
            secret_env.RUNTIME_ENV_FILE,
        )

    # Older rendered Compose files can reference state/env.  Mirror only the
    # synchronized DB-related files; secrets remain mode 0600.
    secret_env.mirror_env_files_for_state_relative_compose(
        instance_id,
        {
            secret_env.POSTGRES_ENV_FILE: postgres_env,
            secret_env.DJANGO_ENV_FILE: django_env,
            secret_env.RUNTIME_ENV_FILE: runtime_env,
        },
        canonical_env_dir=env_dir,
    )

    base = [
        "docker",
        "compose",
        "-p",
        plan.project_name,
        "-f",
        str(plan.compose_file),
    ]

    started = subprocess.run(
        [*base, "up", "-d", DockerService.POSTGRES.value],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
    )
    if started.returncode != 0:
        raise MigrationError(
            "Failed to start PostgreSQL for credential reconciliation: "
            + _redact_secret_text(started.stderr or started.stdout, password)
        )

    ready_argv = [
        *base,
        "exec",
        "-T",
        DockerService.POSTGRES.value,
        "pg_isready",
        "-U",
        user,
        "-d",
        database,
    ]
    deadline = time.monotonic() + min(max(timeout_seconds, 10), 120)
    ready = None
    while time.monotonic() < deadline:
        ready = subprocess.run(
            ready_argv,
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if ready.returncode == 0:
            break
        time.sleep(2)
    if ready is None or ready.returncode != 0:
        detail = "" if ready is None else (ready.stderr or ready.stdout)
        raise MigrationError(
            "PostgreSQL did not become ready for credential reconciliation: "
            + _redact_secret_text(detail, password)
        )

    # Generated Konnaxion DB passwords intentionally avoid single quotes, but
    # quote defensively anyway so legacy values are safe too.
    sql_password = password.replace("'", "''")
    sql = f"ALTER ROLE \"{user}\" WITH PASSWORD '{sql_password}';\n"
    alter = subprocess.run(
        [
            *base,
            "exec",
            "-T",
            DockerService.POSTGRES.value,
            "psql",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            user,
            "-d",
            database,
        ],
        input=sql,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if alter.returncode != 0:
        raise MigrationError(
            "PostgreSQL credential reconciliation failed: "
            + _redact_secret_text(alter.stderr or alter.stdout, password)
        )

    return {
        "ok": True,
        "instance_id": instance_id,
        "service": DockerService.POSTGRES.value,
        "user": user,
        "database": database,
        "password_rotated": True,
        "env_synchronized": True,
        "env_files_rewritten": tuple(env_files_rewritten),
    }


def _redact_secret_text(value: str | None, secret: str) -> str:
    text = str(value or "").strip()
    return text.replace(secret, "<redacted>") if secret else text


class MigrationStatus(StrEnum):
    """Lifecycle status for a migration operation."""

    PLANNED = "planned"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


class MigrationError(RuntimeError):
    """Raised when a controlled migration command fails."""


@dataclass(frozen=True)
class MigrationCommand:
    """A safe, prebuilt migration command."""

    argv: tuple[str, ...]
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS

    def as_list(self) -> list[str]:
        return list(self.argv)


@dataclass(frozen=True)
class MigrationResult:
    """Result of a migration command execution."""

    status: MigrationStatus
    command: tuple[str, ...]
    returncode: int | None
    stdout: str
    stderr: str
    started_at: datetime
    finished_at: datetime
    timed_out: bool = False
    schema_integrity: Mapping[str, object] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == MigrationStatus.SUCCEEDED and self.returncode == 0

    def raise_for_failure(self) -> None:
        if not self.ok:
            raise MigrationError(
                "Migration command failed "
                f"(status={self.status}, returncode={self.returncode}). "
                f"stderr={self.stderr.strip()!r}"
            )


SCHEMA_INTEGRITY_PREFIX = "KX_SCHEMA_INTEGRITY="


def _schema_integrity_script(*, repair: bool) -> str:
    """Return the fixed Django probe used to verify migration/schema coherence.

    The script is generated by the Agent and is never supplied by the Manager.
    It compares the final Django migration state with the actual PostgreSQL
    tables. When ``repair`` is true it may clear stale migration records only
    for apps whose entire expected managed schema is absent and which have no
    applied dependent migrations outside the same repair set.
    """

    repair_literal = "True" if repair else "False"
    # Host-runtime schema ownership is explicit. EkoH and Smart Vote are legacy
    # host apps whose canonical tables live in ``ekoh_smartvote``; the rest of
    # the host Django apps live in ``public``.  World release schemas are
    # intentionally *not* considered here, otherwise a release-local table
    # could incorrectly satisfy a missing host-runtime table.
    schema_overrides = {"ekoh": "ekoh_smartvote", "smart_vote": "ekoh_smartvote"}
    schema_overrides_literal = repr(schema_overrides)
    return f"""import json
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.recorder import MigrationRecorder
repair = {repair_literal}
schema_overrides = {schema_overrides_literal}
executor = MigrationExecutor(connection)
state = executor.loader.project_state(executor.loader.graph.leaf_nodes())
expected_by_app = {{}}
for model in state.apps.get_models(include_auto_created=True):
    opts = model._meta
    if opts.managed and not opts.proxy and opts.db_table:
        expected_by_app.setdefault(opts.app_label, set()).add(opts.db_table)
# Django's connection.introspection.table_names() only observes relations visible
# through the connection search_path. Konnaxion intentionally keeps the default
# host search_path on public while EkoH/Smart Vote live in ekoh_smartvote, so the
# old probe produced false missing-table reports. Query pg_catalog by namespace
# and compare every app against its canonical host schema instead.
actual_by_schema = {{}}
with connection.cursor() as cursor:
    cursor.execute("SELECT n.nspname, c.relname FROM pg_catalog.pg_class AS c JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace WHERE c.relkind IN ('r', 'p', 'f') AND n.nspname <> 'information_schema' AND n.nspname NOT LIKE 'pg_%'")
    for schema_name, relation_name in cursor.fetchall():
        actual_by_schema.setdefault(schema_name, set()).add(relation_name)
schema_by_app = {{
    app: schema_overrides.get(app, "public")
    for app in expected_by_app
}}
recorder = MigrationRecorder(connection)
applied = set(recorder.applied_migrations())
applied_apps = {{app for app, name in applied}}
missing_by_app = {{}}
for app, tables in expected_by_app.items():
    if app not in applied_apps:
        continue
    schema_name = schema_by_app[app]
    actual = actual_by_schema.get(schema_name, set())
    missing = tables - actual
    if missing:
        missing_by_app[app] = sorted(missing)
candidates = set()
for app in missing_by_app:
    schema_name = schema_by_app[app]
    actual = actual_by_schema.get(schema_name, set())
    if not (expected_by_app.get(app, set()) & actual):
        candidates.add(app)
blocked = {{}}
for app in sorted(candidates):
    dependents = []
    for node in sorted(applied):
        if node[0] in candidates:
            continue
        migration = executor.loader.disk_migrations.get(node)
        if migration is None:
            continue
        if any(isinstance(dep, tuple) and dep and dep[0] == app for dep in migration.dependencies):
            dependents.append(f"{{node[0]}}.{{node[1]}}")
    if dependents:
        blocked[app] = {{"reason": "applied dependent migrations exist", "dependents": dependents}}
repairable = sorted(candidates - set(blocked))
repaired = []
if repair and repairable:
    recorder.Migration.objects.filter(app__in=repairable).delete()
    repaired = repairable
result = {{
    "ok": not bool(missing_by_app),
    "missing_tables": missing_by_app,
    "repair_requested": repair,
    "repairable_apps": repairable,
    "repaired_apps": repaired,
    "blocked_repairs": blocked,
    "schema_by_app": {{app: schema_by_app[app] for app in sorted(missing_by_app)}},
}}
print("{SCHEMA_INTEGRITY_PREFIX}" + json.dumps(result, sort_keys=True))"""


def _schema_integrity_error(data: Mapping[str, object]) -> str:
    missing = data.get("missing_tables") or {}
    blocked = data.get("blocked_repairs") or {}
    repairable = data.get("repairable_apps") or []
    return (
        "Database schema drift detected after migrations. "
        f"Applied migration state references missing managed tables: {missing!r}. "
        f"Repairable fresh-app sets: {repairable!r}. "
        f"Blocked repairs: {blocked!r}."
    )


@dataclass(frozen=True)
class MigrationPlan:
    """A migration plan for one Konnaxion instance."""

    instance_id: str
    compose_file: Path
    project_name: str
    service_name: str = DockerService.DJANGO_API.value
    environment: Mapping[str, str] = field(default_factory=dict)

    def migrate_command(self) -> MigrationCommand:
        return MigrationCommand(
            argv=(
                "docker",
                "compose",
                "-p",
                self.project_name,
                "-f",
                str(self.compose_file),
                "run",
                "--rm",
                self.service_name,
                "python",
                "manage.py",
                "migrate",
                "--noinput",
            )
        )

    def show_plan_command(self) -> MigrationCommand:
        return MigrationCommand(
            argv=(
                "docker",
                "compose",
                "-p",
                self.project_name,
                "-f",
                str(self.compose_file),
                "run",
                "--rm",
                self.service_name,
                "python",
                "manage.py",
                "showmigrations",
                "--plan",
            ),
            timeout_seconds=300,
        )

    def check_command(self) -> MigrationCommand:
        return MigrationCommand(
            argv=(
                "docker",
                "compose",
                "-p",
                self.project_name,
                "-f",
                str(self.compose_file),
                "run",
                "--rm",
                self.service_name,
                "python",
                "manage.py",
                "migrate",
                "--check",
            ),
            timeout_seconds=300,
        )

    def schema_integrity_command(self, *, repair: bool = False) -> MigrationCommand:
        return MigrationCommand(
            argv=(
                "docker",
                "compose",
                "-p",
                self.project_name,
                "-f",
                str(self.compose_file),
                "run",
                "--rm",
                self.service_name,
                "python",
                "manage.py",
                "shell",
                "-c",
                _schema_integrity_script(repair=repair),
            ),
            timeout_seconds=300,
        )


def build_migration_plan(
    instance_id: str,
    *,
    compose_file: str | Path | None = None,
    project_name: str | None = None,
    environment: Mapping[str, str] | None = None,
) -> MigrationPlan:
    """Build a canonical migration plan for an instance."""

    if not instance_id:
        raise MigrationError("instance_id is required to build a migration plan")

    resolved_compose_file = Path(compose_file) if compose_file else instance_compose_file(instance_id)
    resolved_project_name = project_name or project_name_for_instance(instance_id)

    return MigrationPlan(
        instance_id=instance_id,
        compose_file=resolved_compose_file,
        project_name=resolved_project_name,
        environment=dict(environment or {}),
    )


def project_name_for_instance(instance_id: str) -> str:
    """Return the same Compose project name used by ``DockerRuntime``."""

    try:
        return docker_project_name(instance_id)
    except ValueError as exc:
        raise MigrationError(str(exc)) from exc


def show_migration_plan(
    instance_id: str,
    *,
    compose_file: str | Path | None = None,
    project_name: str | None = None,
    environment: Mapping[str, str] | None = None,
) -> MigrationResult:
    """Return Django's migration plan for the instance."""

    plan = build_migration_plan(
        instance_id,
        compose_file=compose_file,
        project_name=project_name,
        environment=environment,
    )
    return run_migration_command(plan.show_plan_command(), environment=plan.environment)


def check_migrations_applied(
    instance_id: str,
    *,
    compose_file: str | Path | None = None,
    project_name: str | None = None,
    environment: Mapping[str, str] | None = None,
) -> MigrationResult:
    """Run ``python manage.py migrate --check`` for the instance."""

    plan = build_migration_plan(
        instance_id,
        compose_file=compose_file,
        project_name=project_name,
        environment=environment,
    )
    return run_migration_command(plan.check_command(), environment=plan.environment)


def inspect_schema_integrity(
    instance_id: str,
    *,
    compose_file: str | Path | None = None,
    project_name: str | None = None,
    environment: Mapping[str, str] | None = None,
    repair: bool = False,
) -> dict[str, object]:
    """Verify that applied Django migration state matches actual DB tables.

    ``repair=True`` is intentionally narrow: it only clears stale migration
    rows for apps whose entire expected managed schema is absent and which have
    no already-applied external migration depending on them. The caller must
    still rerun ``migrate`` and verify the schema afterwards.
    """

    plan = build_migration_plan(
        instance_id,
        compose_file=compose_file,
        project_name=project_name,
        environment=environment,
    )
    command = plan.schema_integrity_command(repair=repair)
    result = run_migration_command(command, environment=plan.environment)
    if not result.ok:
        raise MigrationError(
            "Schema integrity probe failed "
            f"(returncode={result.returncode}). stderr={result.stderr.strip()!r}"
        )

    payload_line = next(
        (
            line
            for line in reversed(result.stdout.splitlines())
            if line.startswith(SCHEMA_INTEGRITY_PREFIX)
        ),
        None,
    )
    if payload_line is None:
        raise MigrationError("Schema integrity probe did not return its structured result.")

    try:
        payload = json.loads(payload_line[len(SCHEMA_INTEGRITY_PREFIX) :])
    except json.JSONDecodeError as exc:
        raise MigrationError("Schema integrity probe returned invalid JSON.") from exc

    if not isinstance(payload, dict):
        raise MigrationError("Schema integrity probe returned an invalid payload type.")

    return payload


def run_django_migrations(
    instance_id: str,
    *,
    compose_file: str | Path | None = None,
    project_name: str | None = None,
    environment: Mapping[str, str] | None = None,
    raise_on_failure: bool = True,
    repair_fresh_schema_drift: bool = False,
) -> MigrationResult:
    """Run canonical Django migrations and verify the resulting schema.

    A successful ``migrate`` is not sufficient: Django can report every
    migration as applied while the underlying tables are missing. The Agent
    therefore compares the migration-derived managed table set with the actual
    database before declaring the lifecycle step successful.

    For a Manager-declared fresh deployment, ``repair_fresh_schema_drift`` may
    clear stale migration records only when an affected app has none of its
    expected managed tables and no external applied migration depends on it.
    The Agent then reruns migrations and verifies the schema again.
    """

    plan = build_migration_plan(
        instance_id,
        compose_file=compose_file,
        project_name=project_name,
        environment=environment,
    )
    validate_migration_plan(plan)

    reconcile_postgres_credentials(
        instance_id,
        compose_file=plan.compose_file,
        project_name=plan.project_name,
    )

    result = run_migration_command(plan.migrate_command(), environment=plan.environment)
    if not result.ok:
        if raise_on_failure:
            result.raise_for_failure()
        return result

    integrity = inspect_schema_integrity(
        instance_id,
        compose_file=plan.compose_file,
        project_name=plan.project_name,
        environment=plan.environment,
        repair=False,
    )
    if bool(integrity.get("ok")):
        return replace(result, schema_integrity=integrity)

    repair_summary: dict[str, object] = {}
    if repair_fresh_schema_drift and integrity.get("repairable_apps"):
        repair_summary = inspect_schema_integrity(
            instance_id,
            compose_file=plan.compose_file,
            project_name=plan.project_name,
            environment=plan.environment,
            repair=True,
        )
        repaired_apps = list(repair_summary.get("repaired_apps") or [])
        if repaired_apps:
            rerun = run_migration_command(plan.migrate_command(), environment=plan.environment)
            if not rerun.ok:
                failed = replace(
                    rerun,
                    schema_integrity={
                        "ok": False,
                        "repair_attempted": True,
                        "repaired_apps": repaired_apps,
                        "repair_result": repair_summary,
                    },
                )
                if raise_on_failure:
                    failed.raise_for_failure()
                return failed

            final_integrity = inspect_schema_integrity(
                instance_id,
                compose_file=plan.compose_file,
                project_name=plan.project_name,
                environment=plan.environment,
                repair=False,
            )
            final_integrity = dict(final_integrity)
            final_integrity["repair_attempted"] = True
            final_integrity["repaired_apps"] = repaired_apps
            if bool(final_integrity.get("ok")):
                return replace(rerun, schema_integrity=final_integrity)
            integrity = final_integrity

    failed_integrity = dict(integrity)
    if repair_summary:
        failed_integrity["repair_result"] = repair_summary
    error_text = _schema_integrity_error(failed_integrity)
    failed = MigrationResult(
        status=MigrationStatus.FAILED,
        command=result.command,
        returncode=3,
        stdout=result.stdout,
        stderr=error_text,
        started_at=result.started_at,
        finished_at=_utcnow(),
        schema_integrity=failed_integrity,
    )
    if raise_on_failure:
        failed.raise_for_failure()
    return failed


def validate_migration_plan(plan: MigrationPlan) -> None:
    """Validate a migration plan before command execution."""

    if plan.service_name != DockerService.DJANGO_API.value:
        raise MigrationError(
            f"Migration service must be {DockerService.DJANGO_API.value!r}; "
            f"got {plan.service_name!r}"
        )

    if not plan.compose_file:
        raise MigrationError("compose_file is required")

    if plan.compose_file.name not in {"docker-compose.runtime.yml", "docker-compose.capsule.yml"}:
        raise MigrationError(
            "compose_file must be a Konnaxion runtime/capsule compose file, "
            f"got {plan.compose_file.name!r}"
        )


def run_migration_command(
    command: MigrationCommand,
    *,
    environment: Mapping[str, str] | None = None,
) -> MigrationResult:
    """Run a prebuilt allowlisted migration command."""

    validate_command_allowlist(command.argv)

    started_at = _utcnow()

    try:
        completed = subprocess.run(
            command.as_list(),
            check=False,
            capture_output=True,
            text=True,
            timeout=command.timeout_seconds,
            env=_merged_environment(environment),
        )
    except subprocess.TimeoutExpired as exc:
        finished_at = _utcnow()
        return MigrationResult(
            status=MigrationStatus.TIMED_OUT,
            command=command.argv,
            returncode=None,
            stdout=exc.stdout or "",
            stderr=exc.stderr or "",
            started_at=started_at,
            finished_at=finished_at,
            timed_out=True,
        )

    finished_at = _utcnow()
    status = (
        MigrationStatus.SUCCEEDED
        if completed.returncode == 0
        else MigrationStatus.FAILED
    )

    return MigrationResult(
        status=status,
        command=command.argv,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        started_at=started_at,
        finished_at=finished_at,
    )


def validate_command_allowlist(argv: Sequence[str]) -> None:
    """Reject anything outside the migration command allowlist."""

    if not argv:
        raise MigrationError("empty command is not allowed")

    forbidden_tokens = {";", "&&", "||", "|", "`", "$(", "makemigrations"}
    for token in argv:
        if token in forbidden_tokens or any(marker in token for marker in (";", "`", "$(")):
            raise MigrationError(f"forbidden command token: {token!r}")

    required_prefix = ("docker", "compose")
    if tuple(argv[:2]) != required_prefix:
        raise MigrationError("migration commands must start with: docker compose")

    if "run" not in argv:
        raise MigrationError("migration command must use docker compose run")

    if "--rm" not in argv:
        raise MigrationError("migration command must use --rm")

    if DockerService.DJANGO_API.value not in argv:
        raise MigrationError(
            f"migration command must target {DockerService.DJANGO_API.value!r}"
        )

    expected_manage = ("python", "manage.py")
    if not _contains_subsequence(tuple(argv), expected_manage):
        raise MigrationError("migration command must run python manage.py")

    if "shell" in argv:
        shell_index = argv.index("shell")
        expected_tail = tuple(argv[shell_index:])
        allowed_tails = {
            ("shell", "-c", _schema_integrity_script(repair=False)),
            ("shell", "-c", _schema_integrity_script(repair=True)),
        }
        if expected_tail not in allowed_tails:
            raise MigrationError(
                "only the fixed schema integrity probe is allowed via manage.py shell"
            )
        return

    if "migrate" not in argv and "showmigrations" not in argv:
        raise MigrationError("only migrate/showmigrations commands are allowed")

    if "migrate" in argv and "makemigrations" in argv:
        raise MigrationError("makemigrations is not allowed at runtime")


def redact_command(argv: Sequence[str]) -> str:
    """Return a printable command string without exposing secrets."""

    redacted: list[str] = []
    secret_markers = ("PASSWORD", "SECRET", "TOKEN", "KEY", "DATABASE_URL")

    skip_next = False
    for token in argv:
        if skip_next:
            redacted.append("<redacted>")
            skip_next = False
            continue

        upper = token.upper()
        if any(marker in upper for marker in secret_markers):
            if "=" in token:
                name, _value = token.split("=", 1)
                redacted.append(f"{name}=<redacted>")
            else:
                redacted.append("<redacted>")
            continue

        if token in {"-e", "--env"}:
            redacted.append(token)
            skip_next = True
            continue

        redacted.append(token)

    return " ".join(redacted)


def _contains_subsequence(values: tuple[str, ...], expected: tuple[str, ...]) -> bool:
    if not expected:
        return True

    window_size = len(expected)
    for index in range(0, len(values) - window_size + 1):
        if values[index : index + window_size] == expected:
            return True
    return False


def _merged_environment(environment: Mapping[str, str] | None) -> dict[str, str] | None:
    if environment is None:
        return None

    import os

    merged = dict(os.environ)
    merged.update({str(key): str(value) for key, value in environment.items()})
    return merged


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)
