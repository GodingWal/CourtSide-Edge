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

  const recommendationCounts = {};
  (data.forecasts || []).forEach((item) => {
    const status = item.qualified ? "qualified" : (item.system_recommendation || "unclassified");
    recommendationCounts[status] = (recommendationCounts[status] || 0) + 1;
  });
  const statusOrder = ["qualified", "blocked_stale_quote", "blocked_data_quality", "blocked_by_skeptic", "blocked_calibration", "blocked_exposure", "declined_no_edge"];
  const statusLabels = {
    qualified: "Qualified",
    blocked_stale_quote: "Quote freshness",
    blocked_data_quality: "Data / market coverage",
    blocked_by_skeptic: "Availability / restriction",
    blocked_calibration: "Calibration / uncertainty / disagreement",
    blocked_exposure: "Exposure policy",
    declined_no_edge: "Insufficient lower-bound edge",
    unclassified: "Unclassified",
  };
  const funnelRows = [...statusOrder, ...Object.keys(recommendationCounts).filter((key) => !statusOrder.includes(key))]
    .filter((key) => recommendationCounts[key])
    .map((key) => row(statusLabels[key] || key.replaceAll("_", " "), recommendationCounts[key], key === "qualified" ? "ok" : "warn"));
  $("qualificationFunnel").innerHTML = funnelRows.join("") || empty("No current forecasts are available to classify.");

  const nextEvidence = [];
  if (recommendationCounts.blocked_stale_quote) nextEvidence.push(["Refresh quotes", `${recommendationCounts.blocked_stale_quote} forecast(s) need a quote no older than 30 minutes.`]);
  if (recommendationCounts.blocked_data_quality) nextEvidence.push(["Expand consensus coverage", `${recommendationCounts.blocked_data_quality} forecast(s) need two independent sources or cleaner inputs.`]);
  if (recommendationCounts.blocked_by_skeptic) nextEvidence.push(["Resolve player role", `${recommendationCounts.blocked_by_skeptic} forecast(s) have availability or minutes-restriction risk.`]);
  if (recommendationCounts.blocked_calibration) nextEvidence.push(["Collect and diagnose evidence", `${recommendationCounts.blocked_calibration} forecast(s) lack fitted shrinkage, have unstable minutes, or have component conflict.`]);
  if (recommendationCounts.blocked_exposure) nextEvidence.push(["Reduce concentration", `${recommendationCounts.blocked_exposure} forecast(s) exceed the player, team, or game exposure policy.`]);
  if (recommendationCounts.declined_no_edge) nextEvidence.push(["Wait for a better line", `${recommendationCounts.declined_no_edge} forecast(s) do not clear break-even with a 95% lower bound.`]);
  $("qualificationActions").innerHTML = nextEvidence.length
    ? nextEvidence.map(([title, detail]) => `<div class="listitem"><b>${esc(title)}</b><p>${esc(detail)}</p></div>`).join("")
    : empty(recommendationCounts.qualified ? "Every current forecast shown here cleared its gates." : "Load a current board to generate prioritized actions.");

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
