"""Shared validation for commands executed without a shell."""
from __future__ import annotations

import os
import re
import shlex
from pathlib import Path
from typing import Optional

_SHELL_METACHAR_RE = re.compile(r"[;&|`$(){}[\]<>!#\r\n]")

# These programs are limited to operations that inspect data or report system
# state. Utilities with write/execute modes (find, sed, awk, env, curl, wget)
# are deliberately excluded.
_READ_ONLY_COMMANDS = {
    "cat",
    "comm",
    "date",
    "df",
    "diff",
    "dir",
    "du",
    "echo",
    "file",
    "grep",
    "head",
    "hostname",
    "id",
    "ifconfig",
    "ip",
    "ipconfig",
    "ls",
    "nslookup",
    "paste",
    "ping",
    "printenv",
    "pwd",
    "sort",
    "stat",
    "tail",
    "tr",
    "type",
    "uname",
    "uniq",
    "uptime",
    "wc",
    "where",
    "whereis",
    "which",
    "whoami",
}
_VERSION_ONLY_COMMANDS = {"node", "perl", "python", "python3", "ruby"}
_VERSION_FLAGS = {("--version",), ("-v",), ("-V",)}


def parse_safe_command(command: str) -> tuple[Optional[list[str]], str]:
    command = (command or "").strip()
    if not command:
        return None, "Command is empty."
    if _SHELL_METACHAR_RE.search(command):
        return None, "Command contains shell metacharacters."
    try:
        args = shlex.split(command, posix=os.name != "nt")
    except ValueError as exc:
        return None, f"Command could not be parsed: {exc}"
    if not args:
        return None, "Command is empty."

    executable = Path(args[0].strip('"')).name.lower()
    if executable.endswith(".exe"):
        executable = executable[:-4]

    if executable in _VERSION_ONLY_COMMANDS:
        if tuple(args[1:]) not in _VERSION_FLAGS:
            return None, f"{executable} is allowed only with a version flag."
        return args, ""
    if executable not in _READ_ONLY_COMMANDS:
        return None, f"Executable '{executable}' is not in the read-only allowlist."
    return args, ""


def is_safe_command(command: str) -> bool:
    args, _ = parse_safe_command(command)
    return args is not None
