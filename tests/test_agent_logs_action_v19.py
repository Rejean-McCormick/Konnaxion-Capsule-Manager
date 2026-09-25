from __future__ import annotations

from kx_agent import actions
from kx_agent.runtime.docker import CommandResult


def test_instance_logs_handler_adapts_public_fields_to_runtime_api(monkeypatch) -> None:
    import kx_agent.runtime.docker as docker_runtime

    observed: dict[str, object] = {}

    class Runtime:
        def logs(self, services=None, *, lines=200, timestamps=True):
            observed["services"] = services
            observed["lines"] = lines
            observed["timestamps"] = timestamps
            return CommandResult(
                args=("docker", "compose", "logs"),
                returncode=0,
                stdout="hello\n",
                stderr="",
                timed_out=False,
            )

    monkeypatch.setattr(docker_runtime, "runtime_for_instance", lambda instance_id: Runtime())

    request = actions.ActionRequest(
        action=actions.AgentActionName.INSTANCE_LOGS.value,
        params={
            "instance_id": "konnaxion-prod",
            "service": "django-api",
            "tail": 123,
        },
    )
    result = actions.handle_instance_logs(request)

    assert result.status == actions.ActionStatus.SUCCEEDED
    assert observed == {
        "services": ["django-api"],
        "lines": 123,
        "timestamps": True,
    }
    assert result.data["instance_id"] == "konnaxion-prod"
    assert result.data["service"] == "django-api"
    assert result.data["tail"] == 123
    assert result.data["stdout"] == "hello\n"


def test_instance_logs_handler_all_services_when_service_omitted(monkeypatch) -> None:
    import kx_agent.runtime.docker as docker_runtime

    observed: dict[str, object] = {}

    class Runtime:
        def logs(self, services=None, *, lines=200, timestamps=True):
            observed["services"] = services
            observed["lines"] = lines
            return CommandResult(
                args=("docker", "compose", "logs"),
                returncode=0,
                stdout="all logs\n",
                stderr="",
                timed_out=False,
            )

    monkeypatch.setattr(docker_runtime, "runtime_for_instance", lambda instance_id: Runtime())

    request = actions.ActionRequest(
        action=actions.AgentActionName.INSTANCE_LOGS.value,
        params={"instance_id": "konnaxion-prod", "tail": 50},
    )
    result = actions.handle_instance_logs(request)

    assert result.status == actions.ActionStatus.SUCCEEDED
    assert observed == {"services": None, "lines": 50}
