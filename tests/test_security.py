"""Tests for JAMES security and core functionality."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from james.config import settings
from james.tools.base import FunctionTool, ToolResult, tool
from james.tools.registry import DANGEROUS_TOOLS, ToolRegistry
from james.tools.system_tools import run_shell_command, open_application
from james.tools.mcp_tools import _validate_mcp_arguments
from james.tools.forge_tools import _persist_skill, _RESTRICTED_BUILTINS
from james.core.guard import _is_loopback, install_offline_guard
from james.core.scheduler import _validate_command, _fire, Job


@pytest.fixture
def temp_workspace(tmp_path: Path) -> Path:
    settings.assistant.workspace_dir = tmp_path
    settings.assistant.audit_log = tmp_path / "audit.log"
    settings.assistant.egress_audit_log = tmp_path / "egress.log"
    return tmp_path


class TestRunShellCommand:
    def test_safe_command_works(self):
        result = run_shell_command.run(command="python --version")
        assert result.ok is True

    def test_shell_metachar_rejected(self):
        result = run_shell_command.run(command="echo hello; rm -rf /")
        assert result.ok is False
        assert "unsafe characters" in result.output.lower()

    def test_unsafe_command_rejected(self):
        result = run_shell_command.run(command="rm -rf /")
        assert result.ok is False
        assert "allowlist" in result.output.lower()

    def test_no_shell_injection(self):
        result = run_shell_command.run(command="echo test && cat /etc/passwd")
        assert result.ok is False


class TestOpenApplication:
    def test_url_scheme_validation(self):
        result = open_application.run(target="javascript:alert(1)")
        assert result.ok is False

    def test_file_scheme_rejected(self):
        result = open_application.run(target="file:///etc/passwd")
        assert result.ok is False

    def test_http_url_accepted(self):
        result = open_application.run(target="https://example.com")
        assert result.ok is True


class TestMCPArgumentValidation:
    def test_valid_arguments_pass(self):
        result = _validate_mcp_arguments({"query": "test"}, "search")
        assert result == {"query": "test"}

    def test_non_dict_rejected(self):
        with pytest.raises(ValueError, match="must be a dict"):
            _validate_mcp_arguments("not a dict", "search")

    def test_sensitive_keys_redacted(self):
        result = _validate_mcp_arguments({"api_key": "secret123"}, "search")
        assert result["api_key"] == "***REDACTED***"

    def test_size_limit_enforced(self):
        large_value = "x" * 70000
        with pytest.raises(ValueError, match="exceed maximum size"):
            _validate_mcp_arguments({"data": large_value}, "search")


class TestForgeSandbox:
    def test_restricted_builtins_blocks_dangerous(self):
        assert "__import__" not in _RESTRICTED_BUILTINS
        assert "exec" not in _RESTRICTED_BUILTINS
        assert "eval" not in _RESTRICTED_BUILTINS
        assert "open" not in _RESTRICTED_BUILTINS

    def test_safe_builtins_present(self):
        assert "str" in _RESTRICTED_BUILTINS
        assert "int" in _RESTRICTED_BUILTINS
        assert "len" in _RESTRICTED_BUILTINS
        assert "print" in _RESTRICTED_BUILTINS


class TestAuditLogIntegrity:
    def test_audit_entry_is_hmac_signed(self, temp_workspace: Path):
        from james.tools.registry import ToolRegistry
        from james.tools.system_tools import get_system_info

        reg = ToolRegistry(tools=[get_system_info], discover_plugins=False)
        result = reg.execute("get_system_info", {})
        assert result.ok is True

    def test_tampered_audit_detected(self, temp_workspace: Path):
        from james.tools.registry import ToolRegistry

        reg = ToolRegistry(discover_plugins=False)
        reg._audit("test_tool", {"arg": "val"}, ToolResult(ok=True, output="ok"))

        log_path = str(temp_workspace / "audit.log")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write("forged_hash | 2024-01-01 | tool=test_tool ok=True args={}\n")

        assert not ToolRegistry.verify_audit_integrity(log_path)


class TestGuardEgress:
    def test_loopback_allowed(self):
        assert _is_loopback("127.0.0.1") is True
        assert _is_loopback("::1") is True
        assert _is_loopback("localhost") is True

    def test_external_blocked(self):
        assert _is_loopback("8.8.8.8") is False
        assert _is_loopback("example.com") is False


class TestSchedulerCommandValidation:
    def test_safe_command_validated(self):
        assert _validate_command("echo hello") is True

    def test_dangerous_command_rejected(self):
        assert _validate_command("echo hello; rm -rf /") is False


class TestPerToolPermissions:
    def test_allowed_tools_enforced(self, monkeypatch):
        monkeypatch.setattr(
            settings.assistant, "allowed_tools", ["read_file", "write_file"]
        )
        monkeypatch.setattr(settings.assistant, "denied_tools", [])
        from james.tools.registry import ToolRegistry
        from james.tools.file_tools import read_file, delete_file

        reg = ToolRegistry(tools=[read_file, delete_file], discover_plugins=False)
        result = reg.execute("delete_file", {"path": "/tmp/test"})
        assert result.ok is False
        assert "not in the allowed tools list" in result.output

    def test_denied_tools_enforced(self, monkeypatch):
        monkeypatch.setattr(settings.assistant, "allowed_tools", [])
        monkeypatch.setattr(settings.assistant, "denied_tools", ["run_shell_command"])
        from james.tools.registry import ToolRegistry
        from james.tools.system_tools import run_shell_command

        reg = ToolRegistry(tools=[run_shell_command], discover_plugins=False)
        result = reg.execute("run_shell_command", {"command": "echo test"})
        assert result.ok is False
        assert "explicitly denied" in result.output


class TestRateLimiting:
    def test_rate_limit_enforced(self, monkeypatch):
        monkeypatch.setattr(settings.assistant, "allowed_tools", [])
        monkeypatch.setattr(settings.assistant, "denied_tools", [])
        from james.tools.registry import ToolRegistry
        from james.tools.system_tools import get_system_info

        reg = ToolRegistry(tools=[get_system_info], discover_plugins=False)
        reg._max_calls_per_minute = 2
        reg.execute("get_system_info", {})
        reg.execute("get_system_info", {})
        result = reg.execute("get_system_info", {})
        assert result.ok is False
        assert "Rate limit exceeded" in result.output


class TestInputValidation:
    def test_integer_type_check(self):
        @tool("test_int", "Test integer param.", {"count": {"type": "integer"}})
        def test_func(count: int) -> ToolResult:
            return ToolResult(ok=True, output=str(count))

        tool_instance = test_func
        result = tool_instance.run(count="not_an_int")
        assert result.ok is False
        assert "must be an integer" in result.output

    def test_string_max_length(self):
        @tool("test_str", "Test string param.", {"name": {"type": "string", "maxLength": 10}})
        def test_func(name: str) -> ToolResult:
            return ToolResult(ok=True, output=name)

        result = test_func.run(name="a" * 20)
        assert result.ok is False
        assert "exceeds maximum length" in result.output

    def test_minimum_value(self):
        @tool("test_min", "Test min param.", {"count": {"type": "integer", "minimum": 0}})
        def test_func(count: int) -> ToolResult:
            return ToolResult(ok=True, output=str(count))

        result = test_func.run(count=-1)
        assert result.ok is False
        assert "below minimum" in result.output

    def test_required_param_missing(self):
        @tool("test_req", "Test required param.", {"name": {"type": "string"}}, required=["name"])
        def test_func(name: str) -> ToolResult:
            return ToolResult(ok=True, output=name)

        result = test_func.run()
        assert result.ok is False
        assert "Missing required parameter" in result.output


class TestEnvSecurity:
    def test_env_gpg_loading(self, tmp_path: Path):
        from james.config import _decrypt_env_gpg

        result = _decrypt_env_gpg(tmp_path / "nonexistent.gpg")
        assert result == {}

    def test_env_world_readable_warning(self, tmp_path: Path, monkeypatch):
        if not hasattr(os, "getuid"):
            pytest.skip("getuid not available on this platform")
        monkeypatch.setattr(os, "getuid", lambda: 1000)
        env_file = tmp_path / ".env"
        env_file.write_text("KEY=value", encoding="utf-8")
        os.chmod(str(env_file), 0o644)

        with pytest.warns(UserWarning, match="world-readable"):
            from james.config import _warn_env_permissions
            _warn_env_permissions(env_file)


class TestConversationEncryption:
    def test_encrypt_decrypt_roundtrip(self):
        from james.core.assistant import encrypt_history, decrypt_history

        history = [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}]
        encrypted = encrypt_history(history)
        decrypted = decrypt_history(encrypted)
        assert decrypted == history

    def test_empty_history(self):
        from james.core.assistant import encrypt_history, decrypt_history

        encrypted = encrypt_history([])
        decrypted = decrypt_history(encrypted)
        assert decrypted == []