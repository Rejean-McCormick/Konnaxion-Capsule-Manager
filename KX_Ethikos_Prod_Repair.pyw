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


class RepairApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("1120x800")
        self.manager_dir = Path(__file__).resolve().parent
        self.report_root = self.manager_dir / "diagnostics" / "ethikos-prod"
        self.repair_root = self.manager_dir / "diagnostics" / "ethikos-prod-repairs"
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.running = False
        self.report: dict[str, object] | None = None
        self.last_preview: dict[str, object] | None = None
        self.last_preview_signature: tuple[str, str] | None = None

        self.report_path = tk.StringVar(value=self._latest_report_path())
        self.scenario_path = tk.StringVar(value="")
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
                if mode == "scenario_preview":
                    self.last_preview = result if isinstance(result, dict) else None
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
        for button in (self.validate_button, self.migrate_button, self.import_button):
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
    try:
        parsed = json.loads(text[start:finish].strip())
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


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
