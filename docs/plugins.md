# Writing plugins with the SDK

JAMES has two extension tiers, and the **Plugin SDK** (`james.sdk`) is the
supported, documented way to author both.

1. **Generated skills** — safe-by-default pure `@tool` functions. Validated by
   the Skill Forge constrained runtime before they are written or loaded, and
   the only tier the marketplace can install.
2. **Trusted external plugins** — arbitrary Python, loaded only when
   `ENABLE_TRUSTED_EXTERNAL_PLUGINS=true`. Opt-in, because they can run any
   code.

Import from `james.sdk`, never from internal modules — the SDK is the stable
contract.

## A minimal generated skill

```python
"""Say hello to a user."""
from james.sdk import tool, ToolResult


@tool(
    "hello",
    "Say hello to a user by name.",
    {"name": {"type": "string", "description": "The user's name."}},
    required=["name"],
)
def hello(name: str) -> ToolResult:
    return ToolResult(ok=True, output=f"Hello, {name}!")
```

Save it as `plugins/hello.py`. It is discovered automatically on the next run
and appears in the registry (see `james --check`).

## Scaffolding

Use the CLI to generate a valid, manifest-carrying plugin file:

```bash
james --new-tool hello
```

`create_plugin` from the SDK does the same in code:

```python
from james.sdk import create_plugin

create_plugin("hello", description="Say hello.", tags=["greetings"])
```

The scaffold always passes the constrained runtime validation, so you can edit
the function body without fighting the parser.

## Manifests

Every SDK-generated plugin carries a manifest block under the
`# JAMES-GENERATED-SKILL v1` header:

```text
# JAMES-GENERATED-SKILL v1
# manifest-name: hello
# manifest-version: 1.0.0
# manifest-author: JAMES Community
# manifest-description: Say hello.
# manifest-tags: greetings
```

Parse and validate manifests in code:

```python
from james.sdk import parse_manifest, validate_manifest

manifest = parse_manifest(source)
issues = validate_manifest(manifest)   # [] when valid
```

The marketplace reads this metadata when a skill is published, so
`author`, `version`, and `tags` flow into the catalog automatically.

## Validation and loading

```python
from james.sdk import validate_plugin, load_plugin

issues = validate_plugin(source)   # list[str]; empty == safe to load
module = load_plugin("plugins/hello.py")
```

`validate_plugin` enforces the constrained-runtime rules: no filesystem,
network, process, reflection, loops, or dynamic execution. `load_plugin`
uses the same sandbox for generated skills; pass `trusted=True` only for
explicitly enabled trusted plugins.

## What the constrained runtime allows

A generated skill function may use literals, arithmetic, collections, and the
approved builtins (`len`, `range`, `sum`, `min`, `max`, `abs`, `round`,
`isinstance`, `print`, exception types). It may **not** import anything other
than JAMES metadata, use attributes, classes, loops, comprehensions, lambdas,
`with`, `yield`, or any I/O. If your capability needs real I/O, expose it via a
built-in JAMES tool and call that from your skill instead.

## Publishing to the marketplace

When a generated skill is saved and then published, it is bundled with its
manifest metadata:

```
save_skill → publish_skill(name="hello")
```

Marketplace entries without bundled code cannot be installed yet; they are
catalog placeholders.

## Trusted external plugins

Set `ENABLE_TRUSTED_EXTERNAL_PLUGINS=true`, drop a normal `.py` file in
`plugins/`, and JAMES will load its `Tool`s. This tier can do anything Python
can. Only enable it for source you have reviewed.

See [the security model](security.md) for the boundary between the two tiers.

## Signatures and dependencies

Marketplace-installable code must carry an Ed25519 signature and content digest.
Dependencies use plugin names with optional semantic constraints:

```text
# manifest-dependencies: shared_utils>=1.2.0,calendar_core==2.0.0
# manifest-signing-key-id: team-release
# manifest-content-sha256: <64 lowercase hex characters>
# manifest-signature: <base64 Ed25519 signature>
```

Use the SDK helpers to sign and verify bundles:

```python
from james.sdk import sign_plugin_source, verify_plugin_signature

signed = sign_plugin_source(source, private_key_pem, "team-release")
ok, reason = verify_plugin_signature(signed, {"team-release": public_key_pem})
```

Install trusted public keys as
`WORKSPACE_DIR/trusted_plugin_keys/<key-id>.pem`. The marketplace resolves the
full dependency chain and rejects missing dependencies, cycles, unsigned code,
unknown keys, digest mismatches, and invalid signatures. Locally published
skills are signed automatically with a workspace-local key whose public half is
enrolled in the same trust directory.

## Cloud plugin registry

Since Phase-4, the marketplace can also sync a **remote catalog** published as
a plain JSON list on GitHub (or any static host). JAMES merges it into the
local `marketplace.json` on demand; remote entries replace local ones with the
same name and are marked `"source": "remote"`.

```dotenv
MARKETPLACE_URL=https://raw.githubusercontent.com/Krish-1507/JAMES_Agent/main/marketplace/plugins.json
```

Trigger a sync from the agent (`update_marketplace`), the Integrations page in
the web UI, or in code:

```python
from james.tools.marketplace import sync_remote_catalog, marketplace_status

result = sync_remote_catalog()          # {"ok": True, "added": n, "total": n}
status = marketplace_status()           # last sync time + local/remote counts
```

Entry shape:

```json
{"name": "my-plugin", "description": "What it does.", "version": "1.0.0"}
```

**Security is unchanged**: the catalog is a discovery index only. Installing a
plugin from it still runs the full Ed25519 signature and digest verification
described above, so code fetched from the cloud cannot execute unless it was
signed by a key you have installed in `trusted_plugin_keys/`. `sync_remote_catalog`
also ignores malformed entries and never touches the local catalog on a
network failure.
