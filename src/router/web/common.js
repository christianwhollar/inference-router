const $ = (s) => document.querySelector(s);
const esc = (x) =>
  String(x ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const fmt = (x, d = 2) =>
  Number(x).toLocaleString(undefined, { maximumFractionDigits: d });
const pct = (x) => (100 * x).toFixed(1) + "%";
const when = (x) => (x ? new Date(x * 1000).toLocaleString() : "—");
let key = sessionStorage.getItem("api-key") || "";
let config = {};
function toast(msg, bad = false) {
  let t = $("#toast");
  t.textContent = msg;
  t.hidden = false;
  t.style.background = bad ? "#852f39" : "#173c47";
  clearTimeout(window.toastTimer);
  window.toastTimer = setTimeout(() => (t.hidden = true), 6000);
}
async function api(path, options = {}) {
  let headers = {
    "Content-Type": "application/json",
    ...(key ? { Authorization: "Bearer " + key } : {}),
    ...options.headers,
  };
  let r = await fetch(path, { ...options, headers });
  let body = await r.json();
  if (!r.ok) {
    let detail =
      typeof body.detail === "string"
        ? body.detail
        : JSON.stringify(body.detail);
    throw Error(detail || r.statusText);
  }
  return body;
}
function post(path, data, headers = {}) {
  return api(path, { method: "POST", body: JSON.stringify(data), headers });
}
function stat(label, value, note = "") {
  return `<div class="stat"><span class="label">${esc(label)}</span><strong class="value">${esc(value)}</strong><small>${esc(note)}</small></div>`;
}
function badge(text) {
  let good = ["approved", "matched", "complete", "succeeded", "ready"].includes(
      text,
    ),
    bad = ["failed", "rejected", "escalate"].includes(text);
  return `<span class="badge ${good ? "good" : bad ? "bad" : "warn"}">${esc(text.replaceAll("_", " "))}</span>`;
}
function empty(text) {
  return `<div class="empty">${esc(text)}</div>`;
}
function jsonView(value) {
  return `<pre class="code">${esc(JSON.stringify(value, null, 2))}</pre>`;
}
function setNav(items, active, fn) {
  $("#nav").innerHTML = items
    .map(
      ([id, label]) =>
        `<button data-nav="${id}" class="${id === active ? "active" : ""}">${label}</button>`,
    )
    .join("");
  $("#nav").onclick = (e) => {
    let b = e.target.closest("[data-nav]");
    if (b) fn(b.dataset.nav);
  };
}
async function initAuth() {
  config = await api("/app-config");
  let el = $("#auth");
  if (config.demo) {
    if (!key) key = "demo-analyst";
    el.innerHTML =
      '<span class="badge">LOCAL DEMO</span><select id="role" aria-label="Demo identity"><option value="demo-analyst">Alpha · analyst</option><option value="demo-reviewer">Alpha · reviewer</option><option value="demo-beta">Beta · analyst</option></select>';
    $("#role").value = key;
    $("#role").onchange = () => {
      key = $("#role").value;
      sessionStorage.setItem("api-key", key);
      window.dispatchEvent(new Event("identity-changed"));
    };
  } else {
    el.innerHTML =
      '<input id="key" type="password" placeholder="API key" aria-label="API key"><button id="connect">Connect</button>';
    $("#key").value = key;
    $("#connect").onclick = () => {
      key = $("#key").value;
      sessionStorage.setItem("api-key", key);
      window.dispatchEvent(new Event("identity-changed"));
    };
  }
}
function guarded(fn) {
  return async (...args) => {
    try {
      return await fn(...args);
    } catch (e) {
      toast(e.message, true);
    }
  };
}
