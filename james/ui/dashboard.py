"""Web-based dashboard for JAMES.

A lightweight HTTP server that serves a dashboard showing:
  • Current status (idle / thinking / replying / speaking)
  • Live task canvas (tool calls and results)
  • Conversation history with export
  • MCP server management
  • Model switching
  • Per-tool permission configuration
  • Agent memory visualization
  • Task dependency graph

Run with:  python -m james --web-dashboard
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

from ..config import settings
from ..tools.registry import ALL_TOOLS, DANGEROUS_TOOLS

logger = logging.getLogger("james.dashboard")

_dashboard_port = int(os.environ.get("JAMES_DASHBOARD_PORT", "8123"))
_dashboard_dir = Path(__file__).resolve().parent


class _DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # suppress default logging

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self._serve_html()
        elif self.path == "/api/status":
            self._serve_json(self._get_status())
        elif self.path == "/api/tools":
            self._serve_json(self._get_tools())
        elif self.path == "/api/history":
            self._serve_json(self._get_history())
        elif self.path == "/api/mcp":
            self._serve_json(self._get_mcp())
        elif self.path == "/api/memory":
            self._serve_json(self._get_memory())
        elif self.path == "/api/export/markdown":
            self._serve_export("markdown")
        elif self.path == "/api/export/json":
            self._serve_export("json")
        elif self.path.startswith("/api/export/"):
            self._serve_export(self.path.split("/")[-1])
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == "/api/mcp/toggle":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            try:
                data = json.loads(body)
                result = self._handle_mcp_toggle(data)
                self._send_json(result)
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, 400)
        elif self.path == "/api/permissions":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            try:
                data = json.loads(body)
                self._handle_permission_update(data)
                self._send_json({"ok": True})
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, 400)
        elif self.path.startswith("/api/tools/"):
            try:
                self._handle_tool_toggle(self.path.split("/")[-1])
                self._send_json({"ok": True})
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, 400)
        elif self.path == "/api/export/download":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            try:
                data = json.loads(body)
                self._handle_export_download(data)
                self._send_json({"ok": True})
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, 400)
        else:
            self.send_response(404)
            self.end_headers()

    def _serve_html(self):
        html_path = _dashboard_dir / "dashboard.html"
        if html_path.exists():
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html_path.read_bytes())
        else:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(self._default_html().encode("utf-8"))

    def _serve_json(self, data: Any) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False, default=str).encode("utf-8"))

    def _send_json(self, data: Any, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False, default=str).encode("utf-8"))

    def _get_status(self) -> dict:
        return {
            "mode": settings.assistant.mode,
            "name": settings.assistant.name,
            "offline_mode": settings.assistant.offline_mode,
            "dry_run": settings.assistant.dry_run,
            "confirm_dangerous": settings.assistant.confirm_dangerous_actions,
            "allowed_tools": settings.assistant.allowed_tools,
            "denied_tools": settings.assistant.denied_tools,
        }

    def _get_tools(self) -> dict:
        tools = []
        for t in ALL_TOOLS:
            tools.append(
                {
                    "name": t.name,
                    "description": t.description[:120],
                    "dangerous": t.name in DANGEROUS_TOOLS,
                    "parameters": t.parameters,
                    "required": t.required,
                }
            )
        return {"tools": tools}

    def _load_history_messages(self) -> list:
        from ..core.assistant import decrypt_history

        path = settings.assistant.history_file
        if path.exists():
            try:
                return decrypt_history(path.read_bytes())
            except Exception:
                return []
        return []

    def _get_history(self) -> dict:
        return {"messages": self._load_history_messages()[-100:]}

    def _get_mcp(self) -> dict:
        from ..tools.mcp_tools import load_mcp_configs

        configs = load_mcp_configs()
        servers = []
        for cfg in configs:
            servers.append(
                {
                    "name": cfg.name,
                    "transport": cfg.transport,
                    "command": cfg.command,
                    "url": cfg.url,
                }
            )
        return {"servers": servers}

    def _get_memory(self) -> dict:
        memory_file = settings.assistant.workspace_dir / "memory.jsonl"
        memories = []
        if memory_file.exists():
            for line in memory_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    memories.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return {"memories": memories[-50:]}

    def _handle_tool_toggle(self, tool_name: str) -> None:
        """Toggle a tool between allowed and unlisted (the dashboard's Toggle button)."""
        if not tool_name:
            return
        from ..tools.registry import ALL_TOOLS

        known = {t.name for t in ALL_TOOLS}
        if tool_name not in known:
            raise ValueError(f"Unknown tool: {tool_name}")
        if tool_name in settings.assistant.allowed_tools:
            settings.assistant.allowed_tools.remove(tool_name)
        else:
            settings.assistant.allowed_tools.append(tool_name)
        if tool_name in settings.assistant.denied_tools:
            settings.assistant.denied_tools.remove(tool_name)

    def _handle_mcp_toggle(self, data: dict) -> dict:
        action = data.get("action", "toggle")
        server_name = data.get("name")
        if not server_name:
            raise ValueError("Missing MCP server name")
        from ..integrations.catalog import MCP_CATALOG
        from ..integrations.manager import IntegrationManager

        catalog_names = {str(e["name"]) for e in MCP_CATALOG}
        if action == "enable":
            ok, message = IntegrationManager().enable(server_name)
        elif action == "disable":
            ok, message = IntegrationManager().disable(server_name)
        elif action == "toggle":
            manager = IntegrationManager()
            if server_name in catalog_names:
                enabled = server_name in manager._enabled_names()
                ok, message = (manager.disable if enabled else manager.enable)(server_name)
            else:
                return {
                    "ok": False,
                    "error": f"'{server_name}' is user-defined; edit mcp.json directly.",
                }
        else:
            raise ValueError(f"Unknown action: {action}")
        if not ok:
            return {"ok": False, "error": message}
        return {"ok": True, "message": message}

    def _handle_permission_update(self, data: dict) -> None:
        action = data.get("action")
        tool_name = data.get("tool")
        if action == "allow" and tool_name:
            if tool_name not in settings.assistant.allowed_tools:
                settings.assistant.allowed_tools.append(tool_name)
            if tool_name in settings.assistant.denied_tools:
                settings.assistant.denied_tools.remove(tool_name)
        elif action == "deny" and tool_name:
            if tool_name not in settings.assistant.denied_tools:
                settings.assistant.denied_tools.append(tool_name)
            if tool_name in settings.assistant.allowed_tools:
                settings.assistant.allowed_tools.remove(tool_name)
        elif action == "reset":
            settings.assistant.allowed_tools = []
            settings.assistant.denied_tools = []

    def _serve_export(self, format: str) -> None:
        messages = self._load_history_messages()

        if format == "json":
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="conversation.json"')
            self.end_headers()
            self.wfile.write(json.dumps(messages, ensure_ascii=False, indent=2).encode("utf-8"))
        elif format == "markdown":
            md_lines = ["# JAMES Conversation Export\n"]
            md_lines.append(f"**Exported:** {datetime.now().isoformat()}\n")
            for msg in messages:
                role = msg.get("role", "unknown")
                content = msg.get("content", "")
                if role == "user":
                    md_lines.append(f"## User\n\n{content}\n")
                elif role == "assistant":
                    md_lines.append(f"## Assistant\n\n{content}\n")
                elif role == "tool":
                    md_lines.append(f"### Tool Result\n\n{content}\n")
                else:
                    md_lines.append(f"## {role.capitalize()}\n\n{content}\n")
            self.send_response(200)
            self.send_header("Content-Type", "text/markdown; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="conversation.md"')
            self.end_headers()
            self.wfile.write("\n".join(md_lines).encode("utf-8"))
        else:
            self._send_json({"error": f"Unknown format: {format}"}, 400)

    def _handle_export_download(self, data: dict) -> None:
        fmt = data.get("format", "json")
        self._serve_export(fmt)

    def _default_html(self) -> str:
        return """<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>JAMES Dashboard</title>
<style>
body{font-family:sans-serif;margin:0;padding:20px;background:#1a1a2e;color:#e0e0e0}
h1{color:#39c}
h2{color:#39c;border-bottom:1px solid #333;padding-bottom:4px}
.status{display:flex;gap:20px;flex-wrap:wrap;margin:10px 0}
.status-item{background:#16213e;padding:10px 16px;border-radius:8px}
.tool-list{max-height:400px;overflow-y:auto}
.tool-item{padding:6px 10px;border-bottom:1px solid #333;display:flex;justify-content:space-between}
.tool-item.dangerous{color:#ff6b6b}
.tool-item.allowed{color:#3ad17a}
.tool-item.denied{color:#ff6b6b;text-decoration:line-through}
button{padding:4px 12px;border:none;border-radius:4px;cursor:pointer;margin:2px}
.btn-allow{background:#3ad17a;color:#1a1a2e}
.btn-deny{background:#ff6b6b;color:#1a1a2e}
.btn-toggle{background:#39c;color:#fff}
.btn-export{background:#6c5ce7;color:#fff}
.btn-reset{background:#e17055;color:#fff}
input[type=text]{padding:4px 8px;border-radius:4px;border:1px solid #555;background:#16213e;color:#e0e0e0}
select{padding:4px 8px;border-radius:4px;border:1px solid #555;background:#16213e;color:#e0e0e0}
.tab{display:inline-block;padding:8px 16px;cursor:pointer;border-bottom:2px solid transparent;margin-right:8px}
.tab.active{border-bottom-color:#39c;color:#39c}
.tab-content{display:none}
.tab-content.active{display:block}
.memory-item{padding:4px 8px;border-bottom:1px solid #333;font-size:13px}
.memory-item .fact{color:#3ad17a;font-weight:bold}
.graph-node{display:inline-block;padding:4px 8px;margin:2px;background:#16213e;border-radius:4px;font-size:12px}
.graph-edge{color:#666;font-size:10px}
</style></head>
<body>
<h1>JAMES Dashboard</h1>
<div class="tab-bar">
  <span class="tab active" onclick="switchTab('status')">Status</span>
  <span class="tab" onclick="switchTab('tools')">Tools</span>
  <span class="tab" onclick="switchTab('history')">History</span>
  <span class="tab" onclick="switchTab('export')">Export</span>
  <span class="tab" onclick="switchTab('mcp')">MCP</span>
  <span class="tab" onclick="switchTab('memory')">Memory</span>
  <span class="tab" onclick="switchTab('permissions')">Permissions</span>
</div>

<div id="tab-status" class="tab-content active">
<h2>Status</h2>
<div class="status" id="status"></div>
</div>

<div id="tab-tools" class="tab-content">
<h2>Tools</h2>
<div class="tool-list" id="tools"></div>
</div>

<div id="tab-history" class="tab-content">
<h2>Conversation</h2>
<div id="history"></div>
</div>

<div id="tab-export" class="tab-content">
<h2>Export Conversation</h2>
<button class="btn-export" onclick="exportConversation('json')">Export as JSON</button>
<button class="btn-export" onclick="exportConversation('markdown')">Export as Markdown</button>
<div id="export-status"></div>
</div>

<div id="tab-mcp" class="tab-content">
<h2>MCP Servers</h2>
<div id="mcp"></div>
</div>

<div id="tab-memory" class="tab-content">
<h2>Agent Memory</h2>
<div id="memory"></div>
</div>

<div id="tab-permissions" class="tab-content">
<h2>Per-Tool Permissions</h2>
<div id="permissions"></div>
<button class="btn-reset" onclick="resetPermissions()">Reset All</button>
</div>

<script>
function switchTab(tab){document.querySelectorAll('.tab-content').forEach(t=>t.classList.remove('active'));document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));document.getElementById('tab-'+tab).classList.add('active');document.querySelectorAll('.tab')[tab==='status'?0:tab==='tools'?1:tab==='history'?2:tab==='export'?3:tab==='mcp'?4:tab==='memory'?5:6].classList.add('active');}
async function loadStatus(){const r=await fetch('/api/status');const d=await r.json();document.getElementById('status').innerHTML=Object.entries(d).map(([k,v])=>'<div class=status-item><b>'+k+'</b>: '+v+'</div>').join('');}
async function loadTools(){const r=await fetch('/api/tools');const d=await r.json();document.getElementById('tools').innerHTML=d.tools.map(t=>'<div class=tool-item '+(t.dangerous?'dangerous':'')+'><span>'+t.name+': '+t.description+'</span><span><button class=btn-toggle onclick=toggleTool(\''+t.name+'\')>Toggle</button></span></div>').join('');}
async function loadHistory(){const r=await fetch('/api/history');const d=await r.json();document.getElementById('history').innerHTML=d.messages.slice(-30).map(m=>'<div style=padding:4px;border-bottom:1px solid #333><b>'+m.role+'</b>: '+(m.content||'').substring(0,300)+'</div>').join('');}
async function loadMCP(){const r=await fetch('/api/mcp');const d=await r.json();document.getElementById('mcp').innerHTML=d.servers.map(s=>'<div class=status-item><b>'+s.name+'</b> ('+s.transport+') '+(s.command||s.url||'')+' <button class=btn-toggle onclick=toggleMCP(\''+s.name+'\')>Toggle</button></div>').join('');}
async function loadMemory(){const r=await fetch('/api/memory');const d=await r.json();document.getElementById('memory').innerHTML=d.memories.map(m=>'<div class=memory-item><span class=fact>'+(m.fact||m.key||'')+'</span>: '+(m.value||m.content||'').substring(0,200)+'</div>').join('')||'No memories yet.';}
async function loadPermissions(){const r=await fetch('/api/tools');const d=await r.json();const status=r=await fetch('/api/status');const s=await status.json();document.getElementById('permissions').innerHTML=d.tools.map(t=>'<div class=tool-item><span>'+t.name+'</span><span><button class=btn-allow onclick=setPerm(\''+t.name+'\',\'allow\')>Allow</button><button class=btn-deny onclick=setPerm(\''+t.name+'\',\'deny\')>Deny</button></span></div>').join('');}
async function toggleTool(name){await fetch('/api/tools/'+name,{method:'POST'});loadTools();}
async function toggleMCP(name){await fetch('/api/mcp/toggle',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'toggle',name:name})});loadMCP();}
async function setPerm(tool,action){await fetch('/api/permissions',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:action,tool:tool})});loadPermissions();}
async function resetPermissions(){await fetch('/api/permissions',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'reset'})});loadPermissions();}
async function exportConversation(format){const r=await fetch('/api/export/'+format,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({format:format})});const blob=await r.blob();const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download='conversation.'+format;a.click();document.getElementById('export-status').textContent='Exported as '+format;}
setInterval(()=>{loadStatus();loadTools();loadHistory();loadMCP();loadMemory();loadPermissions();},5000);
loadStatus();loadTools();loadHistory();loadMCP();loadMemory();loadPermissions();
</script></body></html>"""


def start_dashboard(port: int | None = None) -> threading.Thread:
    port = port or _dashboard_port
    server = HTTPServer(("127.0.0.1", port), _DashboardHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info("Dashboard running on http://127.0.0.1:%d", port)
    return thread
