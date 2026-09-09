"""Regression tests for local/custom KX_ROOT runtime paths."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import textwrap


def _run_isolated(script: str, *, runtime_root: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["KX_ROOT"] = str(runtime_root)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script)],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def test_runtime_compose_roots_follow_kx_root(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    cp = _run_isolated(
        """
        from pathlib import Path
        import os
        from kx_shared.konnaxion_constants import KX_ROOT
        from kx_agent.runtime.compose import KONNAXION_ROOT, INSTANCES_ROOT, SHARED_CAPSULES_ROOT

        root = Path(os.environ["KX_ROOT"])
        assert Path(KX_ROOT) == root
        assert KONNAXION_ROOT == root
        assert INSTANCES_ROOT == root / "instances"
        assert SHARED_CAPSULES_ROOT == root / "shared" / "capsules"
        """,
        runtime_root=runtime,
    )
    assert cp.returncode == 0, cp.stderr


def test_write_runtime_compose_stays_under_custom_kx_root(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    cp = _run_isolated(
        """
        from pathlib import Path
        import os
        from kx_agent.runtime.compose import ComposeRenderOptions, write_runtime_compose

        root = Path(os.environ["KX_ROOT"])
        capsule_id = "konnaxion-v14-local-test"
        manifest = root / "shared" / "capsules" / capsule_id / "manifest.yaml"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(
            "schema_version: kx-capsule-manifest/v1\\n"
            "capsule_id: konnaxion-v14-local-test\\n"
            "capsule_version: test\\n"
            "app_name: Konnaxion\\n"
            "app_version: v14\\n"
            "channel: local\\n"
            "runtime:\\n  images: []\\n",
            encoding="utf-8",
        )

        result = write_runtime_compose(
            ComposeRenderOptions(
                instance_id="demo-001",
                host="127.0.0.1",
                capsule_id=capsule_id,
                network_profile="local_only",
                exposure_mode="private",
                ensure_env_files=False,
            )
        )
        expected = root / "instances" / "demo-001" / "state" / "docker-compose.runtime.yml"
        assert Path(result.compose_file) == expected
        assert expected.is_file()
        """,
        runtime_root=runtime,
    )
    assert cp.returncode == 0, cp.stderr
