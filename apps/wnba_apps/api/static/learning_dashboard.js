// Evidence-first Learning page. These reads are independent of the main dashboard refresh so
// an unavailable memory or attribution query cannot blank forecasts or owner controls.
let learningEvidence = { errors: null, memory: null };

async function loadLearningEvidence() {
  const requests = [
    ["errors", "/api/learning/error-graph"],
    ["memory", "/api/learning/memory"],
  ];
  const settled = await Promise.allSettled(requests.map(([, path]) => api(path)));
  settled.forEach((result, index) => {
    const key = requests[index][0];
    learningEvidence[key] = result.status === "fulfilled" ? result.value : { unavailable: true };
  });
  renderLearningDashboard();
}

function renderLearningDashboard() {
  const learning = data.learning || {};
  const experiments = learning.experiments || [];
  const rules = learning.rules || [];
  const errors = learningEvidence.errors || {};
  const memory = learningEvidence.memory || {};
  const openDrift = ((memory.failure_memory || {}).open_drift_incidents || []).reduce(
    (total, item) => total + Number(item.events || 0),
    0
  );
  const settledErrors = (errors.episodes || []).length;
  const running = experiments.filter((item) => item.status === "running").length;
  const decisions = experiments.filter((item) => ["promoted", "rolled_back", "abandoned"].includes(item.status)).length;

  $("learningHealth").innerHTML =
    kpi("Settled diagnoses", settledErrors, "Latest outcome-attribution window") +
    kpi("Open drift", openDrift, openDrift ? "Confidence is being reduced" : "No active drift response") +
    kpi("Shadow tests", running, "Never auto-promoted") +
    kpi("Human decisions", decisions, "Audited experiment outcomes");

  const attention = [];
  experiments.forEach((item) => {
    const progress = Number(item.independent_sample_size || 0);
    const target = Number(item.minimum_sample || 0);
    if (item.status === "evaluated") {
      attention.push({
        level: item.verdict === "challenger_better" ? "ok" : "warn",
        title: `${item.challenger_name}: ${item.verdict || "review required"}`,
        detail: `${progress.toLocaleString()} independent markets; a human must record the decision.`,
      });
    } else if (item.status === "running" && target > 0 && progress / target >= 0.8) {
      attention.push({
        level: "warn",
        title: `${item.challenger_name} nearing review`,
        detail: `${progress.toLocaleString()} / ${target.toLocaleString()} independent markets.`,
      });
    }
  });
  if (openDrift) attention.push({ level: "bad", title: "Active model drift", detail: `${openDrift} unresolved drift event(s) are reducing confidence.` });
  rules.filter((item) => item.status === "backtested").forEach((item) => attention.push({ level: "warn", title: `Rule review: ${item.title}`, detail: "Backtest finished; owner approval is pending." }));
  $("attentionItems").innerHTML = attention.length
    ? attention.map((item) => `<div class="listitem attention-item"><span class="status ${item.level}">${item.level === "bad" ? "urgent" : "review"}</span><b>${esc(item.title)}</b><p>${esc(item.detail)}</p></div>`).join("")
    : empty("Nothing requires an owner decision right now.");

  const shares = errors.failure_shares || [];
  $("errorLab").innerHTML = shares.length
    ? shares.map((item) => `<button class="evidence-row" onclick="switchView('today')"><span>${esc(String(item.primary_error).replaceAll("_", " "))}</span><b>${item.share_pct}%</b><small>${item.episodes} settled episode(s)</small></button>`).join("")
    : empty(errors.unavailable ? "Error evidence is temporarily unavailable." : "No settled errors have been attributed yet.");

  const failureMix = ((memory.failure_memory || {}).recent_error_mix || []);
  $("memoryPanel").innerHTML = memory.unavailable
    ? empty("Learning memory is temporarily unavailable.")
    : row("Procedures", (memory.procedural_memory || []).length) +
      row("Causal hypotheses", (memory.causal_memory || []).length) +
      row("Stored precedents", (memory.episodic_memory || {}).stored_precedents || 0) +
      row("Recent failure types", failureMix.length);

  const history = [];
  experiments.forEach((item) => {
    if (item.approved_at) history.push({ at: item.approved_at, kind: "owner", title: `Promoted ${item.challenger_name}`, detail: item.promotion_reason || "Recorded promotion" });
    if (item.rolled_back_at) history.push({ at: item.rolled_back_at, kind: "owner", title: `Rolled back ${item.challenger_name}`, detail: item.rollback_reason || "Recorded rollback" });
    if (item.evaluated_at) history.push({ at: item.evaluated_at, kind: "settled evidence", title: `${item.challenger_name}: ${item.verdict || "evaluated"}`, detail: `${Number(item.independent_sample_size || 0).toLocaleString()} independent markets` });
  });
  history.sort((a, b) => new Date(b.at) - new Date(a.at));
  $("learningHistory").innerHTML = history.length
    ? history.slice(0, 30).map((item) => `<div class="decision-event"><span class="tag ${item.kind === "owner" ? "warn" : "ok"}">${esc(item.kind)}</span><div><b>${esc(item.title)}</b><p>${esc(item.detail)} · ${date(item.at)}</p></div></div>`).join("")
    : empty("No experiment evidence or owner decisions have been recorded yet.");

  learningItems("experiments").forEach((element, index) => {
    const item = experiments[index];
    if (!item) return;
    const interval = ((item.metrics || {}).paired_gain_confidence_interval || {});
    if (interval.lower != null && interval.upper != null) {
      const subgroups = (item.subgroups || []).filter((group) => group.conclusive);
      element.insertAdjacentHTML("beforeend", `<p class="evidence-note"><b>Adjusted 95% gain interval:</b> ${fixed(interval.lower, 4)} to ${fixed(interval.upper, 4)} log-loss points · paired cluster bootstrap by game, player and date.</p>${subgroups.length ? `<details><summary>Segment matrix (${subgroups.length})</summary><div class="segment-grid">${subgroups.map((group) => `<span>${esc(`${group.dimension}: ${group.value}`)}</span><b class="${group.degraded ? "bad" : ""}">${fixed(group.log_loss_gain, 4)}</b><small>${group.sample_size} markets · ${fixed(group.gain_ci_lower, 4)} to ${fixed(group.gain_ci_upper, 4)}</small>`).join("")}</div></details>` : ""}`);
    }
    if (item.status !== "running") return;
    const progress = Number(item.independent_sample_size || 0);
    const target = Math.max(1, Number(item.minimum_sample || 1));
    const percentage = Math.min(100, Math.round((progress / target) * 100));
    element.insertAdjacentHTML("beforeend", `<div class="sample-progress"><span style="width:${percentage}%"></span></div><p class="muted">${percentage}% of evidence target. ETA appears after the first two slate dates.</p>`);
  });
}

const renderLearningWithActions = renderLearning;
renderLearning = function () {
  renderLearningWithActions();
  renderLearningDashboard();
};

loadLearningEvidence();
