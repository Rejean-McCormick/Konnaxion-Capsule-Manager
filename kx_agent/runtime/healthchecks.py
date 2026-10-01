"""
Runtime healthchecks for Konnaxion Instances.

This module validates an already-created Docker Compose runtime before the
Manager reports an instance as usable.

Scope:
- verify the canonical Compose file exists
- verify canonical runtime services are present
- verify core services are running/healthy
- verify generated runtime contracts for public host/routing/healthchecks
- verify Traefik routes answer through the configured host
- provide structured reports for Manager/API/UI consumption

This module does not:
- open ports
- mutate firewall state
- start arbitrary containers
- run arbitrary shell commands
- bypass the Security Gate
"""

from __future__ import annotations

import json
import logging
import ssl
import subprocess
import time
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from kx_shared.konnaxion_constants import (
    DockerService,
    KX_ENV_DEFAULTS,
    ROUTES,
    instance_compose_file,
)


LOGGER = logging.getLogger(__name__)


class HealthcheckError(RuntimeError):
    """Raised when healthcheck execution cannot continue."""


class HealthStatus(StrEnum):
    """Health status values local to runtime health reports."""

    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    SKIPPED = "SKIPPED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class HealthcheckResult:
    """One healthcheck result."""

    name: str
    status: HealthStatus
    message: str
    details: Mapping[str, Any] = field(default_factory=dict)
    duration_ms: int = 0

    @property
    def ok(self) -> bool:
        return self.status in {HealthStatus.PASS, HealthStatus.SKIPPED}

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data


@dataclass(frozen=True)
class HealthReport:
    """Aggregated healthcheck report for one Konnaxion Instance."""

    instance_id: str
    status: HealthStatus
    checks: tuple[HealthcheckResult, ...]
    generated_at_epoch: float

    @property
    def ok(self) -> bool:
        return self.status == HealthStatus.PASS

    @property
    def failure_count(self) -> int:
        return sum(1 for check in self.checks if check.status == HealthStatus.FAIL)

    @property
    def warning_count(self) -> int:
        return sum(1 for check in self.checks if check.status == HealthStatus.WARN)

    def to_dict(self) -> dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "status": self.status.value,
            "ok": self.ok,
            "failure_count": self.failure_count,
            "warning_count": self.warning_count,
            "generated_at_epoch": self.generated_at_epoch,
            "checks": [check.to_dict() for check in self.checks],
        }

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True)


@dataclass(frozen=True)
class RuntimeHealthcheckConfig:
    """Configuration for one healthcheck run."""

    instance_id: str
    compose_file: Path
    host: str
    scheme: str = "https"
    timeout_seconds: int = 8
    retries: int = 1
    require_flower: bool = False
    docker_bin: str = "docker"
    verify_tls: bool = False

    @classmethod
    def for_instance(
        cls,
        instance_id: str,
        *,
        compose_file: Path | None = None,
        host: str | None = None,
        scheme: str = "https",
        timeout_seconds: int = 8,
        retries: int = 1,
        require_flower: bool = False,
        docker_bin: str = "docker",
        verify_tls: bool = False,
    ) -> "RuntimeHealthcheckConfig":
        return cls(
            instance_id=instance_id,
            compose_file=compose_file or instance_compose_file(instance_id),
            host=host or KX_ENV_DEFAULTS.get("KX_HOST", ""),
            scheme=scheme,
            timeout_seconds=timeout_seconds,
            retries=retries,
            require_flower=require_flower,
            docker_bin=docker_bin,
            verify_tls=verify_tls,
        )


@dataclass(frozen=True)
class HttpRouteCheckSpec:
    """One HTTP route check specification."""

    route_path: str
    expected_service: str
    allow_application_client_error: bool = False


CORE_REQUIRED_SERVICES = (
    DockerService.TRAEFIK.value,
    DockerService.FRONTEND_NEXT.value,
    DockerService.DJANGO_API.value,
    DockerService.POSTGRES.value,
    DockerService.REDIS.value,
    DockerService.CELERYWORKER.value,
    DockerService.CELERYBEAT.value,
    DockerService.MEDIA_NGINX.value,
)

OPTIONAL_SERVICES = (
    DockerService.FLOWER.value,
)

# Some stock images do not reliably expose an in-container healthcheck tool.
# Route-level checks still verify reachability when the service matters.
WARN_ONLY_UNHEALTHY_SERVICES = {
    DockerService.MEDIA_NGINX.value,
}

HTTP_ROUTE_CHECKS = (
    HttpRouteCheckSpec(
        "/",
        DockerService.FRONTEND_NEXT.value,
        allow_application_client_error=False,
    ),
    HttpRouteCheckSpec(
        "/api/",
        DockerService.DJANGO_API.value,
        allow_application_client_error=True,
    ),
    # These application-level probes must return a successful response. A
    # listening Uvicorn socket is not enough to declare Konnaxion ready when
    # the database schema is inconsistent.
    HttpRouteCheckSpec(
        "/api/control/universes/",
        DockerService.DJANGO_API.value,
        allow_application_client_error=False,
    ),
    HttpRouteCheckSpec(
        "/api/control/worlds/",
        DockerService.DJANGO_API.value,
        allow_application_client_error=False,
    ),
    HttpRouteCheckSpec(
        "/admin/",
        DockerService.DJANGO_API.value,
        allow_application_client_error=True,
    ),
    HttpRouteCheckSpec(
        "/media/",
        DockerService.MEDIA_NGINX.value,
        allow_application_client_error=True,
    ),
)

CANONICAL_DJANGO_SOCKET_HEALTHCHECK = (
    'python -c "import socket; '
    "sock=socket.create_connection(('127.0.0.1',5000),5); "
    'sock.close()"'
)

MALFORMED_HEALTHCHECK_FRAGMENTS = (
    '"api/health/',
    "'api/health/",
    "s.close()\"api/health",
    "s.close()'api/health",
)

FRAGILE_HEALTHCHECK_FRAGMENTS = (
    "wget -qO- http://127.0.0.1:5000",
    "wget --spider -q http://127.0.0.1:5000",
)


def run_healthchecks(
    instance_id: str,
    *,
    compose_file: Path | None = None,
    host: str | None = None,
    scheme: str = "https",
    timeout_seconds: int = 8,
    retries: int = 1,
    require_flower: bool = False,
    docker_bin: str = "docker",
    verify_tls: bool = False,
) -> HealthReport:
    """Run the standard runtime healthcheck suite."""

    config = RuntimeHealthcheckConfig.for_instance(
        instance_id,
        compose_file=compose_file,
        host=host,
        scheme=scheme,
        timeout_seconds=timeout_seconds,
        retries=retries,
        require_flower=require_flower,
        docker_bin=docker_bin,
        verify_tls=verify_tls,
    )

    checks: list[HealthcheckResult] = []

    checks.append(check_compose_file(config))

    if checks[-1].status == HealthStatus.FAIL:
        return build_report(config.instance_id, checks)

    checks.append(check_compose_runtime_contract(config))

    services = list_compose_services(config)
    checks.append(check_required_services(services, require_flower=require_flower))

    checks.extend(check_service_runtime(config, services))

    if config.host:
        checks.extend(check_http_routes(config))
    else:
        checks.append(
            HealthcheckResult(
                name="http_routes",
                status=HealthStatus.SKIPPED,
                message="KX_HOST is not configured; HTTP route checks skipped.",
            )
        )

    checks.append(check_route_contract())

    return build_report(config.instance_id, checks)


def wait_until_healthy(
    instance_id: str,
    *,
    compose_file: Path | None = None,
    host: str | None = None,
    scheme: str = "https",
    timeout_seconds: int = 120,
    interval_seconds: int = 5,
    docker_bin: str = "docker",
    verify_tls: bool = False,
) -> HealthReport:
    """
    Poll runtime healthchecks until PASS or timeout.

    Returns the final report. It does not raise for an unhealthy runtime.
    """

    deadline = time.monotonic() + timeout_seconds
    latest_report: HealthReport | None = None

    while time.monotonic() < deadline:
        latest_report = run_healthchecks(
            instance_id,
            compose_file=compose_file,
            host=host,
            scheme=scheme,
            timeout_seconds=min(interval_seconds, 10),
            retries=1,
            docker_bin=docker_bin,
            verify_tls=verify_tls,
        )

        if latest_report.ok:
            return latest_report

        time.sleep(interval_seconds)

    if latest_report is not None:
        return latest_report

    return build_report(
        instance_id,
        [
            HealthcheckResult(
                name="wait_until_healthy",
                status=HealthStatus.FAIL,
                message="Healthcheck polling timed out before any report was generated.",
            )
        ],
    )


def check_compose_file(config: RuntimeHealthcheckConfig) -> HealthcheckResult:
    """Verify the rendered runtime Compose file exists."""

    started = time.monotonic()

    if not config.compose_file.exists():
        return timed_result(
            started,
            name="compose_file",
            status=HealthStatus.FAIL,
            message=f"Runtime Compose file does not exist: {config.compose_file}",
        )

    if not config.compose_file.is_file():
        return timed_result(
            started,
            name="compose_file",
            status=HealthStatus.FAIL,
            message=f"Runtime Compose path is not a file: {config.compose_file}",
        )

    return timed_result(
        started,
        name="compose_file",
        status=HealthStatus.PASS,
        message="Runtime Compose file exists.",
        details={"compose_file": str(config.compose_file)},
    )


def check_compose_runtime_contract(
    config: RuntimeHealthcheckConfig,
) -> HealthcheckResult:
    """Verify generated runtime Compose content follows deployment contracts."""

    started = time.monotonic()

    try:
        text = config.compose_file.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return timed_result(
            started,
            name="compose_runtime_contract",
            status=HealthStatus.FAIL,
            message="Could not read runtime Compose file.",
            details={"compose_file": str(config.compose_file), "error": str(exc)},
        )

    issues: list[str] = []
    warnings: list[str] = []
    host = config.host.strip()

    if host and not _is_loopback_host(host):
        if "Host(`127.0.0.1`)" in text:
            issues.append(
                "Public runtime still contains Traefik Host(`127.0.0.1`) rules."
            )

        if "KX_HOST=127.0.0.1" in text:
            issues.append("Public runtime still contains KX_HOST=127.0.0.1.")

    for fragment in MALFORMED_HEALTHCHECK_FRAGMENTS:
        if fragment in text:
            issues.append(
                "Runtime Compose contains a malformed healthcheck fragment: "
                + fragment
            )

    for fragment in FRAGILE_HEALTHCHECK_FRAGMENTS:
        if fragment in text:
            warnings.append(
                "Runtime Compose contains a fragile wget-based healthcheck: "
                + fragment
            )

    if "providers.file.filename" in text and "traefik-dynamic.yml" not in text:
        warnings.append(
            "Traefik file provider appears enabled but traefik-dynamic.yml "
            "is not referenced by name."
        )

    if issues:
        return timed_result(
            started,
            name="compose_runtime_contract",
            status=HealthStatus.FAIL,
            message="Runtime Compose violates one or more required contracts.",
            details={"issues": issues, "warnings": warnings},
        )

    if warnings:
        return timed_result(
            started,
            name="compose_runtime_contract",
            status=HealthStatus.WARN,
            message="Runtime Compose has non-blocking contract warnings.",
            details={"warnings": warnings},
        )

    return timed_result(
        started,
        name="compose_runtime_contract",
        status=HealthStatus.PASS,
        message="Runtime Compose contract checks passed.",
        details={
            "compose_file": str(config.compose_file),
            "host": host,
        },
    )


def check_required_services(
    services: Iterable[str],
    *,
    require_flower: bool = False,
) -> HealthcheckResult:
    """Verify canonical services are present in the Compose project."""

    started = time.monotonic()
    service_set = set(services)
    required = set(CORE_REQUIRED_SERVICES)

    if require_flower:
        required.add(DockerService.FLOWER.value)

    missing = tuple(sorted(required - service_set))
    extra = tuple(sorted(service_set - required - set(OPTIONAL_SERVICES)))

    if missing:
        return timed_result(
            started,
            name="required_services",
            status=HealthStatus.FAIL,
            message="One or more required canonical services are missing.",
            details={"missing": missing, "extra": extra, "services": sorted(service_set)},
        )

    if extra:
        return timed_result(
            started,
            name="required_services",
            status=HealthStatus.WARN,
            message="Unexpected non-canonical services are present.",
            details={"extra": extra, "services": sorted(service_set)},
        )

    return timed_result(
        started,
        name="required_services",
        status=HealthStatus.PASS,
        message="All required canonical services are present.",
        details={"services": sorted(service_set)},
    )


def check_service_runtime(
    config: RuntimeHealthcheckConfig,
    services: Iterable[str],
) -> tuple[HealthcheckResult, ...]:
    """Check runtime state for each service."""

    service_set = set(services)
    results: list[HealthcheckResult] = []

    for service_name in CORE_REQUIRED_SERVICES:
        if service_name not in service_set:
            results.append(
                HealthcheckResult(
                    name=f"service:{service_name}",
                    status=HealthStatus.FAIL,
                    message="Required service is missing from Compose project.",
                )
            )
            continue

        results.append(check_service(config, service_name))

    if DockerService.FLOWER.value in service_set:
        results.append(check_service(config, DockerService.FLOWER.value))

    return tuple(results)


def check_service(
    config: RuntimeHealthcheckConfig,
    service_name: str,
) -> HealthcheckResult:
    """Check one Compose service using Docker Compose state."""

    started = time.monotonic()

    completed = run_command(
        compose_command(
            config,
            "ps",
            "--format",
            "json",
            service_name,
        ),
        timeout_seconds=config.timeout_seconds,
    )

    if completed.returncode != 0:
        return timed_result(
            started,
            name=f"service:{service_name}",
            status=HealthStatus.FAIL,
            message="Could not read service state.",
            details={
                "service": service_name,
                "stderr": completed.stderr.strip(),
                "stdout": completed.stdout.strip(),
            },
        )

    records = parse_compose_ps_json(completed.stdout)

    if not records:
        return timed_result(
            started,
            name=f"service:{service_name}",
            status=HealthStatus.FAIL,
            message="No container record found for service.",
            details={"service": service_name},
        )

    unhealthy_records = []
    non_running_records = []

    for record in records:
        state = str(record.get("State", "")).lower()
        health = str(record.get("Health", "")).lower()

        if state and state != "running":
            non_running_records.append(record)

        if health and health not in {"healthy", "starting"}:
            unhealthy_records.append(record)

    if non_running_records:
        return timed_result(
            started,
            name=f"service:{service_name}",
            status=HealthStatus.FAIL,
            message="Service has non-running containers.",
            details={"service": service_name, "records": non_running_records},
        )

    if unhealthy_records:
        if service_name in WARN_ONLY_UNHEALTHY_SERVICES:
            return timed_result(
                started,
                name=f"service:{service_name}",
                status=HealthStatus.WARN,
                message=(
                    "Service has unhealthy containers, but this service is "
                    "validated by route checks because its image may not expose "
                    "a reliable in-container healthcheck tool."
                ),
                details={"service": service_name, "records": unhealthy_records},
            )

        return timed_result(
            started,
            name=f"service:{service_name}",
            status=HealthStatus.FAIL,
            message="Service has unhealthy containers.",
            details={"service": service_name, "records": unhealthy_records},
        )

    starting = [
        record
        for record in records
        if str(record.get("Health", "")).lower() == "starting"
    ]

    if starting:
        return timed_result(
            started,
            name=f"service:{service_name}",
            status=HealthStatus.WARN,
            message="Service is running but healthcheck is still starting.",
            details={"service": service_name, "records": records},
        )

    return timed_result(
        started,
        name=f"service:{service_name}",
        status=HealthStatus.PASS,
        message="Service is running.",
        details={"service": service_name, "records": records},
    )


def check_http_routes(
    config: RuntimeHealthcheckConfig,
) -> tuple[HealthcheckResult, ...]:
    """Check canonical Traefik HTTP routes through the configured host."""

    results: list[HealthcheckResult] = []

    for spec in HTTP_ROUTE_CHECKS:
        results.append(
            check_http_route(
                config,
                spec.route_path,
                spec.expected_service,
                allow_application_client_error=spec.allow_application_client_error,
            )
        )

    return tuple(results)


def check_http_route(
    config: RuntimeHealthcheckConfig,
    route_path: str,
    expected_service: str,
    *,
    allow_application_client_error: bool = False,
) -> HealthcheckResult:
    """Check one canonical HTTP route."""

    started = time.monotonic()
    url = build_url(config.scheme, config.host, route_path)

    last_error = ""
    status_code: int | None = None
    response_headers: dict[str, str] = {}
    body_sample = ""

    for attempt in range(config.retries + 1):
        try:
            request = Request(
                url,
                headers={
                    "User-Agent": "kx-agent-healthcheck/1",
                    "Accept": "text/html,application/json,*/*",
                },
                method="GET",
            )

            context = _ssl_context_for_config(config)

            with urlopen(
                request,
                timeout=config.timeout_seconds,
                context=context,
            ) as response:
                status_code = response.getcode()
                response_headers = normalize_headers(response.headers)

            return classify_http_route_response(
                started,
                route_path=route_path,
                expected_service=expected_service,
                url=url,
                status_code=status_code,
                headers=response_headers,
                body_sample=body_sample,
                allow_application_client_error=allow_application_client_error,
            )

        except HTTPError as exc:
            status_code = exc.code
            response_headers = normalize_headers(exc.headers)

            try:
                body_sample = exc.read(512).decode("utf-8", errors="replace")
            except Exception:
                body_sample = ""

            return classify_http_route_response(
                started,
                route_path=route_path,
                expected_service=expected_service,
                url=url,
                status_code=status_code,
                headers=response_headers,
                body_sample=body_sample,
                allow_application_client_error=allow_application_client_error,
            )

        except URLError as exc:
            last_error = str(exc.reason)

        except TimeoutError:
            last_error = "request timed out"

        if attempt < config.retries:
            time.sleep(1)

    return timed_result(
        started,
        name=f"http:{route_path}",
        status=HealthStatus.FAIL,
        message="HTTP route did not respond successfully.",
        details={
            "url": url,
            "status_code": status_code,
            "expected_service": expected_service,
            "error": last_error,
            "headers": response_headers,
            "body_sample": body_sample,
        },
    )


def classify_http_route_response(
    started: float,
    *,
    route_path: str,
    expected_service: str,
    url: str,
    status_code: int,
    headers: Mapping[str, str],
    body_sample: str,
    allow_application_client_error: bool,
) -> HealthcheckResult:
    """Classify one HTTP route response."""

    details = {
        "url": url,
        "status_code": status_code,
        "expected_service": expected_service,
        "headers": dict(headers),
        "body_sample": body_sample[:256],
    }

    if 200 <= status_code < 400:
        return timed_result(
            started,
            name=f"http:{route_path}",
            status=HealthStatus.PASS,
            message="HTTP route responded successfully.",
            details=details,
        )

    if is_traefik_default_404(
        status_code=status_code,
        headers=headers,
        body_sample=body_sample,
    ):
        return timed_result(
            started,
            name=f"http:{route_path}",
            status=HealthStatus.FAIL,
            message=(
                "HTTP route returned Traefik's default 404; router host/path "
                "did not match the configured runtime."
            ),
            details=details,
        )

    if 400 <= status_code < 500:
        if allow_application_client_error:
            return timed_result(
                started,
                name=f"http:{route_path}",
                status=HealthStatus.PASS,
                message=(
                    "HTTP route reached an application service and returned a "
                    "client response. This is acceptable for canonical API/admin/"
                    "media route probes that may not map to a concrete resource."
                ),
                details=details,
            )

        return timed_result(
            started,
            name=f"http:{route_path}",
            status=HealthStatus.FAIL,
            message="HTTP route returned a client error for a route that must serve.",
            details=details,
        )

    if 500 <= status_code:
        return timed_result(
            started,
            name=f"http:{route_path}",
            status=HealthStatus.FAIL,
            message="HTTP route reached a server error.",
            details=details,
        )

    return timed_result(
        started,
        name=f"http:{route_path}",
        status=HealthStatus.WARN,
        message="HTTP route returned an unusual status code.",
        details=details,
    )


def is_traefik_default_404(
    *,
    status_code: int,
    headers: Mapping[str, str],
    body_sample: str,
) -> bool:
    """Return True for Traefik's own unmatched-route 404 response."""

    if status_code != 404:
        return False

    normalized = normalize_headers(headers)
    content_type = normalized.get("content-type", "").lower()
    server = normalized.get("server", "").lower()
    body = body_sample.strip().lower()

    if "uvicorn" in server:
        return False

    if "next.js" in normalized.get("x-powered-by", "").lower():
        return False

    django_header_names = {
        "content-language",
        "cross-origin-opener-policy",
        "referrer-policy",
    }
    if any(name in normalized for name in django_header_names):
        return False

    return (
        "text/plain" in content_type
        and body in {"404 page not found", "404 not found", ""}
    )


def check_route_contract() -> HealthcheckResult:
    """Verify the canonical route table remains aligned with the docs."""

    started = time.monotonic()
    expected = {
        "/": DockerService.FRONTEND_NEXT.value,
        "/api/": DockerService.DJANGO_API.value,
        "/admin/": DockerService.DJANGO_API.value,
        "/media/": DockerService.MEDIA_NGINX.value,
    }

    actual = {path: service for path, service in ROUTES.items()}

    if actual != expected:
        return timed_result(
            started,
            name="route_contract",
            status=HealthStatus.FAIL,
            message="Canonical route mapping is not aligned.",
            details={"expected": expected, "actual": actual},
        )

    return timed_result(
        started,
        name="route_contract",
        status=HealthStatus.PASS,
        message="Canonical route mapping is aligned.",
        details={"routes": actual},
    )


def list_compose_services(config: RuntimeHealthcheckConfig) -> tuple[str, ...]:
    """Return services declared by the runtime Compose project."""

    completed = run_command(
        compose_command(config, "config", "--services"),
        timeout_seconds=config.timeout_seconds,
    )

    if completed.returncode != 0:
        raise HealthcheckError(
            "Could not list Compose services: "
            + (completed.stderr.strip() or completed.stdout.strip())
        )

    return tuple(
        line.strip()
        for line in completed.stdout.splitlines()
        if line.strip()
    )


def compose_command(config: RuntimeHealthcheckConfig, *args: str) -> list[str]:
    """Build a safe Docker Compose command."""

    return [
        config.docker_bin,
        "compose",
        "-f",
        str(config.compose_file),
        "-p",
        f"konnaxion-{config.instance_id}",
        *args,
    ]


def run_command(
    command: list[str],
    *,
    timeout_seconds: int,
) -> subprocess.CompletedProcess[str]:
    """Run an allowlisted subprocess command without shell expansion."""

    if not command:
        raise HealthcheckError("Refusing to run an empty command.")

    if command[0] != "docker":
        raise HealthcheckError(f"Refusing to run unsupported binary: {command[0]}")

    LOGGER.debug("Running healthcheck command: %s", command)

    try:
        return subprocess.run(
            command,
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(
            args=command,
            returncode=124,
            stdout=exc.stdout or "",
            stderr=exc.stderr or "command timed out",
        )
    except OSError as exc:
        return subprocess.CompletedProcess(
            args=command,
            returncode=127,
            stdout="",
            stderr=str(exc),
        )


def parse_compose_ps_json(stdout: str) -> tuple[dict[str, Any], ...]:
    """
    Parse `docker compose ps --format json` output.

    Docker Compose versions may emit either:
    - one JSON array
    - one JSON object per line
    """

    raw = stdout.strip()

    if not raw:
        return ()

    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return tuple(item for item in parsed if isinstance(item, dict))
        if isinstance(parsed, dict):
            return (parsed,)
    except json.JSONDecodeError:
        pass

    records: list[dict[str, Any]] = []

    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue

        try:
            parsed_line = json.loads(line)
        except json.JSONDecodeError:
            continue

        if isinstance(parsed_line, dict):
            records.append(parsed_line)

    return tuple(records)


def build_report(
    instance_id: str,
    checks: Iterable[HealthcheckResult],
) -> HealthReport:
    """Build an aggregate report from individual results."""

    check_tuple = tuple(checks)
    status = aggregate_status(check_tuple)

    return HealthReport(
        instance_id=instance_id,
        status=status,
        checks=check_tuple,
        generated_at_epoch=time.time(),
    )


def aggregate_status(checks: Iterable[HealthcheckResult]) -> HealthStatus:
    """Aggregate individual check statuses."""

    statuses = [check.status for check in checks]

    if not statuses:
        return HealthStatus.UNKNOWN

    if HealthStatus.FAIL in statuses:
        return HealthStatus.FAIL

    if HealthStatus.UNKNOWN in statuses:
        return HealthStatus.UNKNOWN

    if HealthStatus.WARN in statuses:
        return HealthStatus.WARN

    return HealthStatus.PASS


def build_url(scheme: str, host: str, route_path: str) -> str:
    """Build a healthcheck URL."""

    safe_scheme = scheme.strip().lower() or "https"
    safe_host = host.strip().rstrip("/")
    safe_path = route_path if route_path.startswith("/") else f"/{route_path}"

    return f"{safe_scheme}://{safe_host}{safe_path}"


def timed_result(
    started: float,
    *,
    name: str,
    status: HealthStatus,
    message: str,
    details: Mapping[str, Any] | None = None,
) -> HealthcheckResult:
    """Create a result with elapsed milliseconds."""

    return HealthcheckResult(
        name=name,
        status=status,
        message=message,
        details=details or {},
        duration_ms=int((time.monotonic() - started) * 1000),
    )


def normalize_headers(headers: Mapping[str, Any]) -> dict[str, str]:
    """Return case-normalized HTTP headers."""

    normalized: dict[str, str] = {}

    for key, value in headers.items():
        normalized[str(key).lower()] = str(value)

    return normalized


def _ssl_context_for_config(config: RuntimeHealthcheckConfig) -> ssl.SSLContext | None:
    """Return TLS context for HTTP probes."""

    if config.scheme.strip().lower() != "https":
        return None

    if config.verify_tls:
        return None

    return ssl._create_unverified_context()


def _is_loopback_host(host: str) -> bool:
    """Return True when host points to loopback/local-only access."""

    value = host.strip().lower()

    if not value:
        return False

    if value.startswith(("http://", "https://")):
        value = value.split("://", 1)[1]

    value = value.split("/", 1)[0].split(":", 1)[0]

    return value in {
        "127.0.0.1",
        "localhost",
        "::1",
        "0.0.0.0",
    }

