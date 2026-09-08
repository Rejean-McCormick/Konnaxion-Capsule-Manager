from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


BROWSER_HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def test_build_job_store_is_created_under_runtime_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KX_ROOT", str(tmp_path / "runtime"))
    monkeypatch.delenv("KX_CAPSULE_BUILD_JOB_DIR", raising=False)

    from kx_manager.services import build_jobs

    path = build_jobs.ensure_build_job_store()

    assert path == tmp_path / "runtime" / "manager" / "build-jobs"
    assert path.is_dir()
    assert (path / "README.txt").is_file()


def test_create_build_job_immediately_creates_json_and_log(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KX_ROOT", str(tmp_path / "runtime"))
    monkeypatch.delenv("KX_CAPSULE_BUILD_JOB_DIR", raising=False)

    from kx_manager.services import build_jobs

    class FakeExecutor:
        def submit(self, *args: Any, **kwargs: Any) -> None:
            return None

    monkeypatch.setattr(build_jobs, "_executor_for_config", lambda: FakeExecutor())

    job = build_jobs.create_build_job(
        "build_capsule",
        {
            "capsule_id": "konnaxion-v14-local-2026.09.08",
            "capsule_version": "2026.09.08-local.1",
            "source_dir": str(tmp_path / "source"),
            "capsule_output_dir": str(tmp_path / "runtime" / "capsules"),
            "network_profile": "local_only",
        },
    )

    job_file = Path(str(job["job_file"]))
    log_file = Path(str(job["log_file"]))

    assert job_file.is_file()
    assert log_file.is_file()
    assert (job_file.parent / "latest-job.txt").read_text(encoding="utf-8").strip() == job["job_id"]
    persisted = json.loads(job_file.read_text(encoding="utf-8"))
    assert persisted["status"] == "queued"
    assert "queued:" in log_file.read_text(encoding="utf-8")


def test_build_job_page_renders_progress_and_log_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KX_ROOT", str(tmp_path / "runtime"))
    monkeypatch.delenv("KX_CAPSULE_BUILD_JOB_DIR", raising=False)

    from kx_manager.services import build_jobs
    from kx_manager.ui import app as ui_app

    class FakeExecutor:
        def submit(self, *args: Any, **kwargs: Any) -> None:
            return None

    monkeypatch.setattr(build_jobs, "_executor_for_config", lambda: FakeExecutor())
    job = build_jobs.create_build_job(
        "build_capsule",
        {
            "capsule_id": "konnaxion-v14-local-2026.09.08",
            "capsule_version": "2026.09.08-local.1",
            "source_dir": str(tmp_path / "source"),
            "capsule_output_dir": str(tmp_path / "runtime" / "capsules"),
            "network_profile": "local_only",
        },
    )

    app = FastAPI()
    ui_app.register(app)
    client = TestClient(app)
    response = client.get(f"/ui/build-jobs/{job['job_id']}", headers=BROWSER_HEADERS)

    assert response.status_code == 200
    assert "Capsule Build Progress" in response.text
    assert "1%" in response.text
    assert "Live Build Log" in response.text
    assert str(job["log_file"]) in response.text
    assert 'http-equiv="refresh"' in response.text


def test_build_action_redirects_to_progress_page(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KX_ROOT", str(tmp_path / "runtime"))

    from kx_manager.ui import app as ui_app

    async def fake_dispatch(action: Any, payload: Any = None) -> dict[str, Any]:
        return {
            "ok": True,
            "action": "build_capsule",
            "message": "Capsule build queued.",
            "instance_id": None,
            "data": {"build_job_id": "job-123"},
            "stdout": None,
            "stderr": None,
            "returncode": None,
        }

    monkeypatch.setattr(ui_app, "dispatch_gui_action", fake_dispatch, raising=False)
    monkeypatch.setattr(ui_app, "_validated_payload", lambda action, payload: dict(payload), raising=True)

    app = FastAPI()
    ui_app.register(app)
    client = TestClient(app, follow_redirects=False)
    response = client.post(
        "/ui/actions/build-capsule",
        data={"capsule_id": "demo"},
        headers=BROWSER_HEADERS,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/ui/build-jobs/job-123"


def test_run_job_finishes_and_persists_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KX_ROOT", str(tmp_path / "runtime"))
    monkeypatch.delenv("KX_CAPSULE_BUILD_JOB_DIR", raising=False)

    from kx_manager.services import build_jobs, builder

    class FakeExecutor:
        def submit(self, *args: Any, **kwargs: Any) -> None:
            return None

    class FakeResult:
        ok = True

        def to_dict(self) -> dict[str, Any]:
            return {"ok": True, "capsule_file": str(tmp_path / "runtime" / "capsules" / "demo.kxcap")}

    monkeypatch.setattr(build_jobs, "_executor_for_config", lambda: FakeExecutor())
    monkeypatch.setattr(build_jobs, "_docker_preflight", lambda: (True, "Docker engine ready."))
    monkeypatch.setattr(builder, "build_capsule", lambda request: FakeResult())

    job = build_jobs.create_build_job(
        "build_capsule",
        {
            "capsule_id": "demo",
            "capsule_version": "1.0-local.1",
            "source_dir": str(tmp_path / "source"),
            "capsule_output_dir": str(tmp_path / "runtime" / "capsules"),
            "network_profile": "local_only",
        },
    )
    build_jobs._run_job(job["job_id"], "build_capsule", {
        "capsule_id": "demo",
        "capsule_version": "1.0-local.1",
        "source_dir": str(tmp_path / "source"),
        "capsule_output_dir": str(tmp_path / "runtime" / "capsules"),
        "network_profile": "local_only",
    })

    finished = build_jobs.get_build_job(job["job_id"])
    assert finished is not None
    assert finished["status"] == "succeeded"
    assert finished["progress"] == 100
    assert "Docker engine ready" in finished["log_tail"]
    assert "capsule build succeeded" in finished["log_tail"]


def test_builder_progress_event_writes_sidecar_and_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log_file = tmp_path / "job.log"
    progress_file = tmp_path / "job.progress.json"
    monkeypatch.setenv("KX_BUILD_JOB_LOG_FILE", str(log_file))
    monkeypatch.setenv("KX_BUILD_PROGRESS_FILE", str(progress_file))

    from kx_builder import package

    package._build_progress_event("backend-image", 25, "Building backend image.")

    assert log_file.is_file()
    assert "backend-image: Building backend image." in log_file.read_text(encoding="utf-8")
    payload = json.loads(progress_file.read_text(encoding="utf-8"))
    assert payload["phase"] == "backend-image"
    assert payload["progress"] == 25


def test_build_action_backend_queues_job_instead_of_running_builder(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio
    from kx_manager.services import build_jobs
    from kx_manager.ui import action_backends

    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_create(action: str, payload: Any) -> dict[str, Any]:
        calls.append((action, dict(payload)))
        return {
            "job_id": "job-queued-1",
            "status": "queued",
            "phase": "queued",
            "progress": 1,
            "log_file": r"C:\mycode\Konnaxion\runtime\manager\build-jobs\job-queued-1.log",
            "job_file": r"C:\mycode\Konnaxion\runtime\manager\build-jobs\job-queued-1.json",
        }

    monkeypatch.setattr(build_jobs, "create_build_job", fake_create)
    result = asyncio.run(
        action_backends._handle_build_capsule(
            "build_capsule",
            {"capsule_id": "demo", "network_profile": "local_only"},
        )
    )

    assert result.ok is True
    assert result.message == "Capsule build queued."
    assert result.data["build_job_id"] == "job-queued-1"
    assert calls == [("build_capsule", {"capsule_id": "demo", "network_profile": "local_only"})]


def test_package_capsule_reports_package_compress_and_digest_phases(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kx_builder import package

    staging = tmp_path / "staging"
    staging.mkdir()
    output = tmp_path / "demo.kxcap"
    events: list[tuple[str, int, str]] = []

    monkeypatch.setattr(
        package,
        "validate_staging_dir",
        lambda root, options=None: package.PackageValidationResult(ok=True),
    )
    monkeypatch.setattr(package, "raise_if_invalid", lambda result: None)

    def fake_tar(root: Path, tar_path: Path, *, deterministic: bool = True) -> Path:
        Path(tar_path).write_bytes(b"tar")
        return Path(tar_path)

    def fake_compress(tar_path: Path, output_path: Path, *, level: int) -> Path:
        Path(output_path).write_bytes(b"kxcap")
        return Path(output_path)

    monkeypatch.setattr(package, "create_tar_archive", fake_tar)
    monkeypatch.setattr(package, "compress_tar_to_kxcap", fake_compress)
    monkeypatch.setattr(package, "sha256_file", lambda path: "a" * 64)
    monkeypatch.setattr(
        package,
        "_build_progress_event",
        lambda phase, progress, message: events.append((phase, progress, message)),
    )

    result = package.package_capsule(
        staging,
        output,
        options=package.PackageOptions(
            overwrite=True,
            include_package_metadata=False,
            strict_root=False,
            scan_for_secrets=False,
        ),
    )

    assert result.capsule_file == output
    assert [phase for phase, _progress, _message in events] == [
        "package",
        "compress",
        "digest",
    ]
    assert events[0][1] == 92
    assert events[1][1] == 94
    assert events[2][1] == 95
    assert "zstd level" in events[1][2]
