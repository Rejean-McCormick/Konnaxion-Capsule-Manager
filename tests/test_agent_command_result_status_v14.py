from kx_agent.actions import _command_result_ok, _object_to_mapping
from kx_agent.runtime.docker import CommandResult


def test_command_result_nonzero_is_not_success_when_asdict_omits_ok_property() -> None:
    result = CommandResult(args=("docker", "compose", "up"), returncode=1)
    data = _object_to_mapping(result)

    assert "ok" not in data
    assert _command_result_ok(result, data) is False


def test_command_result_zero_is_success() -> None:
    result = CommandResult(args=("docker", "compose", "up"), returncode=0)
    assert _command_result_ok(result, _object_to_mapping(result)) is True


def test_command_result_timeout_is_not_success() -> None:
    result = CommandResult(args=("docker", "compose", "up"), returncode=0, timed_out=True)
    assert _command_result_ok(result, _object_to_mapping(result)) is False
