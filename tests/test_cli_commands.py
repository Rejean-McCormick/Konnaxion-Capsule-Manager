"""
Tests for the canonical Konnaxion CLI command surface.

These tests intentionally verify naming alignment, not full command execution.
They prevent drift from the documented public operator CLI:

    kx capsule ...
    kx instance ...
    kx backup ...
    kx security ...
    kx network ...

The implementation under test may expose a Typer, Click, argparse, or custom
command tree, but it must remain aligned with ``PUBLIC_CLI_COMMANDS`` from
``kx_shared.konnaxion_constants``.
"""

from __future__ import annotations

import importlib
from collections.abc import Iterable

import pytest

from kx_shared.konnaxion_constants import CLI_NAME, PUBLIC_CLI_COMMANDS


EXPECTED_PUBLIC_COMMANDS = (
    "kx capsule build",
    "kx capsule verify",
    "kx capsule import",
    "kx artifact list",
    "kx artifact show",
    "kx artifact remove",
    "kx instance create",
    "kx instance start",
    "kx instance stop",
    "kx instance status",
    "kx instance logs",
    "kx instance backup",
    "kx instance restore",
    "kx instance restore-new",
    "kx instance update",
    "kx instance rollback",
    "kx instance health",
    "kx backup list",
    "kx backup verify",
    "kx backup test-restore",
    "kx security check",
    "kx network set-profile",
)

FORBIDDEN_COMMANDS = (
    "konnaxion capsule build",
    "konnaxion instance start",
    "manager capsule import",
    "launcher instance start",
    "daemon start",
    "kx deploy",
    "kx install",
    "kx docker",
    "kx compose",
    "kx db",
    "kx redis",
    "kx worker",
    "kx frontend",
    "kx backend",
)

INTERNAL_ONLY_COMMANDS = (
    "kx backup preflight",
    "kx backup postflight",
    "kx instance stop-services",
    "kx instance fix-permissions",
)


def test_cli_name_is_kx() -> None:
    """The canonical CLI executable name is exactly ``kx``."""

    assert CLI_NAME == "kx"


def test_public_cli_commands_match_canonical_tuple() -> None:
    """The shared constants must expose the exact public command surface."""

    assert tuple(PUBLIC_CLI_COMMANDS) == EXPECTED_PUBLIC_COMMANDS


@pytest.mark.parametrize("command", EXPECTED_PUBLIC_COMMANDS)
def test_public_commands_start_with_kx(command: str) -> None:
    """Every public command must use the canonical CLI root."""

    assert command.startswith("kx ")


@pytest.mark.parametrize("command", EXPECTED_PUBLIC_COMMANDS)
def test_public_commands_have_valid_group(command: str) -> None:
    """Every public command must belong to an approved command group."""

    parts = command.split()
    assert parts[0] == "kx"
    assert parts[1] in {"capsule", "artifact", "instance", "backup", "security", "network"}


@pytest.mark.parametrize("command", EXPECTED_PUBLIC_COMMANDS)
def test_public_commands_are_lowercase_kebab_case(command: str) -> None:
    """Commands should be stable lowercase/kebab-case strings."""

    assert command == command.lower()

    for part in command.split():
        assert "_" not in part
        assert "." not in part


@pytest.mark.parametrize("command", FORBIDDEN_COMMANDS)
def test_forbidden_commands_are_not_public(command: str) -> None:
    """Legacy or implementation-specific aliases must not be public commands."""

    assert command not in PUBLIC_CLI_COMMANDS


@pytest.mark.parametrize("command", INTERNAL_ONLY_COMMANDS)
def test_internal_only_commands_are_not_public(command: str) -> None:
    """Agent-only/internal operations must not appear in the public CLI list."""

    assert command not in PUBLIC_CLI_COMMANDS


def test_cli_module_imports() -> None:
    """The CLI module should import without side effects."""

    module = importlib.import_module("kx_cli.main")
    assert module is not None


def test_cli_module_exposes_app_or_main() -> None:
    """The CLI entry module must expose a callable/app entrypoint."""

    module = importlib.import_module("kx_cli.main")

    has_entrypoint = any(
        hasattr(module, name)
        for name in (
            "app",       # Typer/common
            "cli",       # Click/common
            "main",      # argparse/custom
            "run",       # custom
        )
    )

    assert has_entrypoint, "kx_cli.main must expose app, cli, main, or run"


def test_cli_commands_are_unique() -> None:
    """Duplicate command strings create ambiguous help output."""

    assert len(PUBLIC_CLI_COMMANDS) == len(set(PUBLIC_CLI_COMMANDS))


def test_cli_commands_are_sorted_by_canonical_group_order() -> None:
    """Keep the command list stable for docs, tests, and UI generation."""

    group_order = {
        "capsule": 0,
        "artifact": 1,
        "instance": 2,
        "backup": 3,
        "security": 4,
        "network": 5,
    }

    def sort_key(command: str) -> tuple[int, int]:
        parts = command.split()
        group = parts[1]
        return (group_order[group], EXPECTED_PUBLIC_COMMANDS.index(command))

    assert tuple(PUBLIC_CLI_COMMANDS) == tuple(sorted(PUBLIC_CLI_COMMANDS, key=sort_key))


def test_expected_public_commands_do_not_include_service_aliases() -> None:
    """Public operator commands should not leak internal service aliases."""

    forbidden_tokens = {
        "api",
        "backend",
        "cache",
        "database",
        "db",
        "frontend",
        "next",
        "web",
        "worker",
    }

    for command in PUBLIC_CLI_COMMANDS:
        tokens = set(command.split())
        assert tokens.isdisjoint(forbidden_tokens), command


def test_public_commands_can_generate_help_sections() -> None:
    """The canonical list should be directly usable for help/docs generation."""

    sections = _group_commands(PUBLIC_CLI_COMMANDS)

    assert tuple(sections) == ("capsule", "artifact", "instance", "backup", "security", "network")
    assert sections["capsule"] == (
        "build",
        "verify",
        "import",
    )
    assert sections["artifact"] == (
        "list",
        "show",
        "remove",
    )
    assert sections["instance"] == (
        "create",
        "start",
        "stop",
        "status",
        "logs",
        "backup",
        "restore",
        "restore-new",
        "update",
        "rollback",
        "health",
    )
    assert sections["backup"] == (
        "list",
        "verify",
        "test-restore",
    )
    assert sections["security"] == ("check",)
    assert sections["network"] == ("set-profile",)


def _group_commands(commands: Iterable[str]) -> dict[str, tuple[str, ...]]:
    """Group canonical command strings by their first command group."""

    grouped: dict[str, list[str]] = {}

    for command in commands:
        parts = command.split()
        assert len(parts) >= 3, f"Invalid command shape: {command!r}"
        root, group, *subcommand_parts = parts
        assert root == "kx"

        grouped.setdefault(group, []).append(" ".join(subcommand_parts))

    return {group: tuple(items) for group, items in grouped.items()}
