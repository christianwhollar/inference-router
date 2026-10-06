let page = "playground";
const tabs = [
  ["playground", "Request workbench"],
  ["models", "Model registry"],
  ["usage", "Usage and reconciliation"],
  ["benchmarks", "Measured behavior"],
];
function heading(t, d) {
  return `<div class="page-title"><div><h1>${t}</h1><p class="muted">${d}</p></div></div>`;
}
async function render(next = page) {
  page = next;
  setNav(tabs, page, guarded(render));
  if (page === "playground") await playground();
  if (page === "models") await models();
  if (page === "usage") await usage();
  if (page === "benchmarks") await benchmarks();
}
async function playground() {
  let u = await api("/usage");
  $("#main").innerHTML =
    heading(
      "Request console",
      "Apply task quality, privacy, context and budget constraints before reserving provider usage.",
    ) +
    `<div class="stats">${stat("Tenant", u.tenant)}${stat("Reserved / spent", "$" + fmt(u.reserved_or_spent_usd, 6), "Current UTC day")}${stat("Daily limit", "$" + fmt(u.daily_limit_usd), "Enforced transactionally")}${stat("Admission limit", "4", "Concurrent provider requests per process")}</div><div class="split"><section class="card"><h2>Request</h2><label for="prompt">Prompt</label><textarea id="prompt">Return JSON with answer equal to the trade_id from this record: {"trade_id":"BK-418720","amount":500}.</textarea><div class="form-grid" style="margin-top:14px"><div><label for="task">Task profile</label><select id="task"><option value="extraction">Extraction</option><option value="arithmetic">Arithmetic</option><option value="policy">Policy classification</option><option value="general">General · operator rating</option></select></div><div><label for="privacy">Data classification</label><select id="privacy"><option value="public">Public</option><option value="confidential">Confidential</option></select></div><div><label for="quality">Minimum observed accuracy / rating</label><input id="quality" type="number" min="0" max="1" step="0.05" value="0.5"></div><div><label for="budget">Request budget (USD)</label><input id="budget" type="number" min="0.000001" step="0.01" value="0.02"></div><div><label for="tokens">Maximum output tokens</label><input id="tokens" type="number" value="160" min="1" max="4096"></div><div><label for="format">Response format</label><select id="format"><option value="json">JSON</option><option value="text">Text</option></select></div></div><details><summary>Context assembly</summary><p class="small muted">Evidence blocks are prioritized and deduplicated under a conservative context bound. They are marked as untrusted text.</p><textarea id="contexts" aria-label="Context blocks">[]</textarea></details><div class="row" style="margin-top:18px"><button id="explain">Inspect eligible routes</button><button class="primary" id="complete">Run request</button></div></section><section class="card" id="response">${empty("Inspect the routing decision, then make a request.")}</section></div>`;
  const request = () => ({
    prompt: $("#prompt").value,
    task: $("#task").value,
    data_class: $("#privacy").value,
    minimum_quality: +$("#quality").value,
    budget_usd: +$("#budget").value,
    max_output_tokens: +$("#tokens").value,
    response_format: $("#format").value,
    contexts: JSON.parse($("#contexts").value),
  });
  $("#explain").onclick = guarded(async () => {
    let d = await post("/v1/route", request());
    $("#response").innerHTML =
      "<h2>Route eligibility</h2>" +
      d.routes
        .map(
          (r) =>
            `<div style="padding:15px 0;border-bottom:1px solid var(--line)"><div class="row spread"><strong>${esc(r.model)}</strong><span class="badge ${r.eligible ? "good" : "warn"}">${r.eligible ? "eligible" : "excluded"}</span></div><p class="small">${r.reasons.length ? esc(r.reasons.join(", ")) : "Meets current constraints"}</p><p class="small muted">Quality ${fmt(r.quality)} · ${esc(r.quality_source.replaceAll("_", " "))}${r.calibration_samples ? " · n=" + r.calibration_samples : ""}</p><p class="small">Reservation bound: $${fmt(r.reserved_bound_usd || 0, 6)} · ${r.included_sources.length} evidence blocks included</p></div>`,
        )
        .join("") +
      '<p class="small muted" style="margin-top:15px">Observed calibration accuracy is specific to the recorded synthetic task family. It is not a guarantee for this prompt.</p>';
  });
  $("#complete").onclick = guarded(async () => {
    let b = $("#complete");
    b.disabled = true;
    $("#response").innerHTML =
      '<div class="loading">Reserving budget and calling a provider…</div>';
    try {
      let r = await post("/v1/complete", request());
      $("#response").innerHTML =
        `<div class="row spread"><h2>${esc(r.model)}</h2><span class="badge ${r.fixture ? "warn" : "good"}">${r.fixture ? "OFFLINE FIXTURE" : "Live provider"}${r.cached ? " · cached" : ""}</span></div>${r.fixture ? '<div class="notice">This is an explicit offline fixture for exercising routing and accounting. Start with a live model configuration for inference.</div>' : ""}<pre class="code">${esc(r.text)}</pre><div class="grid two">${stat("Charged / reserved", "$" + fmt(r.charged_usd, 6))}${stat("Provider latency", fmt(r.latency_ms, 1) + " ms")}${stat("Input tokens", r.input_tokens)}${stat("Output tokens", r.output_tokens)}</div><p class="small muted">Attempted: ${esc(r.attempted.join(" → "))}. Cached responses return the original provider latency and incur no new charge.</p><details><summary>Request and reservation identifiers</summary>${jsonView({ request_id: r.request_id, reservation_id: r.reservation_id, quality_source: r.quality_source, sources: r.sources })}</details>`;
    } finally {
      b.disabled = false;
    }
  });
}
async function models() {
  let d = await api("/models");
  $("#main").innerHTML =
    heading(
      "A registry with evidence behind it.",
      "Task profiles are bound to a model digest. General ratings remain explicit operator settings.",
    ) +
    d.items
      .map(
        (m) =>
          `<section class="card" style="margin-bottom:20px"><div class="row spread"><h2>${esc(m.name)}</h2><span class="badge">${esc(m.protocol)}</span></div><div class="row"><span class="pill">Context ${fmt(m.context_tokens)}</span><span class="pill">Confidential ${m.approved_for_confidential ? "approved" : "blocked"}</span><span class="pill">Circuit failures ${m.circuit.failures}</span></div><p class="small muted">${esc(m.model || "Fixture provider")} · configured input $${m.input_per_million}/M, output $${m.output_per_million}/M tokens</p>${
            Object.keys(m.task_scores).length
              ? "<table><thead><tr><th>Task</th><th>Calibration accuracy</th><th>Cases</th><th>Median latency</th></tr></thead><tbody>" +
                Object.entries(m.task_scores)
                  .map(
                    ([task, s]) =>
                      `<tr><td>${esc(task)}</td><td>${pct(s.accuracy)}</td><td>${s.samples}</td><td>${fmt(s.p50_ms, 1)} ms</td></tr>`,
                  )
                  .join("") +
                "</tbody></table>"
              : '<p class="small muted">No task-specific calibration loaded. Operator rating: ' +
                m.quality +
                "</p>"
          }</section>`,
      )
      .join("") +
    '<div class="notice">Circuit breakers and caches are process-local. Multiple replicas share quota accounting through PostgreSQL; provider-wide distributed circuit coordination is outside this implementation.</div>';
}
async function usage() {
  let [u, d] = await Promise.all([api("/usage"), api("/reservations")]);
  $("#main").innerHTML =
    heading(
      "Close the loop on uncertain usage.",
      "A timeout can leave provider billing unknown. Reservations remain held until usage is reconciled.",
    ) +
    `<div class="stats">${stat("Reserved / spent", "$" + fmt(u.reserved_or_spent_usd, 6))}${stat("Daily limit", "$" + fmt(u.daily_limit_usd))}${stat("Recent uncertain", d.items.filter((r) => r.state === "uncertain").length, "Among the latest 100 reservations")}${stat("Recent settled", d.items.filter((r) => r.state === "settled").length)}</div><div class="card flush"><table><thead><tr><th>Time / model</th><th>State</th><th>Reserved</th><th>Actual</th><th>Reason / action</th></tr></thead><tbody>${d.items.map((r) => `<tr><td>${when(r.created_at)}<div class="small mono muted">${esc(r.model)}</div></td><td>${badge(r.state)}</td><td class="mono">$${fmt(r.reserved / 1e6, 6)}</td><td class="mono">${r.actual == null ? "unknown" : "$" + fmt(r.actual / 1e6, 6)}</td><td><span class="small">${esc(r.note)}</span>${r.state !== "settled" ? `<div><button class="small" data-reconcile="${r.id}" data-bound="${r.reserved}">Reconcile usage</button></div>` : ""}</td></tr>`).join("")}</tbody></table>${!d.items.length ? empty("Run a request to create a reservation.") : ""}</div><section class="card" id="reconcile" style="margin-top:20px" hidden></section>`;
  document.querySelectorAll("[data-reconcile]").forEach(
    (b) =>
      (b.onclick = () => {
        let id = b.dataset.reconcile;
        $("#reconcile").hidden = false;
        $("#reconcile").innerHTML =
          `<h2>Reconcile reservation</h2><p class="mono small">${id}</p><div class="form-grid"><div><label for="actual">Verified actual charge (micro-USD)</label><input id="actual" type="number" min="0" max="${b.dataset.bound}" value="0"></div><div><label for="note">Evidence / reconciliation note</label><input id="note" placeholder="Provider invoice or confirmed failure evidence"></div></div><button class="primary" id="settle" style="margin-top:15px">Record settlement</button><p class="small muted">Reviewer role required. Active calls cannot be reconciled until 120 seconds have elapsed. Repeating the same settlement does not refund twice.</p>`;
        $("#settle").onclick = guarded(async () => {
          await post("/reservations/" + id + "/reconcile", {
            actual_micros: +$("#actual").value,
            note: $("#note").value,
          });
          toast("Usage reconciled");
          await usage();
        });
      }),
  );
}
async function benchmarks() {
  let d = await api("/benchmarks");
  $("#main").innerHTML =
    heading(
      "Measured behavior, including failure.",
      "Inspect calibration data and fault-injection results before changing routing policy.",
    ) +
    (d.calibration
      ? `<section class="card"><h2>Live local model calibration</h2><p class="small muted">Twenty calibration and twenty held-out examples per task and model. Only calibration results enter the routing configuration.</p><table><thead><tr><th>Model / task</th><th>Calibration</th><th>Held out</th><th>Held-out p50</th></tr></thead><tbody>${Object.entries(
          d.calibration.summary,
        )
          .flatMap(([model, s]) =>
            ["extraction", "arithmetic", "policy"].map(
              (task) =>
                `<tr><td>${esc(model)}<div class="small muted">${task}</div></td><td>${pct(s[task + "_calibration"].accuracy)}</td><td>${pct(s[task + "_holdout"].accuracy)}</td><td>${fmt(s[task + "_holdout"].p50_ms, 1)} ms</td></tr>`,
            ),
          )
          .join(
            "",
          )}</tbody></table><p class="small muted">${esc(d.calibration.pricing)}</p></section>`
      : '<div class="notice">Live calibration results have not been installed in this build.</div>') +
    (d.reliability
      ? '<section class="card" style="margin-top:20px"><h2>Concurrency and failure study</h2>' +
        jsonView(d.reliability) +
        "</section>"
      : "") +
    '<div class="card" style="margin-top:20px"><h2>Reproduce</h2><pre class="code">python -m router.calibration --output runtime/calibration\npython -m router.reliability --output runtime/reliability.json</pre><p class="small muted">The reliability study uses controlled provider faults. Its throughput describes local gateway overhead, not real model capacity or a cloud SLA.</p></div>';
}
window.addEventListener(
  "identity-changed",
  guarded(() => render()),
);
guarded(async () => {
  await initAuth();
  await render();
})();
