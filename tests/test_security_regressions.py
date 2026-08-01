"""Regression coverage for the security hardening changes."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from james.core.command_policy import parse_safe_command
from james.core.scheduler import Scheduler
from james.tools.forge_tools import (
    _GENERATED_SKILL_HEADER,
    _persist_skill,
    _validate_skill_ast,
    configure_forge,
    load_generated_skill_source,
)
from james.tools.registry import ToolRegistry, is_dangerous_tool_call


def test_scheduler_is_operational_and_rejects_arbitrary_commands(isolated_workspace: Path) -> None:
    scheduler = Scheduler(isolated_workspace / "schedule.json")
    assert hasattr(scheduler, "start")
    assert hasattr(scheduler, "stop")
    with pytest.raises(ValueError, match="read-only command allowlist"):
        scheduler.add(datetime.now(), command="rm -rf /")
    job_id = scheduler.add(datetime.now(), command="python --version")
    assert job_id


@pytest.mark.parametrize("command", ["python -c print", "env rm -rf /", "find . -delete"])
def test_command_policy_rejects_interpreter_and_mutating_bypasses(command: str) -> None:
    args, reason = parse_safe_command(command)
    assert args is None
    assert reason


def test_scheduled_commands_are_dangerous_only_when_they_execute() -> None:
    assert is_dangerous_tool_call("schedule_task", {"message": "hello"}) is False
    assert is_dangerous_tool_call("schedule_task", {"command": "python --version"}) is True


def test_generated_skills_reject_reflection_escape() -> None:
    code = """
from james.tools.base import tool

@tool("bad", "bad", {})
def bad():
    return ().__class__
"""
    issues = _validate_skill_ast(code)
    assert any("Attribute is not allowed" in issue for issue in issues)


def test_generated_skills_load_only_constrained_code(isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from james.tools import forge_tools

    monkeypatch.setattr(forge_tools, "_PLUGINS_DIR", isolated_workspace / "plugins")
    registry = ToolRegistry(tools=[], discover_plugins=False)
    configure_forge(registry)
    code = """
from james.tools.base import tool, ToolResult

@tool("double_number", "Double a number.", {"value": {"type": "integer"}}, required=["value"])
def double_number(value):
    return ToolResult(ok=True, output=str(value * 2))
"""
    module = load_generated_skill_source(code)
    assert module.double_number.run(value=4).output == "8"

    result = _persist_skill("double_number", code)
    assert result.ok
    saved = isolated_workspace / "plugins" / "double_number.py"
    assert saved.read_text(encoding="utf-8").startswith(_GENERATED_SKILL_HEADER)
    assert registry.execute("double_number", {"value": 5}).output == "10"


def test_history_cipher_is_authenticated(isolated_workspace: Path) -> None:
    from james.core.assistant import decrypt_history, encrypt_history

    encrypted = encrypt_history([{"role": "user", "content": "confidential"}])
    assert b"confidential" not in encrypted
    tampered = encrypted[:-1] + bytes([encrypted[-1] ^ 1])
    assert decrypt_history(tampered) == []


def test_audit_key_is_random_and_persisted(isolated_workspace: Path) -> None:
    from james.tools.registry import _audit_hmac_key

    first = _audit_hmac_key()
    second = _audit_hmac_key()
    assert first == second
    assert first != b"james-audit-secret-change-me-in-production"
    assert (isolated_workspace / ".james_audit_hmac.key").exists()
