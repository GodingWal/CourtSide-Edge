// Trust diagnostics and point-in-time replay load independently so a new artifact table cannot
// blank the live decision board while a migration or weekly fitting job is pending.
let trustEvidence = { available: false };
let replayArchive = [];

function trustEmpty(message) { return empty(message || "This artifact has not accumulated enough evidence yet."); }

function renderTrustDashboard() {
  const trust = trustEvidence || {};
  if (!trust.available) {
    ["trustStatus", "riskCoverage", "conformalCoverage", "clvTrend", "sourceReliability", "featureAblations", "segmentCalibration", "jointGames"].forEach((id) => {
      $(id).innerHTML = trustEmpty(trust.reason || "Trust fitting has not run yet.");
    });
    return;
  }
  const evidence = trust.evidence_summary || {};
  $("trustStatus").innerHTML = `<div class="evidence-summary"><div><span>Independent markets</span><b>${Number(evidence.independent_markets || 0).toLocaleString()}</b><small>${Number(evidence.raw_episodes || 0).toLocaleString()} raw episodes</small></div><div><span>Independent games</span><b>${Number(evidence.independent_games || 0).toLocaleString()}</b><small>Shared outcomes are discounted</small></div><div><span>Latest forecast</span><b>${evidence.latest_forecast ? ago(evidence.latest_forecast) : "never"}</b><small>Production forecast evidence</small></div><div><span>Latest settlement</span><b>${evidence.latest_settlement ? ago(evidence.latest_settlement) : "never"}</b><small>Outcome evidence</small></div></div><div class="artifact-grid">${(trust.artifact_status || []).map((item) => `<div class="artifact ${item.ready ? "ready" : "provisional"}"><span>${esc(item.name)}</span><b>${item.ready ? "measured" : "provisional"}</b><small>n=${Number(item.sample_size || 0).toLocaleString()} / ${Number(item.minimum_sample || 0).toLocaleString()} minimum &middot; ${item.calculated_at ? ago(item.calculated_at) : "never fitted"}</small></div>`).join("")}</div><p class="muted">${esc(trust.independence_note || "")}</p>`;
  const pooled = (trust.policies || []).find((item) => item.segment === "all");
  const curve = pooled?.risk_coverage || [];
  $("riskCoverage").innerHTML = curve.length ? curve.map((point) => `<div class="coverage-row"><span>${pct(point.coverage)} published</span><i><b style="width:${Math.min(100, Number(point.log_loss || 0) * 100)}%"></b></i><strong>${fixed(point.log_loss, 3)} loss</strong><small>${point.selected} forecasts · calibration ${pct(point.calibration_error)}</small></div>`).join("") : trustEmpty();
  $("conformalCoverage").innerHTML = (trust.intervals || []).slice(0, 18).map((item) => `<div class="metricrow"><span>${esc(item.segment.replaceAll("_", " "))}${item.used_fallback ? " · pooled fallback" : ""}</span><b class="${Number(item.empirical_coverage) + .02 < Number(item.target_coverage) ? "bad" : "ok"}">${pct(item.empirical_coverage)} / ${pct(item.target_coverage)} · ±${fixed(item.radius, 2)}</b></div>`).join("") || trustEmpty();
  $("clvTrend").innerHTML = (trust.closing_line_value || []).slice(-12).map((item) => row(new Date(item.week).toLocaleDateString(undefined, {month:"short",day:"numeric"}), `${Number(item.episodes).toLocaleString()} markets / ${Number(item.independent_games || 0).toLocaleString()} games &middot; ${Number(item.mean_normalized_line_value || 0) >= 0 ? "+" : ""}${pct(item.mean_normalized_line_value)} normalized CLV &middot; ${pct(item.positive_rate)} positive`, Number(item.mean_normalized_line_value || 0) > 0 ? "ok" : "warn")).join("") || trustEmpty("Closing lines have not settled yet.");
  $("sourceReliability").innerHTML = (trust.sources || []).map((item) => `<div class="metricrow"><span>${esc(item.source)} / ${esc(item.prop_type.replaceAll("_", " "))} &middot; n=${item.sample_size}</span><b>${pct(item.reliability_weight)} trust &middot; ${pct(item.freshness_rate)} fresh &middot; normalized close error ${pct(item.mean_absolute_error)}</b></div>`).join("") || trustEmpty();
  $("featureAblations").innerHTML = (trust.ablations || []).map((item) => `<div class="listitem"><b>${esc(item.feature_name.replaceAll("_", " "))}</b><p>${esc(item.verdict)} · paired n=${item.sample_size} · gain ${fixed(item.mean_log_loss_gain, 4)}</p><div class="listmeta"><span class="tag ${item.verdict === "helpful" ? "ok" : item.verdict === "harmful" ? "bad" : "warn"}">95% adjusted ${fixed(item.confidence_lower, 4)} to ${fixed(item.confidence_upper, 4)}</span></div></div>`).join("") || trustEmpty();
  $("segmentCalibration").innerHTML = (trust.segments || []).slice(0, 24).map((item) => `<div class="metricrow"><span>${esc(item.prop_type.replaceAll("_", " "))} &middot; ${esc(item.role_state.replaceAll("_", " "))} &middot; ${item.forecasts} markets / ${item.independent_games} games / ESS ${fixed(item.effective_sample_size, 1)}</span><b class="${Math.abs(Number(item.predicted) - Number(item.observed)) > .08 ? "bad" : ""}">${pct(item.predicted)} &rarr; ${pct(item.observed)} &middot; Brier ${fixed(item.brier, 3)}</b></div>`).join("") || trustEmpty();
  $("jointGames").innerHTML = (trust.joint_games || []).map((item) => `<div class="listitem"><b>${date(item.scheduled_tipoff)} · ${(item.player_keys || []).length.toLocaleString()} player markets</b><p>${Number(item.simulations).toLocaleString()} shared-state simulations · pace ${fixed((item.scenario_summary || {}).mean_pace, 1)} (${fixed((item.scenario_summary || {}).pace_p10, 1)}–${fixed((item.scenario_summary || {}).pace_p90, 1)}) · blowout ${pct((item.scenario_summary || {}).realized_blowout_rate)}</p></div>`).join("") || trustEmpty();
}

async function loadTrustEvidence() {
  try { trustEvidence = await api("/api/learning/trust"); }
  catch (error) { trustEvidence = {available:false, reason:error.message}; }
  renderTrustDashboard();
}

function renderReplayList() {
  const query = ($("replaySearch")?.value || "").trim().toLowerCase();
  const outcome = $("replayOutcome")?.value || "all";
  const filtered = replayArchive.filter((item) => {
    const label = `${item.full_name} ${item.prop_type}`.toLowerCase();
    const state = item.system_recommendation !== "candidate" ? "withheld" : item.hit == null ? "open" : item.hit ? "hit" : "miss";
    return (!query || label.includes(query)) && (outcome === "all" || outcome === state);
  });
  $("replayCount").textContent = `${filtered.length}/${replayArchive.length}`;
  $("replayList").innerHTML = filtered.map((item) => `<button class="replay-item" onclick="openReplay('${item.episode_id}')"><span><b>${esc(item.full_name)}</b><small>${esc(item.prop_type.replaceAll("_", " "))} &middot; ${item.side} ${item.line} &middot; ${date(item.forecast_timestamp)}</small></span><strong class="${item.system_recommendation !== "candidate" ? "warn" : item.hit ? "ok" : item.was_voided || item.was_push ? "warn" : "bad"}">${item.system_recommendation !== "candidate" ? "withheld" : item.was_voided ? "void" : item.was_push ? "push" : item.hit == null ? "open" : item.hit ? "hit" : "miss"}</strong></button>`).join("") || trustEmpty("No decisions match these filters.");
}

async function openReplay(id) {
  $("replayDetail").innerHTML = '<div class="empty">Reconstructing point-in-time evidence…</div>';
  if (window.matchMedia("(max-width: 1100px)").matches) {
    $("replayDetail").scrollIntoView({behavior:"smooth", block:"start"});
  }
  try {
    const payload = await api(`/api/replays/${id}`), item = payload.episode || {}, features = item.features || {}, qualification = payload.qualification || {};
    const available = (payload.line_history || []).filter((line) => line.phase === "available_at_decision");
    const later = (payload.line_history || []).filter((line) => line.phase === "after_decision");
    $("replayDetail").innerHTML = `<label>Frozen decision</label><h2>${esc(item.full_name)}</h2><p class="muted">${esc(item.prop_type.replaceAll("_", " "))} · ${item.side} ${item.line} · ${date(item.forecast_timestamp)}</p><div class="evidence-strip"><div><span>Probability</span><b>${pct(item.predicted_probability)}</b></div><div><span>Shrunk</span><b>${pct(item.shrunk_probability)}</b></div><div><span>Break-even</span><b>${pct(item.breakeven_probability)}</b></div><div><span>Decision</span><b>${esc(item.system_recommendation)}</b></div></div><p>${esc(item.decision_reason || "No recorded gate explanation")}</p><div class="card pad replay-gates"><label>Qualification evidence available at decision</label>${[["Base decision",item.system_recommendation === "candidate"],["Learned abstention",qualification.selective_policy_pass],["Future conformal coverage",qualification.conformal_evidence_pass],["Forecast interval clears line",qualification.conformal_direction_pass],["Source reliability",qualification.source_reliability_pass]].map(([name,pass]) => row(name, pass ? "pass" : "withhold", pass ? "ok" : "warn")).join("")}</div><div class="twocol replay-columns"><div><label>Known at decision</label>${available.map((line) => row(`${line.source} · ${date(line.observed_at)}`, line.line)).join("") || trustEmpty()}</div><div><label>After decision</label>${later.map((line) => row(`${line.source} · ${date(line.observed_at)}`, line.line)).join("") || trustEmpty("No later movement archived.")}</div></div><div class="auditgrid">${[["Projected",fixed(item.projected_mean,1)],["Actual",item.actual_stat == null ? "open" : fixed(item.actual_stat,1)],["Minutes",item.actual_minutes == null ? "open" : fixed(item.actual_minutes,1)],["Closing line",item.closing_line ?? "unavailable"],["Role",features.role_state || "unknown"],["Model disagreement",pct(item.model_disagreement)]].map(([name,value]) => `<div class="audit"><span>${esc(name)}</span><b>${esc(value)}</b></div>`).join("")}</div><div class="card pad"><label>Component votes</label>${(payload.components || []).map((component) => row(component.component_name.replaceAll("_", " "), `${pct(component.probability_over)} over · ${pct(component.weight)} weight`)).join("") || trustEmpty()}</div>`;
  } catch (error) { $("replayDetail").innerHTML = trustEmpty(error.message); }
}

async function loadReplays() {
  try { const payload = await api("/api/replays?limit=100"); replayArchive = payload.replays || []; }
  catch (error) { replayArchive = []; }
  renderReplayList();
}

loadTrustEvidence();
loadReplays();
$("replaySearch")?.addEventListener("input", renderReplayList);
$("replayOutcome")?.addEventListener("change", renderReplayList);
