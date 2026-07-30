# Security model

## Scope

JAMES is an LLM-powered desktop agent. LLM output, web content, MCP responses, and third-party plugins may be hostile or incorrect. Treat agentic automation as high-risk unless it is constrained and reviewed.

JAMES is not a container, virtual machine, or operating-system sandbox.

## Safe defaults

The default configuration is intentionally conservative:

```dotenv
JAMES_MODE=standard
CONFIRM_DANGEROUS_ACTIONS=true
AUTO_SKILL=false
ENABLE_TRUSTED_EXTERNAL_PLUGINS=false
```

- `standard` mode blocks known system-mutating tools.
- Non-interactive confirmation requests deny by default.
- `DRY_RUN=true` reports dangerous calls without executing them.
- `ALLOWED_TOOLS` can restrict JAMES to an explicit list of tools.

## Process and command safety

JAMES does not use a command shell for TTS playback, scheduled commands, or the shell-command tool.

`run_shell_command` and scheduled commands share a restrictive policy that permits only selected read-only tools. Dynamic interpreter execution, command chaining, shell metacharacters, and mutating utility modes are rejected.

This policy is defense in depth; use `standard` mode and `ALLOWED_TOOLS` when possible.

## Plugins and generated skills

Generated Skill Forge code is not treated as a normal Python plugin. It is parsed and constrained before writing and every time it is loaded. It cannot use imports other than JAMES metadata imports, attributes, reflection, loops, classes, or dynamic execution.

Ordinary Python files in `./plugins/` are trusted code and can run arbitrary code. JAMES does not load them unless `ENABLE_TRUSTED_EXTERNAL_PLUGINS=true` is set. Review source code before enabling that setting.

The current constrained skill runtime is not a substitute for process isolation. Future releases should run high-risk extension code in a separate, capability-limited process or container.

## Secrets, history, and audit logs

- Audit records are signed with an HMAC key generated per workspace. Set `JAMES_AUDIT_HMAC_KEY` to supply a managed key.
- Conversation history is encrypted with Fernet and an independently generated workspace key. Set `JAMES_HISTORY_KEY` to supply a managed key.
- Keep `.env` private. JAMES warns about group/world-readable `.env` files where permissions are available.
- Conversation exports are intentionally plaintext files; store or share them carefully.

## Offline mode

`OFFLINE_MODE=true` blocks non-loopback network egress. Use a local provider endpoint such as Ollama when enabling it. Verify the configuration with `python -m james doctor` before relying on offline operation.

## Reporting a security issue

Do not publish exploit details, tokens, or private user data in a public issue. Open a private security report through the repository’s security-contact channel when one is available; until then, contact the maintainers directly with reproduction steps and impact.
