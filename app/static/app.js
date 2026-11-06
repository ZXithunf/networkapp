// NetPulse front-end: plain JS, no framework, no build step.
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);

// ---------- helpers ----------

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  let data = null;
  try { data = await res.json(); } catch { /* non-JSON body */ }
  if (!res.ok) {
    const detail = data && data.detail ? data.detail : `HTTP ${res.status}`;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data;
}

// Build DOM nodes with textContent only - never innerHTML with API data (XSS-safe).
function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined && text !== null) node.textContent = String(text);
  if (className) node.className = className;
  return node;
}

function keyValueTable(rows) {
  const table = el("table");
  for (const [key, value] of rows) {
    const tr = el("tr");
    tr.append(el("th", key), el("td", value ?? "—"));
    table.append(tr);
  }
  return table;
}

function gridTable(headers, rows) {
  const table = el("table", null, "grid");
  const head = el("tr");
  headers.forEach((h) => head.append(el("th", h)));
  const thead = el("thead");
  thead.append(head);
  table.append(thead);
  const body = el("tbody");
  rows.forEach((r) => {
    const tr = el("tr");
    r.forEach((cell) => {
      if (cell instanceof Node) { const td = el("td"); td.append(cell); tr.append(td); }
      else tr.append(el("td", cell ?? "—"));
    });
    body.append(tr);
  });
  table.append(body);
  const wrap = el("div", null, "scroll");
  wrap.append(table);
  return wrap;
}

function badge(ok) {
  return el("span", ok ? "UP" : "DOWN", ok ? "badge ok" : "badge bad");
}

// Wire a form: run `handler`, show the result or the error inline.
function bindForm(id, handler) {
  const form = $(`#form-${id}`);
  const panel = $(`#panel-${id}`);
  const errorBox = $(".error", panel);
  const result = $(".result", panel);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = $("button[type=submit]", form);
    button.disabled = true;
    errorBox.hidden = true;
    result.replaceChildren(el("p", "Working…", "muted"));
    try {
      result.replaceChildren(await handler(new FormData(form)));
    } catch (err) {
      result.replaceChildren();
      errorBox.textContent = err.message;
      errorBox.hidden = false;
    } finally {
      button.disabled = false;
    }
  });
}

// ---------- tabs ----------

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => {
      const active = t === tab;
      t.classList.toggle("active", active);
      t.setAttribute("aria-selected", String(active));
      $(`#panel-${t.dataset.tab}`).hidden = !active;
    });
    if (tab.dataset.tab === "uptime") loadHistory();
  });
});

// ---------- features ----------

bindForm("subnet", async (form) => {
  const d = await api(`/api/subnet?cidr=${encodeURIComponent(form.get("cidr"))}`);
  return keyValueTable([
    ["Network", `${d.network}/${d.prefix}`],
    ["Broadcast", d.broadcast],
    ["Netmask", d.netmask],
    ["Wildcard", d.wildcard],
    ["Prefix", `/${d.prefix}`],
    ["First host", d.first_host],
    ["Last host", d.last_host],
    ["Usable hosts", d.usable_hosts.toLocaleString()],
  ]);
});

bindForm("vlsm", async (form) => {
  const hosts = String(form.get("hosts"))
    .split(",").map((s) => s.trim()).filter(Boolean).map(Number);
  if (hosts.some((h) => !Number.isInteger(h))) throw new Error("Hosts must be whole numbers");
  const d = await api("/api/vlsm", {
    method: "POST",
    body: JSON.stringify({ base: form.get("base"), hosts }),
  });
  const box = el("div");
  box.append(gridTable(
    ["Name", "Needed", "Subnet", "Mask", "First host", "Last host", "Broadcast", "Usable", "Wasted"],
    d.allocations.map((a) => [a.name, a.hosts_requested, a.cidr, a.netmask,
      a.first_host, a.last_host, a.broadcast, a.usable_hosts, a.wasted]),
  ));
  const left = d.leftover.length ? d.leftover.join(", ") : "none";
  box.append(el("p", `Leftover: ${left} (${d.leftover_addresses} addresses)`, "muted"));
  return box;
});

bindForm("dns", async (form) => {
  const q = new URLSearchParams({ domain: form.get("domain"), type: form.get("type") });
  const d = await api(`/api/dns?${q}`);
  if (!d.records.length) return el("p", `No ${d.type} records for ${d.domain}.`, "muted");
  return gridTable(["Type", "TTL", "Value"], d.records.map((r) => [d.type, d.ttl, r]));
});

bindForm("uptime", async (form) => {
  const d = await api("/api/check", {
    method: "POST",
    body: JSON.stringify({ url: form.get("url") }),
  });
  loadHistory();
  return keyValueTable([
    ["URL", d.url],
    ["Status", d.ok ? "UP" : "DOWN"],
    ["HTTP code", d.status_code],
    ["Response time", `${d.response_ms} ms`],
    ["Error", d.error],
  ]);
});

async function loadHistory() {
  const box = $("#history");
  try {
    const rows = await api("/api/checks");
    if (!rows.length) { box.replaceChildren(el("p", "No checks yet.", "muted")); return; }
    box.replaceChildren(gridTable(
      ["Time (UTC)", "URL", "Status", "Code", "ms"],
      rows.map((r) => [r.checked_at.replace("T", " ").replace("+00:00", ""),
        r.url, badge(r.ok), r.status_code, r.response_ms]),
    ));
  } catch (err) {
    box.replaceChildren(el("p", err.message, "error"));
  }
}

// ---------- footer version ----------

api("/health")
  .then((d) => { $("#version").textContent = `· ${d.version}`; })
  .catch(() => { $("#version").textContent = "(offline)"; });
