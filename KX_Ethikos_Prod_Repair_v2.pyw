from __future__ import annotations

import hashlib
import json
import os
import queue
import shlex
import shutil
import subprocess
import sys
import threading
import traceback
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, simpledialog, ttk

APP_TITLE = "Konnaxion Ethikos Production Repair"
REPAIR_SCHEMA = "kx-ethikos-prod-repair/v1"
INSPECTION_SCHEMA = "kx-ethikos-prod-inspection/v1"


MIGRATION_REMOTE_SCRIPT = r'''set -eu
INSTANCE_ID="$1"
KX_ROOT="$2"
PROJECT_NAME="konnaxion-${INSTANCE_ID}"
PYTHON_BIN="${KX_ROOT}/manager/.venv/bin/python"

if [ ! -x "${PYTHON_BIN}" ]; then
  echo "KX_REPAIR_ERROR Agent Python missing: ${PYTHON_BIN}"
  exit 41
fi

if ! sudo -n true >/dev/null 2>&1; then
  echo "KX_REPAIR_ERROR noninteractive sudo is required for the canonical Docker migration runner"
  exit 44
fi
sudo -n env KX_ROOT="${KX_ROOT}" "${PYTHON_BIN}" - "${INSTANCE_ID}" "${PROJECT_NAME}" <<'KX_PY'
import dataclasses
import json
import sys
from kx_agent.runtime.migrations import (
    check_migrations_applied,
    run_django_migrations,
    show_migration_plan,
)
from kx_shared.konnaxion_constants import instance_compose_file

instance_id = sys.argv[1]
project_name = sys.argv[2]
compose_file = instance_compose_file(instance_id)


def render(result):
    if dataclasses.is_dataclass(result):
        value = dataclasses.asdict(result)
    else:
        value = result
    return json.loads(json.dumps(value, default=str))

payload = {
    "instance_id": instance_id,
    "project_name": project_name,
    "compose_file": str(compose_file),
}

try:
    plan_before = show_migration_plan(
        instance_id,
        compose_file=compose_file,
        project_name=project_name,
    )
    check_before = check_migrations_applied(
        instance_id,
        compose_file=compose_file,
        project_name=project_name,
    )
    payload["plan_before"] = render(plan_before)
    payload["check_before"] = render(check_before)

    if check_before.ok:
        payload["changed"] = False
        payload["migration"] = None
    else:
        migration = run_django_migrations(
            instance_id,
            compose_file=compose_file,
            project_name=project_name,
            raise_on_failure=False,
        )
        payload["migration"] = render(migration)
        payload["changed"] = bool(migration.ok)
        if not migration.ok:
            payload["ok"] = False
            print("KX_REPAIR_JSON_BEGIN")
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            print("KX_REPAIR_JSON_END")
            sys.exit(42)

    check_after = check_migrations_applied(
        instance_id,
        compose_file=compose_file,
        project_name=project_name,
    )
    payload["check_after"] = render(check_after)
    payload["ok"] = bool(check_after.ok)
except Exception as exc:
    payload["ok"] = False
    payload["error"] = {"type": type(exc).__name__, "message": str(exc)}

print("KX_REPAIR_JSON_BEGIN")
print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
print("KX_REPAIR_JSON_END")
if not payload.get("ok"):
    sys.exit(43)
KX_PY
'''


MIGRATION_VALIDATE_SCRIPT = r'''set -eu
INSTANCE_ID="$1"
KX_ROOT="$2"
PROJECT_NAME="konnaxion-${INSTANCE_ID}"
PYTHON_BIN="${KX_ROOT}/manager/.venv/bin/python"
if ! sudo -n true >/dev/null 2>&1; then
  echo "KX_VALIDATE_ERROR noninteractive sudo is required for the canonical Docker migration runner"
  exit 44
fi
sudo -n env KX_ROOT="${KX_ROOT}" "${PYTHON_BIN}" - "${INSTANCE_ID}" "${PROJECT_NAME}" <<'KX_PY'
import dataclasses
import json
import sys
from kx_agent.runtime.migrations import check_migrations_applied, show_migration_plan
from kx_shared.konnaxion_constants import instance_compose_file

instance_id = sys.argv[1]
project_name = sys.argv[2]
compose_file = instance_compose_file(instance_id)

def render(result):
    return json.loads(json.dumps(dataclasses.asdict(result), default=str))

payload = {"instance_id": instance_id, "project_name": project_name, "compose_file": str(compose_file)}
try:
    payload["plan"] = render(show_migration_plan(instance_id, compose_file=compose_file, project_name=project_name))
    payload["check"] = render(check_migrations_applied(instance_id, compose_file=compose_file, project_name=project_name))
    payload["ok"] = True
except Exception as exc:
    payload["ok"] = False
    payload["error"] = {"type": type(exc).__name__, "message": str(exc)}
print("KX_VALIDATE_JSON_BEGIN")
print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
print("KX_VALIDATE_JSON_END")
KX_PY
'''


SCENARIO_REMOTE_TEMPLATE = r'''set -eu
INSTANCE_ID="$1"
KX_ROOT="$2"
REMOTE_JSON="$3"
MODE="$4"
PROJECT_NAME="konnaxion-${INSTANCE_ID}"
COMPOSE_FILE="${KX_ROOT}/instances/${INSTANCE_ID}/state/docker-compose.runtime.yml"
if ! sudo -n true >/dev/null 2>&1; then
  echo "KX_SCENARIO_ERROR noninteractive sudo is required for Docker access"
  exit 54
fi
DJANGO_CID="$(sudo -n docker compose -p "${PROJECT_NAME}" -f "${COMPOSE_FILE}" ps -q django-api 2>/dev/null || true)"
if [ -z "${DJANGO_CID}" ]; then
  echo "KX_SCENARIO_ERROR django-api is not running"
  exit 51
fi
CONTAINER_JSON="/tmp/kx-ethikos-scenario.json"
sudo -n docker cp "${REMOTE_JSON}" "${DJANGO_CID}:${CONTAINER_JSON}"
sudo -n docker exec -i "${DJANGO_CID}" sh -lc 'python manage.py shell' <<'KX_DJANGO_PY'
import json
from pathlib import Path
from konnaxion.ethikos.demo_import.importer import (
    import_ethikos_demo_scenario,
    validate_and_preview_ethikos_demo_scenario,
)

payload = json.loads(Path("/tmp/kx-ethikos-scenario.json").read_text(encoding="utf-8"))
mode = "__MODE__"
if mode == "preview":
    result = validate_and_preview_ethikos_demo_scenario(payload)
else:
    result = import_ethikos_demo_scenario(payload, dry_run=False)
print("KX_SCENARIO_JSON_BEGIN")
print(json.dumps(result, ensure_ascii=False, default=str, sort_keys=True))
print("KX_SCENARIO_JSON_END")
KX_DJANGO_PY
status=$?
sudo -n docker exec "${DJANGO_CID}" rm -f "${CONTAINER_JSON}" >/dev/null 2>&1 || true
rm -f "${REMOTE_JSON}" >/dev/null 2>&1 || true
exit $status
'''

LOCAL_PROMOTION_ANALYZE_SCRIPT = 'import json\nfrom django.apps import apps\nfrom django.conf import settings\nfrom django.db import connection\nfrom django.db.migrations.executor import MigrationExecutor\nfrom konnaxion.ekoh.db import ekoh_smartvote_db_scope\n\nSYSTEM_ALLOWED_TARGETS = {"auth.Permission", "contenttypes.ContentType"}\n\ndef model_label(model):\n    return f"{model._meta.app_label}.{model._meta.object_name}"\n\ndef selected_models():\n    result = [apps.get_model("users", "User"), apps.get_model("auth", "Group")]\n    try:\n        result.append(apps.get_model("account", "EmailAddress"))\n    except LookupError:\n        pass\n    for app_label in ("ethikos", "ekoh", "smart_vote", "kollective_intelligence"):\n        result.extend(apps.get_app_config(app_label).get_models())\n    seen = set(); unique = []\n    for model in result:\n        label = model_label(model)\n        if label not in seen:\n            seen.add(label); unique.append(model)\n    return unique\n\npayload = {"ok": True, "counts": {}, "warnings": [], "blockers": [], "selected_models": []}\ntry:\n    db = settings.DATABASES["default"]\n    payload["database"] = {"vendor": connection.vendor, "host": str(db.get("HOST") or ""), "name": str(db.get("NAME") or "")}\n    with ekoh_smartvote_db_scope():\n        models = selected_models()\n        for model in models:\n            label = model_label(model)\n            payload["selected_models"].append(label)\n            payload["counts"][label] = model._base_manager.count()\n        User = apps.get_model("users", "User")\n        avatar_count = User._base_manager.exclude(avatar="").exclude(avatar__isnull=True).count()\n        if avatar_count:\n            payload["warnings"].append({"code": "media_not_transferred", "message": f"{avatar_count} utilisateur(s) ont un avatar. Le pack DB conserve le chemin, mais ne copie pas les fichiers média."})\n        selected_labels = set(payload["selected_models"])\n        for model in models:\n            source_label = model_label(model)\n            for field in model._meta.get_fields():\n                related = getattr(field, "related_model", None)\n                if related is None or getattr(field, "auto_created", False):\n                    continue\n                target_label = model_label(related)\n                if target_label in selected_labels or target_label in SYSTEM_ALLOWED_TARGETS:\n                    continue\n                count = 0\n                try:\n                    if getattr(field, "many_to_many", False):\n                        count = model._base_manager.filter(**{f"{field.name}__isnull": False}).distinct().count()\n                    elif getattr(field, "many_to_one", False) or getattr(field, "one_to_one", False):\n                        count = model._base_manager.filter(**{f"{field.name}__isnull": False}).count() if getattr(field, "null", False) else model._base_manager.count()\n                except Exception as exc:\n                    payload["warnings"].append({"code": "dependency_scan_warning", "message": f"Impossible d\'inspecter {source_label}.{field.name}: {type(exc).__name__}: {exc}"})\n                    continue\n                if count:\n                    payload["blockers"].append({"source": source_label, "field": field.name, "target": target_label, "count": count})\n        executor = MigrationExecutor(connection)\n        plan = executor.migration_plan(executor.loader.graph.leaf_nodes())\n        payload["pending_migrations"] = [f"{m.app_label}.{m.name}" for m, backwards in plan if not backwards]\n        payload["pending_migration_count"] = len(payload["pending_migrations"])\n        if payload["blockers"] or payload["pending_migration_count"]:\n            payload["ok"] = False\nexcept Exception as exc:\n    payload = {"ok": False, "error": {"type": type(exc).__name__, "message": str(exc)}}\nprint("KX_LOCAL_ANALYZE_JSON_BEGIN")\nprint(json.dumps(payload, ensure_ascii=False, sort_keys=True))\nprint("KX_LOCAL_ANALYZE_JSON_END")\n'

LOCAL_PROMOTION_EXPORT_SCRIPT = 'import json\nimport os\nfrom pathlib import Path\nfrom django.core.management import call_command\nfrom konnaxion.ekoh.db import ekoh_smartvote_db_scope\noutput = Path(os.environ["KX_PROMOTION_FIXTURE"])\nlabels = ["users.User", "auth.Group", "account.EmailAddress", "ethikos", "ekoh", "smart_vote", "kollective_intelligence"]\nwith ekoh_smartvote_db_scope():\n    with output.open("w", encoding="utf-8", newline="\\n") as handle:\n        call_command("dumpdata", *labels, format="json", indent=2, use_natural_foreign_keys=True, use_natural_primary_keys=True, use_base_manager=True, stdout=handle, verbosity=0)\nprint("KX_LOCAL_EXPORT_JSON_BEGIN")\nprint(json.dumps({"ok": True, "fixture": str(output)}, ensure_ascii=False))\nprint("KX_LOCAL_EXPORT_JSON_END")\n'

PROMOTION_REMOTE_TEMPLATE = r'''set -eu
INSTANCE_ID="$1"
KX_ROOT="$2"
REMOTE_FIXTURE="$3"
MODE="$4"
EXPECTED_SHA="$5"
PROJECT_NAME="konnaxion-${INSTANCE_ID}"
COMPOSE_FILE="${KX_ROOT}/instances/${INSTANCE_ID}/state/docker-compose.runtime.yml"
if ! sudo -n true >/dev/null 2>&1; then
  echo "KX_PROMOTION_ERROR noninteractive sudo is required for Docker access"
  exit 64
fi
if ! sudo -n test -f "${COMPOSE_FILE}"; then
  echo "KX_PROMOTION_ERROR compose file missing: ${COMPOSE_FILE}"
  exit 65
fi
DJANGO_CID="$(sudo -n docker compose -p "${PROJECT_NAME}" -f "${COMPOSE_FILE}" ps -q django-api 2>/dev/null || true)"
if [ -z "${DJANGO_CID}" ]; then
  echo "KX_PROMOTION_ERROR django-api is not running"
  exit 66
fi
ACTUAL_SHA="$(sha256sum "${REMOTE_FIXTURE}" | awk '{print $1}')"
if [ "${ACTUAL_SHA}" != "${EXPECTED_SHA}" ]; then
  echo "KX_PROMOTION_ERROR sha256 mismatch before docker copy"
  exit 67
fi
CONTAINER_FIXTURE="/tmp/kx-ethikos-local-promotion.json"
sudo -n docker cp "${REMOTE_FIXTURE}" "${DJANGO_CID}:${CONTAINER_FIXTURE}"
sudo -n docker exec -i -e KX_PROMOTION_MODE="${MODE}" -e KX_PROMOTION_EXPECTED_SHA="${EXPECTED_SHA}" -e KX_PROMOTION_FIXTURE="${CONTAINER_FIXTURE}" "${DJANGO_CID}" sh -lc 'python manage.py shell' <<'KX_DJANGO_PY'
import hashlib, json, os
from collections import Counter
from pathlib import Path
from django.apps import apps
from django.core.management import call_command
from django.db import connection, transaction
from konnaxion.ekoh.db import set_local_ekoh_smartvote_search_path

fixture = Path(os.environ["KX_PROMOTION_FIXTURE"])
mode = os.environ["KX_PROMOTION_MODE"]
expected_sha = os.environ["KX_PROMOTION_EXPECTED_SHA"]
raw = fixture.read_bytes()
actual_sha = hashlib.sha256(raw).hexdigest()
if actual_sha != expected_sha:
    raise RuntimeError("Fixture SHA-256 mismatch inside django container")
items = json.loads(raw.decode("utf-8"))
if not isinstance(items, list):
    raise RuntimeError("Promotion fixture must be a JSON fixture list")
fixture_counts = Counter(str(item.get("model") or "") for item in items if isinstance(item, dict))

DOMAIN_APPS = ("users", "ethikos", "ekoh", "smart_vote", "kollective_intelligence")
SAFE_BOOTSTRAP = {
    "kollective_intelligence.ExpertiseCategory": {
        "fixture_model": "kollective_intelligence.expertisecategory",
        "field": "name",
        "values": {
            "Frontend Development",
            "Backend Development",
            "UI/UX Design",
            "Data Science",
            "DevOps",
            "Mobile Development",
            "QA",
            "Project Management",
        },
    },
    "smart_vote.VoteModality": {
        "fixture_model": "smart_vote.votemodality",
        "field": "name",
        "values": {"approval", "ranking", "rating", "preferential", "budget_split"},
    },
}

def model_counts():
    result = {}
    for app_label in DOMAIN_APPS:
        for model in apps.get_app_config(app_label).get_models():
            result[f"{app_label}.{model._meta.object_name}"] = model._base_manager.count()
    return result

def fixture_field_values(fixture_model, field):
    result = []
    for item in items:
        if not isinstance(item, dict) or str(item.get("model") or "") != fixture_model:
            continue
        fields = item.get("fields") if isinstance(item.get("fields"), dict) else {}
        value = fields.get(field)
        if value is not None:
            result.append(str(value))
    return sorted(result)

def reverse_reference_counts(model):
    refs = {}
    target_qs = model._base_manager.all()
    for rel in model._meta.related_objects:
        related_model = getattr(rel, "related_model", None)
        field = getattr(rel, "field", None)
        field_name = getattr(field, "name", None)
        if related_model is None or not field_name:
            continue
        related_label = f"{related_model._meta.app_label}.{related_model._meta.object_name}"
        try:
            count = related_model._base_manager.filter(**{f"{field_name}__in": target_qs}).distinct().count()
        except Exception as exc:
            refs[f"{related_label}.{field_name}"] = {"error": f"{type(exc).__name__}: {exc}"}
            continue
        if count:
            refs[f"{related_label}.{field_name}"] = count
    return refs

payload = {
    "ok": False,
    "mode": mode,
    "fixture_sha256": actual_sha,
    "fixture_objects": len(items),
    "fixture_counts": dict(sorted(fixture_counts.items())),
}
try:
    with transaction.atomic():
        set_local_ekoh_smartvote_search_path()
        before = model_counts()
        all_non_empty = {k: v for k, v in before.items() if v}
        unsafe_non_empty = {}
        bootstrap = {}

        for label, count in sorted(all_non_empty.items()):
            spec = SAFE_BOOTSTRAP.get(label)
            if spec is None:
                unsafe_non_empty[label] = count
                continue

            app_label, model_name = label.split(".", 1)
            model = apps.get_model(app_label, model_name)
            field = spec["field"]
            actual_values = sorted(str(v) for v in model._base_manager.values_list(field, flat=True))
            expected_values = sorted(spec["values"])
            fixture_values = fixture_field_values(spec["fixture_model"], field)
            fixture_has_expected = set(expected_values).issubset(set(fixture_values))
            refs = reverse_reference_counts(model)
            exact_bootstrap = actual_values == expected_values
            refs_clear = not refs

            bootstrap[label] = {
                "count": count,
                "field": field,
                "actual_values": actual_values,
                "expected_values": expected_values,
                "fixture_contains_expected": fixture_has_expected,
                "reverse_references": refs,
                "accepted": bool(exact_bootstrap and fixture_has_expected and refs_clear),
            }
            if not bootstrap[label]["accepted"]:
                unsafe_non_empty[label] = count

        payload["before"] = before
        payload["target_non_empty"] = all_non_empty
        payload["bootstrap_rows"] = bootstrap
        payload["unsafe_non_empty"] = unsafe_non_empty

        if unsafe_non_empty:
            raise RuntimeError(
                "Target promotion scope contains non-bootstrap or unsafe rows; refusing merge. "
                + ", ".join(f"{k}={v}" for k, v in sorted(unsafe_non_empty.items()))
            )

        removed = {}
        for label, detail in bootstrap.items():
            if not detail.get("accepted"):
                continue
            app_label, model_name = label.split(".", 1)
            model = apps.get_model(app_label, model_name)
            count = model._base_manager.count()
            model._base_manager.all().delete()
            removed[label] = count
        payload["bootstrap_rows_removed_for_load"] = removed

        call_command("loaddata", str(fixture), verbosity=0)
        connection.check_constraints()
        payload["after"] = model_counts()
        payload["ok"] = True
        payload["rolled_back"] = mode == "preview"
        if mode == "preview":
            transaction.set_rollback(True)
        elif mode != "import":
            raise RuntimeError(f"Unknown promotion mode: {mode}")
except Exception as exc:
    payload["ok"] = False
    payload["error"] = {"type": type(exc).__name__, "message": str(exc)}

print("KX_PROMOTION_JSON_BEGIN")
print(json.dumps(payload, ensure_ascii=False, default=str, sort_keys=True))
print("KX_PROMOTION_JSON_END")
KX_DJANGO_PY
status=$?
sudo -n docker exec "${DJANGO_CID}" rm -f "${CONTAINER_FIXTURE}" >/dev/null 2>&1 || true
rm -f "${REMOTE_FIXTURE}" >/dev/null 2>&1 || true
exit $status
'''


class RepairApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("1180x980")
        self.manager_dir = Path(__file__).resolve().parent
        self.report_root = self.manager_dir / "diagnostics" / "ethikos-prod"
        self.repair_root = self.manager_dir / "diagnostics" / "ethikos-prod-repairs"
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.running = False
        self.report: dict[str, object] | None = None
        self.last_preview: dict[str, object] | None = None
        self.last_preview_signature: tuple[str, str] | None = None
        self.last_local_analysis: dict[str, object] | None = None
        self.last_promotion_preview: dict[str, object] | None = None
        self.last_promotion_signature: tuple[str, str] | None = None

        self.report_path = tk.StringVar(value=self._latest_report_path())
        self.scenario_path = tk.StringVar(value="")
        self.local_backend = tk.StringVar(value=str(self.manager_dir.parent / "Konnaxion" / "backend"))
        self.promotion_pack_path = tk.StringVar(value="")
        self.host = tk.StringVar(value="")
        self.user = tk.StringVar(value="kx-admin")
        self.port = tk.StringVar(value="22")
        self.ssh_key = tk.StringVar(value=str(Path.home() / ".ssh" / "id_ed25519"))
        self.instance = tk.StringVar(value="konnaxion-prod")
        self.kx_root = tk.StringVar(value="/opt/konnaxion")
        self.backup_confirmed = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value="Chargez un rapport d'inspection avant toute correction.")

        self._build_ui()
        if self.report_path.get():
            self._load_report()
        self.root.after(120, self._poll_events)

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=10)
        outer.pack(fill="both", expand=True)

        report_box = ttk.LabelFrame(outer, text="Rapport d'inspection", padding=8)
        report_box.pack(fill="x")
        ttk.Entry(report_box, textvariable=self.report_path).grid(row=0, column=0, sticky="ew")
        ttk.Button(report_box, text="…", width=4, command=self._browse_report).grid(row=0, column=1, padx=5)
        ttk.Button(report_box, text="Charger", command=self._load_report).grid(row=0, column=2)
        report_box.columnconfigure(0, weight=1)

        target = ttk.LabelFrame(outer, text="Cible issue du rapport", padding=8)
        target.pack(fill="x", pady=(8, 0))
        rows = [
            ("VPS / IP", self.host, 0, 0), ("SSH user", self.user, 0, 2), ("Port", self.port, 0, 4),
            ("Instance", self.instance, 1, 0), ("KX root", self.kx_root, 1, 2), ("Clé SSH", self.ssh_key, 1, 4),
        ]
        for label, var, row, col in rows:
            ttk.Label(target, text=label).grid(row=row, column=col, sticky="w", padx=(0, 5), pady=3)
            ttk.Entry(target, textvariable=var, width=24).grid(row=row, column=col + 1, sticky="ew", padx=(0, 12), pady=3)
        for col in (1, 3, 5):
            target.columnconfigure(col, weight=1)

        migration = ttk.LabelFrame(outer, text="Réparation certaine : migrations", padding=8)
        migration.pack(fill="x", pady=(8, 0))
        ttk.Label(migration, text=(
            "Le correcteur appelle le runner canonique kx_agent.runtime.migrations sur le VPS. "
            "Il ne lance jamais makemigrations."
        )).pack(anchor="w")
        confirm = ttk.Checkbutton(
            migration,
            text="Je confirme qu'une sauvegarde DB récente et restaurable existe avant migration.",
            variable=self.backup_confirmed,
        )
        confirm.pack(anchor="w", pady=4)
        actions = ttk.Frame(migration)
        actions.pack(fill="x")
        self.validate_button = ttk.Button(actions, text="Valider plan/check (lecture seule)", command=lambda: self._start("validate_migrations"))
        self.validate_button.pack(side="left")
        self.migrate_button = ttk.Button(actions, text="Appliquer migrate --noinput", command=self._confirm_migration)
        self.migrate_button.pack(side="left", padx=6)

        scenario = ttk.LabelFrame(outer, text="Données Ethikos (optionnel, jamais automatique)", padding=8)
        scenario.pack(fill="x", pady=(8, 0))
        ttk.Label(scenario, text=(
            "Utilise le demo importer canonique. Preview obligatoire avant import. "
            "Le seed_ethikos_workflow local n'est jamais utilisé en production."
        )).grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Entry(scenario, textvariable=self.scenario_path).grid(row=1, column=0, sticky="ew", pady=4)
        ttk.Button(scenario, text="…", width=4, command=self._browse_scenario).grid(row=1, column=1, padx=5)
        ttk.Button(scenario, text="Preview", command=lambda: self._start("scenario_preview")).grid(row=1, column=2)
        self.import_button = ttk.Button(scenario, text="Importer scénario prévisualisé", command=self._confirm_scenario_import)
        self.import_button.grid(row=1, column=3, padx=(6, 0))
        scenario.columnconfigure(0, weight=1)

        promotion = ttk.LabelFrame(outer, text="Promotion contrôlée : données locales → production", padding=8)
        promotion.pack(fill="x", pady=(8, 0))
        ttk.Label(promotion, text=("Transfère comptes + EmailAddress + Ethikos + EkoH + Smart Vote + compatibilité kollective. "
                                   "Exclut sessions, MFA, tokens sociaux et données opérationnelles. Preview prod = rollback transactionnel.")).grid(row=0, column=0, columnspan=6, sticky="w")
        ttk.Label(promotion, text="Backend local").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(promotion, textvariable=self.local_backend).grid(row=1, column=1, columnspan=3, sticky="ew", padx=5)
        ttk.Button(promotion, text="…", width=4, command=self._browse_local_backend).grid(row=1, column=4)
        self.local_analyze_button = ttk.Button(promotion, text="1. Analyser local", command=lambda: self._start("local_analyze"))
        self.local_analyze_button.grid(row=1, column=5, padx=(6, 0))
        ttk.Label(promotion, text="Pack local").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Entry(promotion, textvariable=self.promotion_pack_path).grid(row=2, column=1, columnspan=3, sticky="ew", padx=5)
        ttk.Button(promotion, text="…", width=4, command=self._browse_promotion_pack).grid(row=2, column=4)
        self.local_pack_button = ttk.Button(promotion, text="2. Créer pack", command=lambda: self._start("local_pack"))
        self.local_pack_button.grid(row=2, column=5, padx=(6, 0))
        promo_actions = ttk.Frame(promotion); promo_actions.grid(row=3, column=0, columnspan=6, sticky="ew", pady=(4, 0))
        self.promotion_preview_button = ttk.Button(promo_actions, text="3. Prévisualiser sur prod (ROLLBACK)", command=lambda: self._start("promotion_preview"))
        self.promotion_preview_button.pack(side="left")
        self.promotion_import_button = ttk.Button(promo_actions, text="4. Importer local → prod", command=self._confirm_promotion_import)
        self.promotion_import_button.pack(side="left", padx=6)
        ttk.Label(promo_actions, text="Import bloqué si la cible n'est plus vide ou si le pack change.").pack(side="left", padx=8)
        for col in (1, 2, 3): promotion.columnconfigure(col, weight=1)

        ttk.Label(outer, textvariable=self.status).pack(fill="x", pady=8)
        self.log = scrolledtext.ScrolledText(outer, wrap="word", height=28, font=("Consolas", 9))
        self.log.pack(fill="both", expand=True)
        self.log.insert("end", "Aucune opération destructive automatique. Les données ne sont importées qu'après preview + confirmation exacte.\n")
        self.log.configure(state="disabled")

    def _latest_report_path(self) -> str:
        candidates = sorted(self.report_root.glob("*/ethikos-prod-inspection.json"), reverse=True)
        return str(candidates[0]) if candidates else ""

    def _browse_report(self) -> None:
        value = filedialog.askopenfilename(title="Rapport Ethikos", filetypes=[("JSON", "*.json"), ("Tous", "*")])
        if value:
            self.report_path.set(value)

    def _browse_scenario(self) -> None:
        value = filedialog.askopenfilename(title="Scénario Ethikos JSON", filetypes=[("JSON", "*.json"), ("Tous", "*")])
        if value:
            self.scenario_path.set(value)
            self.last_preview = None
            self.last_preview_signature = None

    def _browse_local_backend(self) -> None:
        value = filedialog.askdirectory(title="Backend Konnaxion local")
        if value:
            self.local_backend.set(value)
            self.last_local_analysis = None

    def _browse_promotion_pack(self) -> None:
        value = filedialog.askopenfilename(title="Pack local → prod", filetypes=[("Promotion pack", "*.zip"), ("Tous", "*")])
        if value:
            self.promotion_pack_path.set(value)
            self.last_promotion_preview = None
            self.last_promotion_signature = None

    def _load_report(self) -> None:
        path = Path(self.report_path.get().strip())
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Rapport illisible: {exc}")
            return
        if report.get("schema") != INSPECTION_SCHEMA:
            messagebox.showerror(APP_TITLE, f"Schéma de rapport inattendu: {report.get('schema')}")
            return
        target = report.get("target") or {}
        self.host.set(str(target.get("host") or ""))
        self.user.set(str(target.get("ssh_user") or "kx-admin"))
        self.port.set(str(target.get("ssh_port") or "22"))
        self.instance.set(str(target.get("instance_id") or "konnaxion-prod"))
        self.kx_root.set(str(target.get("kx_root") or "/opt/konnaxion"))
        self.report = report
        self._write_log("\n=== Rapport chargé ===\n")
        for item in report.get("assessment", []):
            if isinstance(item, dict):
                self._write_log(f"[{str(item.get('severity')).upper()}] {item.get('code')}: {item.get('message')}\n")
        pending = self._pending_migrations()
        if pending is None:
            self.status.set("Rapport chargé, mais inspection distante indisponible. Actions destructives bloquées jusqu'à une inspection distante réussie.")
        else:
            self.status.set(f"Rapport chargé. Migrations ciblées en attente: {pending}.")

    def _remote_inspection_ok(self) -> bool:
        if not isinstance(self.report, dict):
            return False
        remote = self.report.get("remote") if isinstance(self.report.get("remote"), dict) else {}
        return bool(remote.get("ok", False))

    def _pending_migrations(self) -> int | None:
        if not self._remote_inspection_ok():
            return None
        assert isinstance(self.report, dict)
        remote = self.report.get("remote") if isinstance(self.report.get("remote"), dict) else {}
        migrations = remote.get("migrations") if isinstance(remote.get("migrations"), dict) else {}
        return int(migrations.get("all_pending_count") or migrations.get("pending_count") or 0)

    def _require_remote_inspection(self) -> bool:
        if self._remote_inspection_ok():
            return True
        messagebox.showerror(
            APP_TITLE,
            "Action bloquée : le rapport ne contient pas d'inspection Django distante réussie. "
            "Corrigez d'abord l'accès SSH puis relancez KX_Ethikos_Prod_Inspector.pyw.",
        )
        self.status.set("Action bloquée : inspection distante non validée.")
        return False

    def _report_target_empty_for_promotion(self) -> tuple[bool, str]:
        if not self._remote_inspection_ok() or not isinstance(self.report, dict):
            return False, "inspection distante absente"
        remote = self.report.get("remote") if isinstance(self.report.get("remote"), dict) else {}
        users = remote.get("users") if isinstance(remote.get("users"), dict) else {}
        ethikos = remote.get("ethikos") if isinstance(remote.get("ethikos"), dict) else {}
        ekoh = remote.get("ekoh") if isinstance(remote.get("ekoh"), dict) else {}
        smart = remote.get("smart_vote") if isinstance(remote.get("smart_vote"), dict) else {}
        checks = {
            "users": int(users.get("total") or 0), "topics": int(ethikos.get("topics") or 0),
            "stances": int(ethikos.get("stances") or 0), "arguments": int(ethikos.get("arguments") or 0),
            "categories": int(ethikos.get("categories") or 0), "ekoh_expertise_scores": int(ekoh.get("expertise_scores") or 0),
            "ekoh_ethics_scores": int(ekoh.get("ethics_scores") or 0), "smart_vote_bindings": int(smart.get("source_bindings") or 0),
            "smart_vote_consultations": int(smart.get("consultations") or 0),
        }
        non_empty = {k: v for k, v in checks.items() if v}
        return (not non_empty, "scope vide" if not non_empty else ", ".join(f"{k}={v}" for k, v in non_empty.items()))

    def _confirm_promotion_import(self) -> None:
        if not self._require_remote_inspection(): return
        empty, reason = self._report_target_empty_for_promotion()
        if not empty:
            messagebox.showerror(APP_TITLE, f"Promotion bloquée : cible non vide ({reason}). Relancez l'inspecteur."); return
        if not self.backup_confirmed.get():
            messagebox.showerror(APP_TITLE, "Confirmez d'abord qu'une sauvegarde DB récente et restaurable existe."); return
        if not isinstance(self.last_promotion_preview, dict) or not self.last_promotion_preview.get("ok") or not self.last_promotion_preview.get("rolled_back"):
            messagebox.showerror(APP_TITLE, "Un preview prod valide avec rollback est requis."); return
        pack = Path(self.promotion_pack_path.get().strip()).resolve()
        pack_hash = hashlib.sha256(pack.read_bytes()).hexdigest()
        if self.last_promotion_signature != (str(pack), pack_hash):
            messagebox.showerror(APP_TITLE, "Le pack a changé depuis le preview."); return
        phrase = f"PROMOTE {self.instance.get().strip()} {pack_hash[:12]}"
        if simpledialog.askstring(APP_TITLE, f"Tapez exactement :\n{phrase}") != phrase:
            self.status.set("Promotion annulée : confirmation incorrecte."); return
        self._start("promotion_import")

    def _confirm_migration(self) -> None:
        if not self._require_remote_inspection():
            return
        if not self.backup_confirmed.get():
            messagebox.showerror(APP_TITLE, "Confirmez d'abord qu'une sauvegarde DB récente et restaurable existe.")
            return
        phrase = f"MIGRATE {self.instance.get().strip()}"
        typed = simpledialog.askstring(APP_TITLE, f"Tapez exactement :\n{phrase}")
        if typed != phrase:
            self.status.set("Migration annulée : confirmation incorrecte.")
            return
        self._start("apply_migrations")

    def _confirm_scenario_import(self) -> None:
        if not self._require_remote_inspection():
            return
        if not isinstance(self.last_preview, dict) or not self.last_preview.get("ok") or self.last_preview_signature is None:
            messagebox.showerror(APP_TITLE, "Un preview valide du scénario est requis avant l'import.")
            return
        current_path = Path(self.scenario_path.get().strip()).resolve()
        try:
            current_hash = hashlib.sha256(current_path.read_bytes()).hexdigest()
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Impossible de relire le scénario: {exc}")
            return
        if (str(current_path), current_hash) != self.last_preview_signature:
            messagebox.showerror(APP_TITLE, "Le fichier scénario a changé depuis le preview. Relancez Preview.")
            self.last_preview = None
            self.last_preview_signature = None
            return
        scenario_key = str(self.last_preview.get("scenario_key") or "")
        phrase = f"IMPORT {scenario_key}"
        typed = simpledialog.askstring(APP_TITLE, f"Tapez exactement :\n{phrase}")
        if typed != phrase:
            self.status.set("Import annulé : confirmation incorrecte.")
            return
        self._start("scenario_import")

    def _start(self, mode: str) -> None:
        if self.running:
            return
        try:
            cfg = self._target_config()
            self._validate_target(cfg)
            if mode.startswith("scenario"):
                self._validate_scenario_path()
            if mode in {"local_analyze", "local_pack"}:
                self._validate_local_backend()
            if mode in {"promotion_preview", "promotion_import"}:
                self._validate_promotion_pack()
                if not self._require_remote_inspection(): return
        except Exception as exc:
            messagebox.showerror(APP_TITLE, str(exc))
            return
        self.running = True
        self._set_buttons(False)
        self.status.set("Opération en cours…")
        threading.Thread(target=self._worker, args=(mode, cfg), daemon=True).start()

    def _worker(self, mode: str, cfg: dict[str, str]) -> None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        repair_dir = self.repair_root / stamp
        repair_dir.mkdir(parents=True, exist_ok=True)
        record: dict[str, object] = {
            "schema": REPAIR_SCHEMA,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "mode": mode,
            "target": cfg,
            "source_inspection": self.report_path.get().strip(),
        }
        try:
            if mode == "validate_migrations":
                result = self._run_remote_script(cfg, MIGRATION_VALIDATE_SCRIPT, "KX_VALIDATE_JSON_BEGIN", "KX_VALIDATE_JSON_END")
            elif mode == "apply_migrations":
                result = self._run_remote_script(cfg, MIGRATION_REMOTE_SCRIPT, "KX_REPAIR_JSON_BEGIN", "KX_REPAIR_JSON_END")
            elif mode in {"scenario_preview", "scenario_import"}:
                result = self._run_scenario(cfg, import_mode=(mode == "scenario_import"))
                if mode == "scenario_preview": self.last_preview = result if isinstance(result, dict) else None
            elif mode == "local_analyze":
                result = self._run_local_analysis(); self.last_local_analysis = result
            elif mode == "local_pack":
                result = self._create_local_promotion_pack()
            elif mode in {"promotion_preview", "promotion_import"}:
                result = self._run_promotion_pack(cfg, import_mode=(mode == "promotion_import"))
                if mode == "promotion_preview": self.last_promotion_preview = result if isinstance(result, dict) else None
            else:
                raise ValueError(mode)
            record["result"] = result
            out = repair_dir / "repair-result.json"
            out.write_text(json.dumps(record, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            self._log("\nRésultat structuré:\n" + json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n")
            self.events.put(("done", {"mode": mode, "path": str(out), "result": result}))
        except Exception:
            text = traceback.format_exc()
            (repair_dir / "repair-error.txt").write_text(text, encoding="utf-8")
            self._log("\nERREUR\n" + text + "\n")
            self.events.put(("failed", text))

    def _run_remote_script(self, cfg: dict[str, str], script: str, begin: str, end: str) -> dict[str, object]:
        ssh = find_ssh()
        remote = f"{cfg['user']}@{cfg['host']}"
        remote_cmd = "bash -s -- {} {}".format(shlex.quote(cfg["instance"]), shlex.quote(cfg["kx_root"]))
        cmd = ssh_base(ssh, cfg) + [remote, remote_cmd]
        script_bytes = script.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
        completed = subprocess.run(
            cmd, input=script_bytes, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=1200, cwd=str(self.manager_dir),
        )
        output = (completed.stdout or b"").decode("utf-8", errors="replace")
        self._log(output)
        parsed = extract_marker_json(output, begin, end)
        if parsed is None:
            raise RuntimeError(f"Aucun payload structuré retourné (rc={completed.returncode}).")
        parsed["ssh_returncode"] = completed.returncode
        return parsed

    def _run_scenario(self, cfg: dict[str, str], *, import_mode: bool) -> dict[str, object]:
        local = Path(self.scenario_path.get().strip())
        raw_bytes = local.read_bytes()
        local_hash = hashlib.sha256(raw_bytes).hexdigest()
        data = json.loads(raw_bytes.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Le scénario JSON doit être un objet.")
        stamp = datetime.now().strftime("%Y%m%d%H%M%S")
        remote_json = f"/tmp/kx-ethikos-scenario-{stamp}.json"
        remote = f"{cfg['user']}@{cfg['host']}"
        scp = find_scp()
        copy_cmd = [scp, "-i", cfg["ssh_key"], "-P", cfg["port"], str(local), f"{remote}:{remote_json}"]
        copied = subprocess.run(copy_cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding="utf-8", errors="replace", timeout=120)
        self._log(copied.stdout or "")
        if copied.returncode != 0:
            raise RuntimeError("Copie du scénario vers /tmp échouée.")
        mode = "import" if import_mode else "preview"
        script = SCENARIO_REMOTE_TEMPLATE.replace("__MODE__", mode)
        ssh = find_ssh()
        remote_cmd = "bash -s -- {} {} {} {}".format(
            shlex.quote(cfg["instance"]), shlex.quote(cfg["kx_root"]), shlex.quote(remote_json), shlex.quote(mode)
        )
        script_bytes = script.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
        completed = subprocess.run(
            ssh_base(ssh, cfg) + [remote, remote_cmd], input=script_bytes,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=1200,
        )
        output = (completed.stdout or b"").decode("utf-8", errors="replace")
        self._log(output)
        parsed = extract_marker_json(output, "KX_SCENARIO_JSON_BEGIN", "KX_SCENARIO_JSON_END")
        if parsed is None:
            raise RuntimeError(f"Preview/import n'a pas retourné de JSON structuré (rc={completed.returncode}).")
        parsed["ssh_returncode"] = completed.returncode
        parsed["local_scenario_sha256"] = local_hash
        parsed["local_scenario_path"] = str(local.resolve())
        if not import_mode:
            self.last_preview_signature = (str(local.resolve()), local_hash)
        return parsed

    def _local_python(self) -> Path:
        backend = Path(self.local_backend.get().strip()).resolve()
        for candidate in (backend / ".venv" / "Scripts" / "python.exe", backend / ".venv" / "bin" / "python"):
            if candidate.is_file(): return candidate
        raise ValueError("Python local .venv introuvable. Lancez RUN_backend_local.bat au moins une fois.")

    def _run_local_django_script(self, script: str, begin: str, end: str, env_extra: dict[str, str] | None = None) -> dict[str, object]:
        backend = Path(self.local_backend.get().strip()).resolve()
        env = os.environ.copy()
        env.update(env_extra or {})
        normalized = script.replace("\r\n", "\n").replace("\r", "\n")
        runner_path: Path | None = None
        try:
            # Never feed promotion code to an interactive Django shell.  On local
            # developer installs IPython may be auto-selected, which prefixes output
            # with ``In [n]:`` and asks for exit confirmation.  Execute a short -c
            # command that loads a temporary LF/UTF-8 script instead.
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                suffix=".py",
                prefix=".kx-ethikos-promotion-",
                dir=str(backend),
                delete=False,
            ) as runner:
                runner.write(normalized)
                runner_path = Path(runner.name)
            runner_name = runner_path.name
            command = (
                "exec(compile(open("
                + repr(runner_name)
                + ", encoding='utf-8').read(), "
                + repr(runner_name)
                + ", 'exec'))"
            )
            completed = subprocess.run(
                [str(self._local_python()), "manage.py", "shell", "-c", command],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=str(backend),
                env=env,
                timeout=1200,
            )
        finally:
            if runner_path is not None:
                try:
                    runner_path.unlink(missing_ok=True)
                except OSError:
                    pass
        output = (completed.stdout or b"").decode("utf-8", errors="replace")
        self._log(output)
        parsed = extract_marker_json(output, begin, end)
        if parsed is None:
            raise RuntimeError(f"Aucun payload local structuré (rc={completed.returncode}).")
        parsed["local_returncode"] = completed.returncode
        return parsed

    def _run_local_analysis(self) -> dict[str, object]:
        return self._run_local_django_script(LOCAL_PROMOTION_ANALYZE_SCRIPT, "KX_LOCAL_ANALYZE_JSON_BEGIN", "KX_LOCAL_ANALYZE_JSON_END")

    def _create_local_promotion_pack(self) -> dict[str, object]:
        analysis = self._run_local_analysis(); self.last_local_analysis = analysis
        if not analysis.get("ok"): raise RuntimeError("Analyse locale bloquante: " + json.dumps(analysis.get("blockers") or analysis.get("error") or {}, ensure_ascii=False, default=str))
        pack_dir = self.manager_dir / "diagnostics" / "ethikos-local-packs" / datetime.now().strftime("%Y%m%d-%H%M%S"); pack_dir.mkdir(parents=True, exist_ok=True)
        fixture = pack_dir / "fixture.json"
        export = self._run_local_django_script(LOCAL_PROMOTION_EXPORT_SCRIPT, "KX_LOCAL_EXPORT_JSON_BEGIN", "KX_LOCAL_EXPORT_JSON_END", {"KX_PROMOTION_FIXTURE": str(fixture)})
        if not export.get("ok") or not fixture.is_file(): raise RuntimeError("Export local échoué.")
        fixture_hash = hashlib.sha256(fixture.read_bytes()).hexdigest()
        manifest = {"schema": "kx-ethikos-local-promotion/v1", "created_at": datetime.now(timezone.utc).isoformat(), "source_backend": str(Path(self.local_backend.get().strip()).resolve()), "fixture_sha256": fixture_hash, "analysis": analysis, "security": {"contains_password_hashes": True, "contains_email_addresses": True, "contains_sessions": False, "contains_mfa_secrets": False, "contains_social_tokens": False, "copies_media_files": False}}
        (pack_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        pack = pack_dir / "ethikos-local-promotion.zip"
        with zipfile.ZipFile(pack, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.write(fixture, "fixture.json"); zf.write(pack_dir / "manifest.json", "manifest.json")
        self.promotion_pack_path.set(str(pack)); self.last_promotion_preview = None; self.last_promotion_signature = None
        return {"ok": True, "pack": str(pack), "pack_sha256": hashlib.sha256(pack.read_bytes()).hexdigest(), "fixture_sha256": fixture_hash, "analysis": analysis}

    def _read_promotion_pack(self):
        pack = Path(self.promotion_pack_path.get().strip()).resolve(); pack_hash = hashlib.sha256(pack.read_bytes()).hexdigest()
        with zipfile.ZipFile(pack, "r") as zf:
            manifest = json.loads(zf.read("manifest.json").decode("utf-8")); fixture = zf.read("fixture.json")
        if manifest.get("schema") != "kx-ethikos-local-promotion/v1": raise ValueError("Schéma de pack invalide.")
        fixture_hash = hashlib.sha256(fixture).hexdigest()
        if fixture_hash != manifest.get("fixture_sha256"): raise ValueError("SHA-256 fixture invalide.")
        return pack, manifest, fixture, fixture_hash, pack_hash

    def _run_promotion_pack(self, cfg: dict[str, str], *, import_mode: bool) -> dict[str, object]:
        empty, reason = self._report_target_empty_for_promotion()
        if not empty: raise RuntimeError(f"Cible non vide selon le rapport: {reason}")
        pack, manifest, fixture_bytes, fixture_hash, pack_hash = self._read_promotion_pack()
        if import_mode and self.last_promotion_signature != (str(pack), pack_hash): raise RuntimeError("Pack différent du preview prod validé.")
        remote = f"{cfg['user']}@{cfg['host']}"; remote_fixture = f"/tmp/kx-ethikos-local-promotion-{datetime.now().strftime('%Y%m%d%H%M%S')}.json"
        with tempfile.TemporaryDirectory(prefix="kx-promotion-") as tmp:
            local_fixture = Path(tmp) / "fixture.json"; local_fixture.write_bytes(fixture_bytes)
            copied = subprocess.run([find_scp(), "-i", cfg["ssh_key"], "-P", cfg["port"], str(local_fixture), f"{remote}:{remote_fixture}"], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding="utf-8", errors="replace", timeout=180)
            self._log(copied.stdout or "")
            if copied.returncode != 0: raise RuntimeError("Copie fixture vers VPS échouée.")
        mode = "import" if import_mode else "preview"; remote_cmd = "bash -s -- {} {} {} {} {}".format(shlex.quote(cfg["instance"]), shlex.quote(cfg["kx_root"]), shlex.quote(remote_fixture), shlex.quote(mode), shlex.quote(fixture_hash))
        completed = subprocess.run(ssh_base(find_ssh(), cfg) + [remote, remote_cmd], input=PROMOTION_REMOTE_TEMPLATE.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8"), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=1800)
        output = (completed.stdout or b"").decode("utf-8", errors="replace"); self._log(output)
        parsed = extract_marker_json(output, "KX_PROMOTION_JSON_BEGIN", "KX_PROMOTION_JSON_END")
        if parsed is None: raise RuntimeError(f"Aucun payload promotion structuré (rc={completed.returncode}).")
        parsed.update({"ssh_returncode": completed.returncode, "pack_path": str(pack), "pack_sha256": pack_hash, "manifest": manifest})
        if not import_mode and parsed.get("ok") and parsed.get("rolled_back"): self.last_promotion_signature = (str(pack), pack_hash)
        return parsed

    def _target_config(self) -> dict[str, str]:
        return {
            "host": self.host.get().strip(), "user": self.user.get().strip(), "port": self.port.get().strip(),
            "ssh_key": self.ssh_key.get().strip(), "instance": self.instance.get().strip(), "kx_root": self.kx_root.get().strip(),
        }

    def _validate_target(self, cfg: dict[str, str]) -> None:
        if self.report is None:
            raise ValueError("Chargez d'abord le rapport produit par l'inspecteur.")
        for key, value in cfg.items():
            if not value:
                raise ValueError(f"Champ requis: {key}")
        if not Path(cfg["ssh_key"]).exists():
            raise ValueError(f"Clé SSH introuvable: {cfg['ssh_key']}")
        int(cfg["port"])
        find_ssh()

    def _validate_scenario_path(self) -> None:
        path = Path(self.scenario_path.get().strip())
        if not path.is_file():
            raise ValueError("Sélectionnez un scénario JSON existant.")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ValueError(f"JSON invalide: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError("Le scénario doit être un objet JSON.")

    def _validate_local_backend(self) -> None:
        backend = Path(self.local_backend.get().strip())
        if not backend.is_dir() or not (backend / "manage.py").is_file(): raise ValueError("Backend local invalide : manage.py introuvable.")
        self._local_python()

    def _validate_promotion_pack(self) -> None:
        path = Path(self.promotion_pack_path.get().strip())
        if not path.is_file(): raise ValueError("Sélectionnez ou créez un pack local .zip.")
        self._read_promotion_pack()

    def _log(self, text: str) -> None:
        self.events.put(("log", text))

    def _write_log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _poll_events(self) -> None:
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "log":
                    self._write_log(str(value))
                elif kind == "done":
                    self.running = False
                    self._set_buttons(True)
                    info = value if isinstance(value, dict) else {}
                    mode = info.get("mode")
                    result = info.get("result") if isinstance(info.get("result"), dict) else {}
                    if mode == "scenario_preview":
                        self.last_preview = result
                        self.status.set("Preview terminé. Import activable seulement si ok=true.")
                    elif mode == "apply_migrations":
                        self.status.set(f"Migration terminée. ok={result.get('ok')} changed={result.get('changed')}")
                    elif mode == "local_analyze":
                        self.status.set(f"Analyse locale terminée. ok={result.get('ok')} blockers={len(result.get('blockers') or [])}")
                    elif mode == "local_pack":
                        self.status.set(f"Pack local créé: {result.get('pack')}")
                    elif mode == "promotion_preview":
                        self.last_promotion_preview = result; self.status.set(f"Preview prod: ok={result.get('ok')} rollback={result.get('rolled_back')}")
                    elif mode == "promotion_import":
                        self.status.set(f"Promotion local → prod: ok={result.get('ok')}")
                    else:
                        self.status.set(f"Opération terminée: {info.get('path')}")
                elif kind == "failed":
                    self.running = False
                    self._set_buttons(True)
                    self.status.set("Opération échouée. Voir le journal.")
        except queue.Empty:
            pass
        self.root.after(120, self._poll_events)

    def _set_buttons(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        for button in (self.validate_button, self.migrate_button, self.import_button, self.local_analyze_button, self.local_pack_button, self.promotion_preview_button, self.promotion_import_button):
            button.configure(state=state)


def find_ssh() -> str:
    value = shutil.which("ssh.exe") or shutil.which("ssh")
    if not value:
        raise RuntimeError("ssh.exe / ssh introuvable dans PATH.")
    return value


def find_scp() -> str:
    value = shutil.which("scp.exe") or shutil.which("scp")
    if not value:
        raise RuntimeError("scp.exe / scp introuvable dans PATH.")
    return value


def ssh_base(ssh: str, cfg: dict[str, str]) -> list[str]:
    return [
        ssh, "-i", cfg["ssh_key"], "-p", cfg["port"],
        "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "-o", "StrictHostKeyChecking=accept-new",
    ]


def extract_marker_json(text: str, begin: str, end: str) -> dict[str, object] | None:
    start = text.find(begin)
    if start < 0:
        return None
    start += len(begin)
    finish = text.find(end, start)
    if finish < 0:
        return None
    segment = text[start:finish].strip()
    try:
        parsed = json.loads(segment)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        # Defensive compatibility with older runs where IPython prefixed the JSON
        # line with ``In [n]:``.  Decode the first JSON object found between the
        # trusted begin/end markers rather than treating the prompt as payload.
        decoder = json.JSONDecoder()
        for line in segment.splitlines():
            brace = line.find("{")
            if brace < 0:
                continue
            try:
                parsed, _ = decoder.raw_decode(line[brace:].lstrip())
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
        return None


def main() -> None:
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except tk.TclError:
        pass
    RepairApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
