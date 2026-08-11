/* JAMES web UI — vanilla JS SPA for the `james serve` sidecar. */
"use strict";

// ---------------------------------------------------------------------------
// state
// ---------------------------------------------------------------------------

const state = {
  status: null,
  session: "default",
  threads: [], // [{id, userEl, asstEl, thinking, streamText, streaming, tools}]
  activeApproval: null,
  busy: false,
};

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

// ---------------------------------------------------------------------------
// api helpers
// ---------------------------------------------------------------------------

async function api(path, method = "GET", body = null) {
  const opts = { method, headers: {} };
  if (body !== null) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (_) { /* ignore */ }
    throw new Error(detail);
  }
  return res.json();
}

function toast(msg, kind = "") {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = msg;
  $("#toast-root").appendChild(el);
  setTimeout(() => el.remove(), 4000);
}

// ---------------------------------------------------------------------------
// markdown (compact, safe renderer: HTML is escaped first)
// ---------------------------------------------------------------------------

function escapeHtml(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
          .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function inlineMd(s) {
  return s
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*]+)\*/g, "$1<em>$2</em>")
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
}

function renderMarkdown(src) {
  const lines = escapeHtml(src).split("\n");
  let html = "";
  let i = 0;
  let inCode = false;
  let codeLang = "";
  let codeBuf = [];
  let listType = null;
  let inTable = false;
  let tableBuf = [];

  const flushTable = () => {
    if (!inTable) return;
    const header = tableBuf[0];
    const rows = tableBuf.slice(1);
    let t = "<table><thead><tr>";
    header.split("|").slice(1, -1).forEach((c) => { t += `<th>${inlineMd(c.trim())}</th>`; });
    t += "</tr></thead><tbody>";
    rows.forEach((r) => {
      t += "<tr>";
      r.split("|").slice(1, -1).forEach((c) => { t += `<td>${inlineMd(c.trim())}</td>`; });
      t += "</tr>";
    });
    t += "</tbody></table>";
    html += t;
    tableBuf = [];
    inTable = false;
  };

  const closeList = () => {
    if (listType) { html += `</${listType}>`; listType = null; }
  };

  while (i < lines.length) {
    const line = lines[i];
    const fence = line.match(/^```(\w*)\s*$/);
    if (fence) {
      closeList(); flushTable();
      if (inCode) {
        html += `<pre><code class="lang-${escapeHtml(codeLang)}">${codeBuf.join("\n")}</code><button class="pre-copy">copy</button></pre>`;
        inCode = false; codeBuf = [];
      } else {
        inCode = true; codeLang = fence[1] || "";
      }
      i++; continue;
    }
    if (inCode) { codeBuf.push(line); i++; continue; }

    if (line.trim() === "") { closeList(); flushTable(); html += inTable ? "" : ""; i++; continue; }

    const isSep = /^\s*\|?\s*:?-{3,}\s*\|?\s*(:?-{3,}\s*\|?\s*)*$/.test(line.trim());
    if (isSep && inTable) { i++; continue; }

    const isTableRow = /^\s*\|/.test(line) && /\|\s*$/.test(line);
    if (isTableRow) { closeList(); if (!inTable) { inTable = true; tableBuf = []; } tableBuf.push(line); i++; continue; }
    if (inTable) { flushTable(); }

    const h = line.match(/^(#{1,4})\s+(.*)$/);
    if (h) { closeList(); const lv = h[1].length; html += `<h${lv}>${inlineMd(h[2])}</h${lv}>`; i++; continue; }

    const q = line.match(/^>\s?(.*)$/);
    if (q) { closeList(); html += `<blockquote>${inlineMd(q[1])}</blockquote>`; i++; continue; }

    const hr = /^(\s*-\s*){3,}$/.test(line) || /^\*\s*\*\s*\*+\s*$/.test(line);
    if (hr) { closeList(); html += "<hr>"; i++; continue; }

    const ul = line.match(/^\s*[-*+]\s+(.*)$/);
    if (ul) { if (listType !== "ul") { closeList(); listType = "ul"; html += "<ul>"; } html += `<li>${inlineMd(ul[1])}</li>`; i++; continue; }

    const ol = line.match(/^\s*\d+\.\s+(.*)$/);
    if (ol) { if (listType !== "ol") { closeList(); listType = "ol"; html += "<ol>"; } html += `<li>${inlineMd(ol[1])}</li>`; i++; continue; }

    closeList();
    html += `<p>${inlineMd(line)}</p>`;
    i++;
  }
  closeList(); flushTable();
  if (inCode) {
    html += `<pre><code class="lang-${escapeHtml(codeLang)}">${codeBuf.join("\n")}</code><button class="pre-copy">copy</button></pre>`;
  }
  return html;
}

document.addEventListener("click", (e) => {
  const btn = e.target.closest(".pre-copy, .copy-btn");
  if (!btn) return;
  let text = "";
  const pre = btn.closest("pre");
  if (pre) {
    text = pre.querySelector("code").textContent;
  } else {
    const msg = btn.closest(".msg");
    text = msg.querySelector(".markdown").textContent;
  }
  navigator.clipboard.writeText(text).then(() => {
    const old = btn.textContent;
    btn.textContent = "copied!";
    setTimeout(() => { btn.textContent = old; }, 1200);
  });
});

// ---------------------------------------------------------------------------
// chat
// ---------------------------------------------------------------------------

function newThread(userText) {
  const chat = $("#chat");
  const thread = document.createElement("div");
  thread.className = "thread";
  chat.appendChild(thread);

  const user = document.createElement("div");
  user.className = "msg user";
  user.innerHTML = `<div class="who">${escapeHtml((state.status && state.status.name) || "You")}</div><div class="markdown"></div>`;
  thread.appendChild(user);
  user.querySelector(".markdown").innerHTML = renderMarkdown(userText);

  const asst = document.createElement("div");
  asst.className = "msg assistant";
  asst.innerHTML = `<div class="who">JAMES</div><button class="copy-btn">copy</button><div class="markdown"></div>`;
  thread.appendChild(asst);
  const body = asst.querySelector(".markdown");

  const t = {
    id: "t" + Date.now() + Math.random().toString(16).slice(2, 6),
    userEl: user,
    asstEl: asst,
    body,
    thinking: null,
    streamText: "",
    streaming: false,
    timer: null,
  };
  state.threads.push(t);
  chat.scrollTop = chat.scrollHeight;
  return t;
}

function showThinking(t) {
  if (!t.thinking) {
    t.thinking = document.createElement("div");
    t.thinking.className = "thinking";
    t.thinking.innerHTML = "<span></span><span></span><span></span>";
    t.asstEl.appendChild(t.thinking);
  }
}

function hideThinking(t) {
  if (t.thinking) { t.thinking.remove(); t.thinking = null; }
}

function renderStream(t) {
  t.body.innerHTML = renderMarkdown(t.streamText) + (t.streaming ? "" : "");
  $("#chat").scrollTop = $("#chat").scrollHeight;
}

function streamInto(t, text) {
  t.streamText += text;
  if (!t.streaming) {
    t.streaming = true;
    t.timer = setInterval(() => {
      renderStream(t);
      if (t.streaming === false) { clearInterval(t.timer); }
    }, 30);
  }
}

function finishStream(t) {
  if (t.timer) clearInterval(t.timer);
  t.streaming = false;
  renderStream(t);
}

function addCaption(text, cls = "") {
  const threads = state.threads;
  const t = threads[threads.length - 1];
  const cap = document.createElement("div");
  cap.className = "caption " + cls;
  cap.innerHTML = text;
  if (t) t.thread ? null : null;
  (t ? t.asstEl.parentElement : $("#chat")).appendChild(cap);
  $("#chat").scrollTop = $("#chat").scrollHeight;
}

// ---------------------------------------------------------------------------
// tool activity panel
// ---------------------------------------------------------------------------

const toolRows = new Map();

function toolRow(id) {
  if (toolRows.has(id)) return toolRows.get(id);
  const row = document.createElement("div");
  row.className = "tool-row";
  row.innerHTML = `<span class="st running">running</span><span class="tname"></span><span class="details"></span>`;
  $("#tool-list").appendChild(row);
  $("#tool-activity").classList.remove("hidden");
  toolRows.set(id, row);
  return row;
}

function onToolStart(ev) {
  const row = toolRow(ev.call_id);
  row.querySelector(".tname").textContent = ev.name || "";
  row.querySelector(".details").textContent = summarizeArgs(ev.args);
  addCaption(`🔧 <span class="tool-name">${escapeHtml(ev.name || "")}</span> — ${escapeHtml(summarizeArgs(ev.args))}`);
}

function onTool(ev) {
  const row = toolRow(ev.call_id);
  const st = row.querySelector(".st");
  st.className = "st " + (ev.ok ? "ok" : "err");
  st.textContent = ev.ok ? "done" : "error";
  row.querySelector(".details").textContent = summarizeResult(ev);
}

function summarizeArgs(args) {
  try {
    const a = typeof args === "string" ? JSON.parse(args) : (args || {});
    const parts = Object.entries(a).slice(0, 4).map(([k, v]) => `${k}=${String(v).slice(0, 60)}`);
    return parts.join("  ") || "(no args)";
  } catch (_) { return String(args || "").slice(0, 120); }
}

function summarizeResult(ev) {
  let out = "";
  try {
    const r = typeof ev.result === "string" ? JSON.parse(ev.result) : ev.result;
    if (r && r.output) out = String(r.output);
    else if (r && r.error) out = String(r.error);
  } catch (_) { out = String(ev.result || ""); }
  return out.slice(0, 120);
}

// ---------------------------------------------------------------------------
// history / status bootstrap
// ---------------------------------------------------------------------------

function renderHistory(history) {
  const chat = $("#chat");
  chat.innerHTML = "";
  state.threads = [];
  for (const msg of history || []) {
    if (msg.role === "user") {
      newThread(msg.text);
    } else if (msg.role === "assistant") {
      const t = state.threads[state.threads.length - 1];
      if (t) { t.streamText = msg.text; finishStream(t); }
    }
  }
}

async function loadStatus() {
  try {
    state.status = await api("/api/status");
  } catch (_) {
    toast("Cannot reach the JAMES server.", "err");
    return;
  }
  if (!state.status || state.status.ready === false) return;
  $("#version").textContent = "v" + (state.status.version || "");
  fillModelSelects(state.status);
  state.session = state.status.session || "default";
  $("#session-name").textContent = state.session;
  if (state.status.history && state.status.history.length && !$("#chat").children.length) {
    renderHistory(state.status.history);
  }
  refreshSessions(state.status.sessions || [], state.session);
  loadVoice();
  loadTools();
  loadSettings();
  loadOnboarding();
  loadIntegrations();
  loadMarketplace();
  loadGateway();
  loadRecipes();
  $("#conn-dot").classList.add("on");
}

// ---------------------------------------------------------------------------
// model switcher
// ---------------------------------------------------------------------------

function fillModelSelects(status) {
  const provSel = $("#provider-select");
  provSel.innerHTML = "";
  for (const p of status.providers || []) {
    const opt = document.createElement("option");
    opt.value = p.name;
    opt.textContent = p.name;
    provSel.appendChild(opt);
  }
  provSel.value = status.provider;
  fillModels(status);
  $("#model-select").value = status.model;
}

function fillModels(status) {
  const p = (status.providers || []).find((x) => x.name === $("#provider-select").value);
  const modelSel = $("#model-select");
  modelSel.innerHTML = "";
  for (const m of (p && p.models) || []) {
    const opt = document.createElement("option");
    opt.value = m;
    opt.textContent = m;
    modelSel.appendChild(opt);
  }
}

$("#provider-select").addEventListener("change", () => {
  const p = (state.status.providers || []).find((x) => x.name === $("#provider-select").value);
  if (p) {
    $("#model-select").innerHTML = "";
    for (const m of p.models || []) {
      const opt = document.createElement("option");
      opt.value = m;
      opt.textContent = m;
      $("#model-select").appendChild(opt);
    }
    $("#model-select").value = p.default_model || (p.models || [])[0];
  }
});

$("#apply-model-btn").addEventListener("click", async () => {
  const provider = $("#provider-select").value;
  const model = $("#model-select").value;
  if (!provider || !model) return;
  try {
    await api("/api/model", "POST", { provider, model });
    toast(`Model switched to ${provider}/${model}`, "ok");
    loadStatus();
  } catch (e) { toast(e.message, "err"); }
});

// ---------------------------------------------------------------------------
// sessions sidebar
// ---------------------------------------------------------------------------

function refreshSessions(sessions, current) {
  const list = $("#session-list");
  list.innerHTML = "";
  for (const name of sessions || []) {
    const item = document.createElement("div");
    item.className = "session-item" + (name === current ? " current" : "");
    item.textContent = name;
    item.addEventListener("click", async () => {
      try {
        await api("/api/sessions/switch", "POST", { name });
        state.session = name;
        $("#session-name").textContent = name;
        $("#chat").innerHTML = "";
        state.threads = [];
        loadStatus();
      } catch (e) { toast(e.message, "err"); }
    });
    list.appendChild(item);
  }
}

$("#new-chat-btn").addEventListener("click", async () => {
  try {
    const r = await api("/api/sessions/new", "POST");
    state.session = r.name;
    $("#session-name").textContent = r.name;
    $("#chat").innerHTML = "";
    state.threads = [];
    loadStatus();
  } catch (e) { toast(e.message, "err"); }
});

$("#session-name").addEventListener("dblclick", async () => {
  const name = prompt("Clear the current session? Type its name to confirm:", state.session);
  if (name === state.session) {
    try { await api("/api/sessions/clear", "POST"); toast("Session cleared", "ok"); loadStatus(); }
    catch (e) { toast(e.message, "err"); }
  }
});

// ---------------------------------------------------------------------------
// composer
// ---------------------------------------------------------------------------

function autosize() {
  const ta = $("#composer-input");
  ta.style.height = "auto";
  ta.style.height = Math.min(ta.scrollHeight, 180) + "px";
}
$("#composer-input").addEventListener("input", autosize);

async function sendTurn(text) {
  if (!text || state.busy) return;
  state.busy = true;
  $("#send-btn").disabled = true;
  const t = newThread(text);
  showThinking(t);
  addCaption("");
  try {
    await api("/api/turn", "POST", { text });
  } catch (e) {
    hideThinking(t);
    t.body.innerHTML = `<span style="color:var(--err)">${escapeHtml(e.message)}</span>`;
    toast(e.message, "err");
  }
  state.busy = false;
  $("#send-btn").disabled = false;
}

$("#composer").addEventListener("submit", (e) => {
  e.preventDefault();
  const ta = $("#composer-input");
  const text = ta.value.trim();
  if (text) {
    ta.value = "";
    autosize();
    sendTurn(text);
  }
});

$("#composer-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    $("#composer").dispatchEvent(new Event("submit"));
  }
});

// ---------------------------------------------------------------------------
// voice page + topbar controls
// ---------------------------------------------------------------------------

const VOICE_PILL = { idle: "🔍 idle", ready: "🟢 ready", listening: "🎧 listening", transcribing: "🎙 transcribing", thinking: "🧠 thinking", speaking: "🔊 speaking", muted: "🔇 muted", error: "⚠️ voice error" };

function setVoiceState(st) {
  const pill = $("#voice-pill");
  pill.textContent = VOICE_PILL[st] || `◉ ${st}`;
  pill.classList.remove("hidden", "speaking", "listening", "thinking", "muted");
  if (st === "speaking") pill.classList.add("speaking");
  if (st === "listening" || st === "ready" || st === "transcribing") pill.classList.add("listening");
  if (st === "thinking") pill.classList.add("thinking");
  if (st === "muted") pill.classList.add("muted");
  $("#voice-state").textContent = st;
  $("#voice-state").className = "pill " + (st === "muted" ? "muted" : "");
  if (st === "muted") $("#voice-mute-btn").textContent = "Unmute mic";
  else $("#voice-mute-btn").textContent = "Mute mic";
}

function setVoiceLevel(level) {
  $("#level-bar").style.width = Math.max(0, Math.min(100, Math.round(level * 100))) + "%";
}

async function loadVoice() {
  try {
    const v = await api("/api/voice");
    $("#voice-duplex").textContent = v.duplex_mode + (v.enabled ? "" : " (voice disabled)");
    $("#voice-enabled-cb").checked = v.enabled;
    setVoiceState(v.state || "idle");
    setVoiceLevel(v.level || 0);
  } catch (_) { /* voice panel is optional */ }
}

$("#mute-btn").addEventListener("click", async () => {
  const st = $("#voice-pill").textContent;
  const muted = !st.includes("🔇");
  try { await api("/api/voice/mute", "POST", { muted }); if (muted) setVoiceState("muted"); else loadVoice(); }
  catch (e) { toast(e.message, "err"); }
});
$("#voice-mute-btn").addEventListener("click", async () => {
  try { await api("/api/voice/mute", "POST", { muted: true }); setVoiceState("muted"); }
  catch (e) { toast(e.message, "err"); }
});
$("#interrupt-btn").addEventListener("click", async () => {
  try { await api("/api/voice/interrupt", "POST"); toast("Interrupted", "ok"); }
  catch (e) { toast(e.message, "err"); }
});
$("#voice-interrupt-btn").addEventListener("click", async () => {
  try { await api("/api/voice/interrupt", "POST"); }
  catch (e) { toast(e.message, "err"); }
});
$("#voice-only-cb").addEventListener("change", async () => {
  try { await api("/api/voice/voice_only", "POST", { enabled: $("#voice-only-cb").checked }); }
  catch (e) { toast(e.message, "err"); }
});
$("#voice-enabled-cb").addEventListener("change", async () => {
  try { await api("/api/settings", "POST", { voice_enabled: $("#voice-enabled-cb").checked }); toast("Voice setting saved (applies on next launch)", "ok"); }
  catch (e) { toast(e.message, "err"); }
});

// ---------------------------------------------------------------------------
// tools page
// ---------------------------------------------------------------------------

let toolState = [];

async function loadTools() {
  try {
    const r = await api("/api/tools");
    toolState = r.tools || [];
    renderTools();
  } catch (_) { /* tools panel optional */ }
}

function renderTools() {
  const list = $("#tools-list");
  list.innerHTML = "";
  for (const t of toolState) {
    const item = document.createElement("div");
    item.className = "tool-item";
    item.innerHTML = `
      <label class="check"><input type="checkbox" data-kind="allowed" ${t.allowed ? "checked" : ""}> allow</label>
      <label class="check"><input type="checkbox" data-kind="denied" ${t.denied ? "checked" : ""}> deny</label>
      <div><div class="t-name">${escapeHtml(t.name)} ${t.dangerous ? '<span class="badge">dangerous</span>' : ""}</div>
      <div class="t-desc">${escapeHtml(t.description || "")}</div></div>`;
    list.appendChild(item);
  }
}

$("#tools-save-btn").addEventListener("click", async () => {
  const allowed = [];
  const denied = [];
  $$("#tools-list .tool-item").forEach((item, idx) => {
    const name = toolState[idx].name;
    const [allowCb, denyCb] = item.querySelectorAll("input");
    if (allowCb.checked) allowed.push(name);
    if (denyCb.checked) denied.push(name);
  });
  try {
    await api("/api/settings", "POST", { allowed_tools: allowed, denied_tools: denied });
    toast("Tool permissions saved", "ok");
    loadTools();
  } catch (e) { toast(e.message, "err"); }
});

// ---------------------------------------------------------------------------
// settings page
// ---------------------------------------------------------------------------

async function loadSettings() {
  try {
    const s = await api("/api/settings");
    $("#set-mode").value = s.mode;
    $("#set-dry-run").checked = s.dry_run;
    $("#set-confirm").checked = s.confirm_dangerous_actions;
    $("#set-offline").checked = s.offline_mode;
    $("#set-wake-engine").value = s.wake_engine;
    $("#set-wake-word").value = s.wake_word;
    $("#set-name").value = s.assistant_name;
    $("#set-user-name").value = s.user_name;
    $("#set-stt").value = s.stt_provider;
    $("#set-tts").value = s.tts_provider;
    $("#set-duplex").value = s.duplex_mode;
  } catch (_) { /* settings page optional */ }
}

$("#settings-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const updates = {
    mode: $("#set-mode").value,
    dry_run: $("#set-dry-run").checked,
    confirm_dangerous_actions: $("#set-confirm").checked,
    offline_mode: $("#set-offline").checked,
    wake_engine: $("#set-wake-engine").value,
    wake_word: $("#set-wake-word").value,
    assistant_name: $("#set-name").value,
    user_name: $("#set-user-name").value,
    stt_provider: $("#set-stt").value,
    tts_provider: $("#set-tts").value,
    duplex_mode: $("#set-duplex").value,
  };
  try {
    await api("/api/settings", "POST", updates);
    toast("Settings saved (applies to the next turn)", "ok");
  } catch (err) { toast(err.message, "err"); }
});

// ---------------------------------------------------------------------------
// integrations page (one-click MCP servers)
// ---------------------------------------------------------------------------

async function loadIntegrations() {
  try {
    const r = await api("/api/integrations");
    renderIntegrations(r.integrations || []);
  } catch (e) { toast("Integrations: " + e.message, "err"); }
}

function renderIntegrations(rows) {
  const list = $("#integration-list");
  list.innerHTML = "";
  for (const it of rows) {
    const missingEnv = Object.entries(it.env || {}).filter(([, v]) => !v.set);
    const item = document.createElement("div");
    item.className = "tool-item";
    item.innerHTML = `
      <label class="check"><input type="checkbox" data-name="${escapeHtml(it.name)}" ${it.enabled ? "checked" : ""}></label>
      <div style="flex:1">
        <div class="t-name">${escapeHtml(it.title || it.name)} ${it.community ? '<span class="badge">community</span>' : ""}</div>
        <div class="t-desc">${escapeHtml(it.description || "")}</div>
        <div class="t-desc muted small">${escapeHtml(it.command || "")} ${escapeHtml((it.args || []).join(" "))}</div>
        ${missingEnv.length ? `<div class="t-desc" style="color:var(--warn)">needs: ${escapeHtml(missingEnv.map(([k]) => k).join(", "))}</div>` : ""}
      </div>`;
    item.querySelector("input").addEventListener("change", async (ev) => {
      const enabled = ev.target.checked;
      try {
        const r = await api(`/api/integrations/${encodeURIComponent(it.name)}/${enabled ? "enable" : "disable"}`, "POST", {});
        toast(`${enabled ? "Enabled" : "Disabled"} ${it.name}${r.reloaded ? ` (tools: -${r.reloaded.removed} +${r.reloaded.added})` : ""}`, "ok");
      } catch (e2) { toast(e2.message, "err"); ev.target.checked = !enabled; }
    });
    list.appendChild(item);
  }
}

// ---------------------------------------------------------------------------
// cloud marketplace
// ---------------------------------------------------------------------------

async function loadMarketplace() {
  try {
    const m = await api("/api/marketplace");
    const synced = m.synced_at ? `Last sync: ${m.synced_at}` : "Never synced";
    $("#marketplace-info").textContent =
      `${m.url} — ${synced} — ${m.remote_count} remote / ${m.local_count} total`;
  } catch (e) { $("#marketplace-info").textContent = "Marketplace status unavailable."; }
}

$("#marketplace-sync-btn").addEventListener("click", async () => {
  $("#marketplace-sync-btn").disabled = true;
  try {
    const r = await api("/api/marketplace/sync", "POST", {});
    toast(r.message, "ok");
    loadMarketplace();
  } catch (e) { toast(e.message, "err"); }
  $("#marketplace-sync-btn").disabled = false;
});

// ---------------------------------------------------------------------------
// messaging gateway
// ---------------------------------------------------------------------------

async function loadGateway() {
  try {
    const g = await api("/api/gateway");
    const list = $("#gateway-info");
    list.innerHTML = "";
    if (!g.enabled) {
      list.innerHTML = '<div class="t-desc muted">Gateway is disabled. Set GATEWAY_ENABLED=true in .env and restart.</div>';
      return;
    }
    for (const c of g.channels || []) {
      const item = document.createElement("div");
      item.className = "tool-item";
      item.innerHTML = `<div><div class="t-name">${escapeHtml(c.name)} <span class="badge" style="border-color:var(--ok);color:var(--ok)">${c.running ? "connected" : "stopped"}</span></div></div>`;
      list.appendChild(item);
    }
  } catch (e) { /* gateway card optional */ }
}

// ---------------------------------------------------------------------------
// recipes page
// ---------------------------------------------------------------------------

async function loadRecipes() {
  try {
    const r = await api("/api/recipes");
    renderRecipes(r.recipes || []);
  } catch (e) { toast("Recipes: " + e.message, "err"); }
}

function renderRecipes(recipes) {
  const list = $("#recipe-list");
  list.innerHTML = "";
  if (!recipes.length) {
    list.innerHTML = '<div class="t-desc muted">No recipes yet — compose one below.</div>';
    return;
  }
  for (const rc of recipes) {
    const item = document.createElement("div");
    item.className = "tool-item";
    const steps = (rc.steps || []).map((s) => s.tool).join(", ");
    item.innerHTML = `
      <label class="check"><input type="checkbox" data-name="${escapeHtml(rc.name)}" ${rc.enabled ? "checked" : ""}></label>
      <div style="flex:1">
        <div class="t-name">${escapeHtml(rc.name)} <span class="badge">${rc.enabled ? "enabled" : "paused"}</span> <span class="badge" style="border-color:var(--border);color:var(--text-dim)">runs: ${rc.runs}</span></div>
        <div class="t-desc">${escapeHtml(rc.description || "")}</div>
        <div class="t-desc muted small">trigger: ${escapeHtml(rc.trigger || "manual")} · steps: ${escapeHtml(steps || "—")}</div>
        ${rc.last_error ? `<div class="t-desc" style="color:var(--err)">last error: ${escapeHtml(String(rc.last_error).slice(0, 120))}</div>` : ""}
      </div>
      <div class="btn-row" style="margin:0">
        <button class="btn rc-run" data-name="${escapeHtml(rc.name)}">Run now</button>
        <button class="btn danger rc-del" data-name="${escapeHtml(rc.name)}">Delete</button>
      </div>`;
    item.querySelector("input").addEventListener("change", async (ev) => {
      try {
        await api(`/api/recipes/${encodeURIComponent(rc.name)}/toggle`, "POST", { enabled: ev.target.checked });
        toast(rc.name + (ev.target.checked ? " enabled" : " paused"), "ok");
        loadRecipes();
      } catch (e2) { toast(e2.message, "err"); ev.target.checked = !ev.target.checked; }
    });
    item.querySelector(".rc-run").addEventListener("click", async () => {
      try {
        const r = await api(`/api/recipes/${encodeURIComponent(rc.name)}/run`, "POST", {});
        toast(r.ok ? "Recipe completed" : "Recipe failed — see details", r.ok ? "ok" : "err");
        loadRecipes();
      } catch (e2) { toast(e2.message, "err"); }
    });
    item.querySelector(".rc-del").addEventListener("click", async () => {
      if (!confirm(`Delete recipe '${rc.name}'?`)) return;
      try {
        await fetch(`/api/recipes/${encodeURIComponent(rc.name)}`, { method: "DELETE" });
        toast("Deleted " + rc.name, "ok");
        loadRecipes();
      } catch (e2) { toast(e2.message, "err"); }
    });
    list.appendChild(item);
  }
}

$("#recipe-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const name = $("#rc-name").value.trim();
  const request = $("#rc-request").value.trim();
  const stepsRaw = $("#rc-steps").value.trim();
  const trigger = $("#rc-trigger").value;
  if (!name && !request) { toast("Give the recipe a name or a request", "err"); return; }
  try {
    if (stepsRaw) {
      let stepsJson = stepsRaw;
      try { stepsJson = JSON.parse(stepsRaw); } catch (_) { /* keep as string */ }
      await api("/api/recipes", "POST", { name: name || "automation", description: request, trigger, steps_json: stepsJson });
      toast("Recipe saved", "ok");
    } else {
      if (!request) { toast("Describe what the recipe should do, or paste steps JSON", "err"); return; }
      const r = await api("/api/recipes/compose", "POST", { request, trigger });
      toast(r.message || "Recipe composed", "ok");
    }
    $("#rc-name").value = ""; $("#rc-request").value = ""; $("#rc-steps").value = "";
    loadRecipes();
  } catch (err) { toast(err.message, "err"); }
});

// ---------------------------------------------------------------------------
// approval modal
// ---------------------------------------------------------------------------

function showApproval(ev) {
  state.activeApproval = ev;
  const root = $("#modal-root");
  root.innerHTML = `
    <div class="modal">
      <h2>⚠️ Approve action?</h2>
      <p>JAMES wants to run <code>${escapeHtml(ev.name)}</code>. Arguments below are redacted.</p>
      <div class="args">${escapeHtml(JSON.stringify(ev.args || {}, null, 2))}</div>
      <div class="btn-row">
        <button id="approve-deny" class="btn danger" autofocus>Deny</button>
        <button id="approve-allow" class="btn primary">Allow once</button>
      </div>
    </div>`;
  root.classList.add("show");
  $("#approve-deny").addEventListener("click", () => respondApproval(false));
  $("#approve-allow").addEventListener("click", () => respondApproval(true));
}

function respondApproval(allowed) {
  const ev = state.activeApproval;
  state.activeApproval = null;
  $("#modal-root").classList.remove("show");
  if (ev) {
    api("/api/approvals/" + encodeURIComponent(ev.id), "POST", { allowed })
      .then(() => toast(allowed ? "Allowed" : "Denied", allowed ? "ok" : ""))
      .catch((e) => toast(e.message, "err"));
  }
}

// ---------------------------------------------------------------------------
// onboarding wizard
// ---------------------------------------------------------------------------

async function loadOnboarding() {
  let ob;
  try { ob = await api("/api/onboarding"); } catch (_) { return; }
  if (!ob.needed && ob.api_key_set) return;
  const root = $("#modal-root");
  root.innerHTML = `
    <div class="modal">
      <h2>Welcome to JAMES</h2>
      <p class="hint">Paste an API key and pick a model — JAMES detects the provider from the key format.</p>
      <label>Provider <select id="ob-provider"></select></label>
      <label>Model <select id="ob-model"></select></label>
      <label>API key <input type="password" id="ob-key" placeholder="sk-… / AIza…"></label>
      <label class="check"><input type="checkbox" id="ob-voice"> Enable voice mode</label>
      <div class="btn-row">
        <button id="ob-cancel" class="btn">Use text-only defaults</button>
        <button id="ob-submit" class="btn primary">Set up JAMES</button>
      </div>
    </div>`;
  root.classList.add("show");
  const provSel = $("#ob-provider");
  for (const p of ob.providers || []) {
    const opt = document.createElement("option");
    opt.value = p.name;
    opt.textContent = p.name;
    provSel.appendChild(opt);
  }
  const fillModels = () => {
    const p = (ob.providers || []).find((x) => x.name === provSel.value);
    const msel = $("#ob-model");
    msel.innerHTML = "";
    for (const m of (p && p.models) || []) {
      const opt = document.createElement("option");
      opt.value = m;
      opt.textContent = m;
      msel.appendChild(opt);
    }
    if (p && p.default_model) msel.value = p.default_model;
  };
  fillModels();
  provSel.addEventListener("change", fillModels);
  $("#ob-cancel").addEventListener("click", () => {
    root.classList.remove("show");
    api("/api/onboarding", "POST", { provider: provSel.value, model: $("#ob-model").value, api_key: "", voice_enabled: false })
      .then(() => { toast("Defaults written — you can configure keys in .env", "ok"); loadStatus(); })
      .catch((e) => toast(e.message, "err"));
  });
  $("#ob-submit").addEventListener("click", async () => {
    const body = {
      provider: provSel.value,
      model: $("#ob-model").value,
      api_key: $("#ob-key").value.trim(),
      voice_enabled: $("#ob-voice").checked,
    };
    try {
      await api("/api/onboarding", "POST", body);
      root.classList.remove("show");
      toast("JAMES is set up", "ok");
      loadStatus();
    } catch (e) { toast(e.message, "err"); }
  });
}

// ---------------------------------------------------------------------------
// SSE events
// ---------------------------------------------------------------------------

function onEvent(ev) {
  switch (ev.type) {
    case "user": {
      const t = newThread(ev.text || "");
      showThinking(t);
      break;
    }
    case "thinking": {
      const t = state.threads[state.threads.length - 1];
      if (t) showThinking(t);
      break;
    }
    case "reply": {
      const t = state.threads[state.threads.length - 1];
      if (t) {
        hideThinking(t);
        streamInto(t, ev.text || "");
        setTimeout(() => finishStream(t), Math.min(6000, 250 + (ev.text || "").length * 2));
      }
      break;
    }
    case "speak": {
      const t = state.threads[state.threads.length - 1];
      if (t && !t.streaming) {
        const cap = document.createElement("div");
        cap.className = "caption";
        cap.textContent = "🔊 JAMES spoke: " + (ev.text || "");
        t.asstEl.parentElement.appendChild(cap);
      }
      break;
    }
    case "tool_start": onToolStart(ev); break;
    case "tool": onTool(ev); break;
    case "skill": {
      addCaption(`🧠 skill: ${escapeHtml(ev.text || "")}`);
      break;
    }
    case "voice": setVoiceState(ev.state); break;
    case "voice_level": setVoiceLevel(ev.level); break;
    case "voice_partial": {
      $("#voice-state").textContent = "🎙 " + (ev.text || "");
      break;
    }
    case "voice_error": toast("Voice error: " + (ev.text || ""), "err"); break;
    case "approval_requested": showApproval(ev); break;
    case "approval_resolved": {
      if (state.activeApproval && state.activeApproval.id === ev.id) {
        state.activeApproval = null;
        $("#modal-root").classList.remove("show");
      }
      break;
    }
    case "model_changed": toast(`Model → ${ev.provider}/${ev.model}`, "ok"); break;
    case "session_changed": {
      state.session = ev.name;
      $("#session-name").textContent = ev.name;
      $("#chat").innerHTML = "";
      state.threads = [];
      loadStatus();
      break;
    }
    case "session_cleared": {
      $("#chat").innerHTML = "";
      state.threads = [];
      toast("Session cleared", "ok");
      break;
    }
    default:
      break;
  }
}

function connectEvents() {
  const es = new EventSource("/api/events");
  es.onopen = () => $("#conn-dot").classList.add("on");
  es.onerror = () => $("#conn-dot").classList.remove("on");
  es.onmessage = (m) => {
    try { onEvent(JSON.parse(m.data)); } catch (_) { /* ignore malformed */ }
  };
}

// ---------------------------------------------------------------------------
// nav
// ---------------------------------------------------------------------------

$$(".nav-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    $$(".nav-btn").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    $$(".page").forEach((p) => p.classList.remove("active"));
    $("#page-" + btn.dataset.page).classList.add("active");
  });
});

// ---------------------------------------------------------------------------
// boot
// ---------------------------------------------------------------------------

loadStatus();
connectEvents();
setInterval(loadStatus, 60000); // keep model list + sessions fresh
