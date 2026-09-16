from __future__ import annotations

import json
import os
import queue
import re
import shlex
import shutil
import ssl
import subprocess
import sys
import threading
import traceback
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

APP_TITLE = "Konnaxion Ethikos Production Inspector"
REPORT_SCHEMA = "kx-ethikos-prod-inspection/v1"
DEFAULT_HOST = "2.56.97.41"
DEFAULT_DOMAIN = "konnaxion.com"
DEFAULT_INSTANCE = "konnaxion-prod"
DEFAULT_USER = "kx-admin"
DEFAULT_PORT = "22"
DEFAULT_KX_ROOT = "/opt/konnaxion"


REMOTE_DJANGO_INSPECTION = r'''
import json
from collections import Counter
from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.recorder import MigrationRecorder

payload = {
    "ok": True,
    "errors": [],
    "migrations": {},
    "database": {},
    "users": {},
    "ethikos": {},
    "ekoh": {},
    "smart_vote": {},
}


def err(section, exc):
    payload["errors"].append({
        "section": section,
        "type": type(exc).__name__,
        "message": str(exc),
    })


def get_model(app_label, model_name):
    try:
        return apps.get_model(app_label, model_name)
    except Exception as exc:
        err(f"model:{app_label}.{model_name}", exc)
        return None


def count_model(model, label):
    if model is None:
        return None
    try:
        return model.objects.count()
    except Exception as exc:
        err(label, exc)
        return None

# Migration state: read-only.
try:
    applied = MigrationRecorder(connection).applied_migrations()
    interesting = {"ethikos", "ekoh", "smart_vote", "kollective_intelligence"}
    applied_pairs = sorted((app, name) for app, name in applied if app in interesting)
    applied_rows = [{"app": app, "name": name} for app, name in applied_pairs]
    executor = MigrationExecutor(connection)
    pending_plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
    all_pending_rows = [
        {"app": migration.app_label, "name": migration.name, "backwards": bool(backwards)}
        for migration, backwards in pending_plan
    ]
    pending_rows = [row for row in all_pending_rows if row["app"] in interesting]
    payload["migrations"] = {
        "applied": applied_rows,
        "applied_count": len(applied_rows),
        "pending": pending_rows,
        "pending_count": len(pending_rows),
        "all_pending": all_pending_rows,
        "all_pending_count": len(all_pending_rows),
    }
except Exception as exc:
    err("migrations", exc)

# Physical DB tables in the two schemas relevant to this investigation.
try:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT table_schema, table_name
            FROM information_schema.tables
            WHERE table_schema IN ('public', 'ekoh_smartvote')
              AND table_type = 'BASE TABLE'
            ORDER BY table_schema, table_name
            """
        )
        tables = [
            {"schema": schema, "table": table}
            for schema, table in cursor.fetchall()
        ]
    interesting_tables = [
        row for row in tables
        if any(token in row["table"].lower() for token in ("ethikos", "ekoh", "expert", "ethic", "consult", "smart_vote", "stance", "argument"))
    ]
    payload["database"] = {
        "schemas": dict(Counter(row["schema"] for row in tables)),
        "interesting_tables": interesting_tables,
    }
except Exception as exc:
    err("database.tables", exc)

# Accounts: counts and usernames only; do not dump password hashes/tokens/emails.
try:
    User = get_user_model()
    sample_fields = ["id", "username", "is_active", "is_staff", "is_superuser"]
    available_fields = {field.name for field in User._meta.fields}
    sample_fields = [field for field in sample_fields if field in available_fields]
    payload["users"] = {
        "total": User.objects.count(),
        "active": User.objects.filter(is_active=True).count() if "is_active" in available_fields else None,
        "staff": User.objects.filter(is_staff=True).count() if "is_staff" in available_fields else None,
        "superusers": User.objects.filter(is_superuser=True).count() if "is_superuser" in available_fields else None,
        "sample": list(User.objects.order_by("id").values(*sample_fields)[:25]),
    }
except Exception as exc:
    err("users", exc)

try:
    EmailAddress = apps.get_model("account", "EmailAddress")
    if EmailAddress is not None:
        payload["users"]["email_addresses"] = EmailAddress.objects.count()
        payload["users"]["verified_email_addresses"] = EmailAddress.objects.filter(verified=True).count()
except Exception:
    # allauth is useful context when installed, but absence is not itself an
    # Ethikos production failure.
    pass

# Canonical Ethikos source facts.
EthikosCategory = get_model("ethikos", "EthikosCategory")
EthikosTopic = get_model("ethikos", "EthikosTopic")
EthikosStance = get_model("ethikos", "EthikosStance")
EthikosArgument = get_model("ethikos", "EthikosArgument")
DemoScenarioImport = get_model("ethikos", "DemoScenarioImport")

payload["ethikos"].update({
    "categories": count_model(EthikosCategory, "ethikos.categories"),
    "topics": count_model(EthikosTopic, "ethikos.topics"),
    "stances": count_model(EthikosStance, "ethikos.stances"),
    "arguments": count_model(EthikosArgument, "ethikos.arguments"),
    "demo_scenario_imports": count_model(DemoScenarioImport, "ethikos.demo_scenario_imports"),
})

if EthikosTopic is not None:
    try:
        topics = []
        for topic in EthikosTopic.objects.select_related("created_by", "category").order_by("-created_at")[:30]:
            topics.append({
                "id": topic.id,
                "title": topic.title,
                "status": topic.status,
                "category": getattr(topic.category, "name", None),
                "created_by": getattr(topic.created_by, "username", None),
                "stance_count": topic.stances.count(),
                "argument_count": topic.arguments.count(),
                "total_votes": topic.total_votes,
            })
        payload["ethikos"]["topic_sample"] = topics
    except Exception as exc:
        err("ethikos.topic_sample", exc)

# EkoH + Smart Vote legacy tables may use a dedicated schema.
try:
    from konnaxion.ekoh.db import ekoh_smartvote_db_scope
    with ekoh_smartvote_db_scope():
        ExpertiseCategory = get_model("ekoh", "ExpertiseCategory")
        UserExpertiseScore = get_model("ekoh", "UserExpertiseScore")
        UserEthicsScore = get_model("ekoh", "UserEthicsScore")
        RatingVisibilitySetting = get_model("ekoh", "RatingVisibilitySetting")
        Consultation = get_model("smart_vote", "Consultation")
        SourceConsultationBinding = get_model("smart_vote", "SourceConsultationBinding")
        ConsultationRelevance = get_model("smart_vote", "ConsultationRelevance")

        payload["ekoh"] = {
            "expertise_categories": count_model(ExpertiseCategory, "ekoh.expertise_categories"),
            "expertise_scores": count_model(UserExpertiseScore, "ekoh.expertise_scores"),
            "ethics_scores": count_model(UserEthicsScore, "ekoh.ethics_scores"),
            "rating_visibility": count_model(RatingVisibilitySetting, "ekoh.rating_visibility"),
        }
        payload["smart_vote"] = {
            "consultations": count_model(Consultation, "smart_vote.consultations"),
            "source_bindings": count_model(SourceConsultationBinding, "smart_vote.source_bindings"),
            "relevance_rows": count_model(ConsultationRelevance, "smart_vote.relevance_rows"),
        }

        if SourceConsultationBinding is not None:
            try:
                payload["smart_vote"]["binding_sample"] = list(
                    SourceConsultationBinding.objects.order_by("source_type", "source_id")
                    .values("source_type", "source_id", "source_key", "consultation_id")[:30]
                )
            except Exception as exc:
                err("smart_vote.binding_sample", exc)
except Exception as exc:
    err("ekoh_smartvote_scope", exc)

# Exercise the actual Smart Vote reading contract for available Ethikos topics.
try:
    if EthikosTopic is not None:
        from konnaxion.smart_vote.services.reading_service import build_ethikos_topic_reading
        reading_rows = []
        for topic_id in EthikosTopic.objects.order_by("id").values_list("id", flat=True)[:20]:
            try:
                reading = build_ethikos_topic_reading(topic_id, viewer=None)
                reading_rows.append({
                    "topic_id": topic_id,
                    "available": reading is not None,
                    "reading_key": reading.get("reading_key") if isinstance(reading, dict) else None,
                    "participant_count": (
                        reading.get("baseline", {}).get("participant_count")
                        if isinstance(reading, dict) else None
                    ),
                })
            except Exception as exc:
                reading_rows.append({
                    "topic_id": topic_id,
                    "available": False,
                    "error": f"{type(exc).__name__}: {exc}",
                })
        payload["smart_vote"]["readings"] = reading_rows
except Exception as exc:
    err("smart_vote.readings", exc)

payload["ok"] = not bool(payload["errors"])
print("KX_ETHIKOS_JSON_BEGIN")
print(json.dumps(payload, ensure_ascii=False, default=str, sort_keys=True))
print("KX_ETHIKOS_JSON_END")
'''


REMOTE_SHELL_TEMPLATE = r'''set -u
INSTANCE_ID="$1"
KX_ROOT="$2"
PROJECT_NAME="konnaxion-${INSTANCE_ID}"
COMPOSE_FILE="${KX_ROOT}/instances/${INSTANCE_ID}/state/docker-compose.runtime.yml"

# kx-admin is intentionally not in the docker group. SecurityDiag configures
# passwordless/noninteractive sudo for approved administrative diagnostics.
if sudo -n true >/dev/null 2>&1; then
  KX_SUDO=(sudo -n)
else
  KX_SUDO=()
fi

echo "KX_ETHIKOS_REMOTE target=${INSTANCE_ID} compose=${COMPOSE_FILE}"
echo "KX_ETHIKOS_PRIVILEGE sudo_noninteractive=$([ ${#KX_SUDO[@]} -gt 0 ] && echo yes || echo no)"

if ! "${KX_SUDO[@]}" test -f "${COMPOSE_FILE}" 2>/dev/null; then
  echo "KX_ETHIKOS_ERROR compose file unavailable: ${COMPOSE_FILE}"
  echo "KX_ETHIKOS_HINT direct permissions may be intentionally restricted; sudo -n is required for kx-admin"
  exit 31
fi

DJANGO_CID="$("${KX_SUDO[@]}" docker compose -p "${PROJECT_NAME}" -f "${COMPOSE_FILE}" ps -q django-api 2>/dev/null || true)"
if [ -z "${DJANGO_CID}" ]; then
  echo "KX_ETHIKOS_ERROR django-api container is not running or is not visible"
  "${KX_SUDO[@]}" docker compose -p "${PROJECT_NAME}" -f "${COMPOSE_FILE}" ps 2>&1 || true
  exit 32
fi

echo "KX_ETHIKOS_DJANGO_CONTAINER ${DJANGO_CID}"
"${KX_SUDO[@]}" docker exec -i "${DJANGO_CID}" sh -lc 'python manage.py shell' <<'KX_DJANGO_PY'
__DJANGO_INSPECTION__
KX_DJANGO_PY

echo "KX_ETHIKOS_LOG_CLUES_BEGIN"
"${KX_SUDO[@]}" docker compose -p "${PROJECT_NAME}" -f "${COMPOSE_FILE}" logs --tail=700 django-api 2>&1 | grep -Ei 'ethikos|ekoh|smart.?vote|migration|ProgrammingError|OperationalError|relation .* does not exist|UndefinedTable|UndefinedColumn|traceback|exception' | tail -220 || true
echo "KX_ETHIKOS_LOG_CLUES_END"
'''


class InspectorApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("1120x780")
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.running = False
        self.latest_report: Path | None = None

        self.manager_dir = Path(__file__).resolve().parent
        self.config_path = self.manager_dir / ".kx-ui" / "ethikos-prod-inspector.json"
        self.report_root = self.manager_dir / "diagnostics" / "ethikos-prod"

        self.vars = {
            "host": tk.StringVar(value=DEFAULT_HOST),
            "domain": tk.StringVar(value=DEFAULT_DOMAIN),
            "instance": tk.StringVar(value=DEFAULT_INSTANCE),
            "user": tk.StringVar(value=DEFAULT_USER),
            "port": tk.StringVar(value=DEFAULT_PORT),
            "ssh_key": tk.StringVar(value=str(Path.home() / ".ssh" / "id_ed25519")),
            "kx_root": tk.StringVar(value=DEFAULT_KX_ROOT),
            "diagnostic": tk.StringVar(value=str(self.manager_dir / "KX_Diagnose_Online.ps1")),
            "source": tk.StringVar(value=str(self.manager_dir.parent / "Konnaxion")),
            "capsule": tk.StringVar(value=""),
        }
        self._load_config()
        self._build_ui()
        self.root.after(120, self._poll_events)

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=10)
        outer.pack(fill="both", expand=True)

        target = ttk.LabelFrame(outer, text="Cible production", padding=8)
        target.pack(fill="x")
        fields = [
            ("Domaine", "domain", 0, 0),
            ("VPS / IP", "host", 0, 2),
            ("Instance", "instance", 0, 4),
            ("SSH user", "user", 1, 0),
            ("SSH port", "port", 1, 2),
            ("KX root", "kx_root", 1, 4),
        ]
        for label, key, row, col in fields:
            ttk.Label(target, text=label).grid(row=row, column=col, sticky="w", padx=(0, 5), pady=3)
            ttk.Entry(target, textvariable=self.vars[key], width=24).grid(row=row, column=col + 1, sticky="ew", padx=(0, 12), pady=3)
        for col in (1, 3, 5):
            target.columnconfigure(col, weight=1)

        paths = ttk.LabelFrame(outer, text="Outils existants", padding=8)
        paths.pack(fill="x", pady=(8, 0))
        self._path_row(paths, 0, "Clé SSH", "ssh_key", self._browse_key)
        self._path_row(paths, 1, "KX_Diagnose_Online.ps1", "diagnostic", self._browse_diagnostic)
        self._path_row(paths, 2, "Source Konnaxion locale", "source", self._browse_source)
        self._path_row(paths, 3, "Capsule locale (optionnelle)", "capsule", self._browse_capsule)

        buttons = ttk.Frame(outer)
        buttons.pack(fill="x", pady=8)
        self.full_button = ttk.Button(buttons, text="Inspection complète", command=lambda: self._start("full"))
        self.full_button.pack(side="left")
        self.deep_button = ttk.Button(buttons, text="Ethikos / EkoH / Smart Vote seulement", command=lambda: self._start("deep"))
        self.deep_button.pack(side="left", padx=6)
        self.general_button = ttk.Button(buttons, text="Diagnostic VPS existant seulement", command=lambda: self._start("general"))
        self.general_button.pack(side="left")
        ttk.Button(buttons, text="Ouvrir le dernier rapport", command=self._open_latest_report).pack(side="right")

        self.status = tk.StringVar(value="Prêt. Toutes les opérations de cet outil sont en lecture seule.")
        ttk.Label(outer, textvariable=self.status).pack(fill="x", pady=(0, 5))

        self.log = scrolledtext.ScrolledText(outer, wrap="word", height=28, font=("Consolas", 9))
        self.log.pack(fill="both", expand=True)
        self.log.insert("end", "Inspecteur production Ethikos — aucun restart, deploy, migrate ou seed.\n")
        self.log.configure(state="disabled")

    def _path_row(self, parent, row: int, label: str, key: str, command) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 5), pady=3)
        ttk.Entry(parent, textvariable=self.vars[key]).grid(row=row, column=1, sticky="ew", pady=3)
        ttk.Button(parent, text="…", width=4, command=command).grid(row=row, column=2, padx=(5, 0), pady=3)
        parent.columnconfigure(1, weight=1)

    def _browse_key(self) -> None:
        value = filedialog.askopenfilename(title="Clé SSH privée")
        if value:
            self.vars["ssh_key"].set(value)

    def _browse_diagnostic(self) -> None:
        value = filedialog.askopenfilename(title="KX_Diagnose_Online.ps1", filetypes=[("PowerShell", "*.ps1"), ("Tous", "*")])
        if value:
            self.vars["diagnostic"].set(value)

    def _browse_source(self) -> None:
        value = filedialog.askdirectory(title="Dossier source Konnaxion")
        if value:
            self.vars["source"].set(value)

    def _browse_capsule(self) -> None:
        value = filedialog.askopenfilename(title="Capsule locale", filetypes=[("Konnaxion capsule", "*.kxcap"), ("Tous", "*")])
        if value:
            self.vars["capsule"].set(value)

    def _log(self, text: str) -> None:
        self.events.put(("log", text))

    def _start(self, mode: str) -> None:
        if self.running:
            return
        try:
            cfg = self._config()
            self._validate_config(cfg, mode)
        except Exception as exc:
            messagebox.showerror(APP_TITLE, str(exc))
            return
        self._save_config()
        self.running = True
        self._set_buttons(False)
        self.status.set("Inspection en cours…")
        threading.Thread(target=self._worker, args=(mode, cfg), daemon=True).start()

    def _worker(self, mode: str, cfg: dict[str, str]) -> None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        report_dir = self.report_root / stamp
        report_dir.mkdir(parents=True, exist_ok=True)
        report: dict[str, object] = {
            "schema": REPORT_SCHEMA,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "target": {
                "domain": cfg["domain"],
                "host": cfg["host"],
                "instance_id": cfg["instance"],
                "ssh_user": cfg["user"],
                "ssh_port": int(cfg["port"]),
                "kx_root": cfg["kx_root"],
            },
            "general_diagnostic": None,
            "api_probes": [],
            "remote": None,
            "assessment": [],
        }
        try:
            self._log(f"\n=== Inspection {stamp} ===\n")
            if mode in {"full", "general"}:
                report["general_diagnostic"] = self._run_general(cfg, report_dir)
            if mode in {"full", "deep"}:
                report["api_probes"] = self._probe_api(cfg["domain"])
                report["remote"] = self._run_remote(cfg, report_dir)
            report["assessment"] = assess_report(report)
            report_path = report_dir / "ethikos-prod-inspection.json"
            report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            summary_path = report_dir / "ethikos-prod-inspection.txt"
            summary_path.write_text(render_summary(report), encoding="utf-8")
            self.latest_report = report_path
            self._log("\n" + render_summary(report) + "\n")
            self.events.put(("done", str(report_path)))
        except Exception:
            error_text = traceback.format_exc()
            (report_dir / "inspector-error.txt").write_text(error_text, encoding="utf-8")
            self._log("\nERREUR INSPECTEUR\n" + error_text + "\n")
            self.events.put(("failed", error_text))

    def _run_general(self, cfg: dict[str, str], report_dir: Path) -> dict[str, object]:
        ps = find_powershell()
        script = Path(cfg["diagnostic"])
        cmd = [
            ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
            "-LocalRepo", str(self.manager_dir),
            "-LocalSource", cfg["source"],
            "-DropletUser", cfg["user"],
            "-DropletHost", cfg["host"],
            "-SshPort", cfg["port"],
            "-SshKey", cfg["ssh_key"],
            "-Domain", cfg["domain"],
            "-InstanceId", cfg["instance"],
            "-RemoteKxRoot", cfg["kx_root"],
            "-RemoteCapsDir", cfg["kx_root"].rstrip("/") + "/capsules",
        ]
        if cfg["capsule"]:
            cmd += ["-LocalCapsule", cfg["capsule"]]
        self._log("\n--- Diagnostic VPS existant ---\n")
        rc, output = run_streamed(cmd, self._log, cwd=self.manager_dir)
        raw_path = report_dir / "KX_Diagnose_Online-output.txt"
        raw_path.write_text(output, encoding="utf-8", errors="replace")
        transcript = None
        matches = re.findall(r"(?im)^Log file:\s*(.+?)\s*$", output)
        if matches:
            transcript = matches[-1].strip()
        return {"returncode": rc, "captured_output": str(raw_path), "transcript_log": transcript}

    def _run_remote(self, cfg: dict[str, str], report_dir: Path) -> dict[str, object]:
        ssh = find_ssh()
        remote = f"{cfg['user']}@{cfg['host']}"
        remote_command = "bash -s -- {} {}".format(shlex.quote(cfg["instance"]), shlex.quote(cfg["kx_root"]))
        cmd = [
            ssh, "-i", cfg["ssh_key"], "-p", cfg["port"],
            "-o", "BatchMode=yes", "-o", "ConnectTimeout=15",
            "-o", "StrictHostKeyChecking=accept-new", remote, remote_command,
        ]
        shell = REMOTE_SHELL_TEMPLATE.replace("__DJANGO_INSPECTION__", REMOTE_DJANGO_INSPECTION)
        self._log("\n--- Inspection ciblée Ethikos / EkoH / Smart Vote ---\n")
        # Send raw UTF-8 bytes. On Windows, text=True may translate \n to \r\n
        # on stdin; those CRLF bytes break bash continuations/heredocs remotely.
        shell_bytes = shell.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
        completed = subprocess.run(
            cmd,
            input=shell_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=300,
            cwd=str(self.manager_dir),
        )
        output = (completed.stdout or b"").decode("utf-8", errors="replace")
        self._log(output)
        raw_path = report_dir / "ethikos-remote-inspection.txt"
        raw_path.write_text(output, encoding="utf-8")
        parsed = extract_marker_json(output, "KX_ETHIKOS_JSON_BEGIN", "KX_ETHIKOS_JSON_END")
        if parsed is None:
            return {"ok": False, "returncode": completed.returncode, "raw_output": str(raw_path), "error": "No structured Django inspection payload was returned."}
        parsed["returncode"] = completed.returncode
        parsed["raw_output"] = str(raw_path)
        return parsed

    def _probe_api(self, domain: str) -> list[dict[str, object]]:
        self._log("\n--- Probes API publiques ---\n")
        urls = [
            (f"https://{domain}/api/ethikos/topics/", True),
            (f"https://{domain}/api/ethikos/categories/", False),
            (f"https://{domain}/api/ethikos/stances/", True),
            (f"https://{domain}/api/ethikos/arguments/", True),
        ]
        rows = []
        context = ssl.create_default_context()
        for url, required in urls:
            row: dict[str, object] = {"url": url, "required": required}
            req = urllib.request.Request(url, headers={"User-Agent": "KX-Ethikos-Prod-Inspector/1"})
            try:
                with urllib.request.urlopen(req, timeout=15, context=context) as resp:
                    body = resp.read(800).decode("utf-8", errors="replace")
                    row.update({"status": resp.status, "content_type": resp.headers.get("Content-Type"), "body_head": body})
            except urllib.error.HTTPError as exc:
                body = exc.read(800).decode("utf-8", errors="replace")
                row.update({"status": exc.code, "error": str(exc), "body_head": body})
            except Exception as exc:
                row.update({"status": None, "error": f"{type(exc).__name__}: {exc}"})
            rows.append(row)
            self._log(f"{row.get('status')} {url}\n")
        return rows

    def _config(self) -> dict[str, str]:
        return {key: var.get().strip() for key, var in self.vars.items()}

    def _validate_config(self, cfg: dict[str, str], mode: str) -> None:
        for key in ("host", "domain", "instance", "user", "port", "ssh_key", "kx_root"):
            if not cfg[key]:
                raise ValueError(f"Champ requis: {key}")
        if not Path(cfg["ssh_key"]).exists():
            raise ValueError(f"Clé SSH introuvable: {cfg['ssh_key']}")
        if mode in {"full", "general"} and not Path(cfg["diagnostic"]).exists():
            raise ValueError(f"Diagnostic PowerShell introuvable: {cfg['diagnostic']}")
        try:
            int(cfg["port"])
        except ValueError as exc:
            raise ValueError("Le port SSH doit être numérique.") from exc
        find_ssh()
        if mode in {"full", "general"}:
            find_powershell()

    def _save_config(self) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        data = self._config()
        self.config_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def _load_config(self) -> None:
        try:
            data = json.loads(self.config_path.read_text(encoding="utf-8"))
        except Exception:
            return
        for key, value in data.items():
            if key in self.vars and isinstance(value, str):
                self.vars[key].set(value)

    def _poll_events(self) -> None:
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "log":
                    self.log.configure(state="normal")
                    self.log.insert("end", str(value))
                    self.log.see("end")
                    self.log.configure(state="disabled")
                elif kind == "done":
                    self.running = False
                    self._set_buttons(True)
                    self.status.set(f"Inspection terminée: {value}")
                elif kind == "failed":
                    self.running = False
                    self._set_buttons(True)
                    self.status.set("Inspection échouée. Voir le journal.")
        except queue.Empty:
            pass
        self.root.after(120, self._poll_events)

    def _set_buttons(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        for button in (self.full_button, self.deep_button, self.general_button):
            button.configure(state=state)

    def _open_latest_report(self) -> None:
        path = self.latest_report
        if path is None:
            candidates = sorted(self.report_root.glob("*/ethikos-prod-inspection.json"), reverse=True)
            path = candidates[0] if candidates else None
        if path is None or not path.exists():
            messagebox.showinfo(APP_TITLE, "Aucun rapport trouvé.")
            return
        try:
            if sys.platform.startswith("win"):
                os.startfile(path.parent)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path.parent)])
            else:
                subprocess.Popen(["xdg-open", str(path.parent)])
        except Exception as exc:
            messagebox.showerror(APP_TITLE, str(exc))


def find_ssh() -> str:
    path = shutil.which("ssh.exe") or shutil.which("ssh")
    if not path:
        raise RuntimeError("ssh.exe / ssh introuvable dans PATH.")
    return path


def find_powershell() -> str:
    path = shutil.which("powershell.exe") or shutil.which("pwsh.exe") or shutil.which("pwsh") or shutil.which("powershell")
    if not path:
        raise RuntimeError("PowerShell introuvable dans PATH.")
    return path


def run_streamed(cmd: list[str], emit, *, cwd: Path) -> tuple[int, str]:
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    chunks: list[str] = []
    assert proc.stdout is not None
    for line in proc.stdout:
        chunks.append(line)
        emit(line)
    return proc.wait(), "".join(chunks)


def extract_marker_json(text: str, begin: str, end: str) -> dict[str, object] | None:
    start = text.find(begin)
    if start < 0:
        return None
    start += len(begin)
    finish = text.find(end, start)
    if finish < 0:
        return None
    raw = text[start:finish].strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def assess_report(report: dict[str, object]) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    remote = report.get("remote")
    if not isinstance(remote, dict):
        remote = {}
        findings.append({"severity": "error", "code": "remote_missing", "message": "Inspection Django distante absente."})

    remote_ok = bool(remote.get("ok", False))
    if not remote_ok:
        rc = remote.get("returncode")
        detail = str(remote.get("error") or "Inspection distante indisponible.")
        findings.append({"severity": "error", "code": "remote_errors", "message": f"Inspection Django distante indisponible (rc={rc}): {detail}"})
        if int(rc or 0) == 255:
            findings.append({"severity": "error", "code": "ssh_auth_failed", "message": "La connexion SSH a échoué avant l'inspection Django. Vérifier l'utilisateur SSH, la clé autorisée et la politique de connexion non-root."})
        findings.append({"severity": "unknown", "code": "migrations_unknown", "message": "État des migrations inconnu : aucun payload Django distant n'a été obtenu."})
    else:
        migrations = remote.get("migrations") if isinstance(remote.get("migrations"), dict) else {}
        pending = int(migrations.get("all_pending_count") or migrations.get("pending_count") or 0)
        targeted_pending = int(migrations.get("pending_count") or 0)
        if pending:
            findings.append({"severity": "error", "code": "pending_migrations", "message": f"{pending} migration(s) Django sont en attente, dont {targeted_pending} ciblée(s) Ethikos/EkoH/Smart Vote."})
        else:
            findings.append({"severity": "ok", "code": "migrations_clear", "message": "Aucune migration Django en attente détectée."})

    users = remote.get("users") if remote_ok and isinstance(remote.get("users"), dict) else {}
    total_users = users.get("total")
    if total_users == 0:
        findings.append({"severity": "error", "code": "users_empty", "message": "La base de production ne contient aucun compte utilisateur."})

    ethikos = remote.get("ethikos") if remote_ok and isinstance(remote.get("ethikos"), dict) else {}
    topics = ethikos.get("topics")
    if topics == 0:
        findings.append({"severity": "error", "code": "ethikos_topics_empty", "message": "Aucun débat/topic Ethikos n'est présent en production."})
    elif isinstance(topics, int):
        findings.append({"severity": "ok", "code": "ethikos_topics_present", "message": f"{topics} topic(s) Ethikos détecté(s)."})

    ekoh = remote.get("ekoh") if remote_ok and isinstance(remote.get("ekoh"), dict) else {}
    if users.get("total") and ekoh.get("ethics_scores") == 0 and ekoh.get("expertise_scores") == 0:
        findings.append({"severity": "warning", "code": "ekoh_profiles_empty", "message": "Des utilisateurs existent mais aucun score EkoH n'a été trouvé."})

    smart = remote.get("smart_vote") if remote_ok and isinstance(remote.get("smart_vote"), dict) else {}
    if isinstance(topics, int) and topics > 0 and smart.get("source_bindings") == 0:
        findings.append({"severity": "warning", "code": "smart_vote_bindings_empty", "message": "Des topics Ethikos existent mais aucun binding Smart Vote n'a été trouvé."})
    readings = smart.get("readings") if isinstance(smart.get("readings"), list) else []
    reading_errors = [row for row in readings if isinstance(row, dict) and row.get("error")]
    if reading_errors:
        findings.append({"severity": "error", "code": "smart_vote_reading_errors", "message": f"{len(reading_errors)} lecture(s) Smart Vote lèvent une erreur runtime."})

    probes = report.get("api_probes") if isinstance(report.get("api_probes"), list) else []
    bad = [
        row for row in probes
        if isinstance(row, dict) and (
            row.get("status") is None
            or int(row.get("status") or 0) >= 500
            or (int(row.get("status") or 0) == 404 and bool(row.get("required", True)))
        )
    ]
    denied = [row for row in probes if isinstance(row, dict) and int(row.get("status") or 0) in {401, 403}]
    optional_missing = [row for row in probes if isinstance(row, dict) and int(row.get("status") or 0) == 404 and not bool(row.get("required", True))]
    empty_json_lists = [
        row for row in probes
        if isinstance(row, dict)
        and int(row.get("status") or 0) == 200
        and str(row.get("body_head") or "").strip() == "[]"
    ]
    if empty_json_lists:
        required_empty = sum(1 for row in empty_json_lists if bool(row.get("required", True)))
        severity = "error" if required_empty else "warning"
        findings.append({"severity": severity, "code": "api_collections_empty", "message": f"{len(empty_json_lists)} collection(s) API Ethikos répondent 200 mais sont vides; {required_empty} sont requises."})
    if bad:
        findings.append({"severity": "error", "code": "api_failure", "message": f"{len(bad)} probe(s) API requis ont échoué, retourné 404 ou 5xx."})
    if denied:
        findings.append({"severity": "warning", "code": "api_auth_required", "message": f"{len(denied)} probe(s) API demandent une authentification (401/403)."})
    if optional_missing:
        findings.append({"severity": "warning", "code": "optional_api_missing", "message": f"{len(optional_missing)} route(s) API optionnelle(s) ne sont pas enregistrées (404)."})
    return findings


def render_summary(report: dict[str, object]) -> str:
    target = report.get("target") if isinstance(report.get("target"), dict) else {}
    lines = [
        "Konnaxion Ethikos Production Inspection",
        f"Target: {target.get('domain')} / {target.get('instance_id')} @ {target.get('host')}",
        "",
        "Assessment:",
    ]
    for item in report.get("assessment", []):
        if isinstance(item, dict):
            lines.append(f"- [{str(item.get('severity', '')).upper()}] {item.get('code')}: {item.get('message')}")
    remote = report.get("remote") if isinstance(report.get("remote"), dict) else {}
    if remote and remote.get("ok", False):
        lines += [
            "",
            f"Users: {dict(remote.get('users') or {}).get('total')}",
            f"Ethikos topics: {dict(remote.get('ethikos') or {}).get('topics')}",
            f"Ethikos stances: {dict(remote.get('ethikos') or {}).get('stances')}",
            f"Ethikos arguments: {dict(remote.get('ethikos') or {}).get('arguments')}",
            f"EkoH expertise scores: {dict(remote.get('ekoh') or {}).get('expertise_scores')}",
            f"EkoH ethics scores: {dict(remote.get('ekoh') or {}).get('ethics_scores')}",
            f"Smart Vote bindings: {dict(remote.get('smart_vote') or {}).get('source_bindings')}",
            f"Pending Django migrations: {dict(remote.get('migrations') or {}).get('all_pending_count', dict(remote.get('migrations') or {}).get('pending_count'))}",
            f"Pending targeted migrations: {dict(remote.get('migrations') or {}).get('pending_count')}",
        ]
    elif remote:
        lines += [
            "",
            f"Remote inspection: UNAVAILABLE (rc={remote.get('returncode')})",
            f"Remote error: {remote.get('error')}",
            "Users / Ethikos / EkoH / Smart Vote / migrations: UNKNOWN",
        ]
    return "\n".join(lines) + "\n"


def main() -> None:
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except tk.TclError:
        pass
    InspectorApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
