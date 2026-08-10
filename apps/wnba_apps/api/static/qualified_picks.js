// The decision surface is intentionally smaller than the analysis board. Only forecasts that
// survived every production gate appear here; everything else remains inspectable below.
function qualifiedForecasts() {
  return data.forecasts
    .filter((item) => item.qualified === true)
    .sort((a, b) => Number(b.conservative_edge || -1) - Number(a.conservative_edge || -1));
}

function minutesScenarioMarkup(item) {
  const scenarios = item.minutes_scenarios || [];
  if (!scenarios.length) return '<p class="muted">Minutes scenarios unavailable.</p>';
  return `<div class="scenario-grid">${scenarios.map((scenario) => `<div><span>${esc(scenario.name)}</span><b>${fixed(scenario.minutes, 1)} min</b><small>${pct(scenario.probability)} · ±${fixed(scenario.minutes_std, 1)}</small></div>`).join("")}</div>`;
}

function renderQualifiedPicks() {
  const qualified = qualifiedForecasts();
  $("qualifiedCount").textContent = `${qualified.length} qualified`;
  if (!qualified.length) {
    const reasons = {};
    data.forecasts.forEach((item) => {
      const reason = item.system_recommendation || "unclassified";
      reasons[reason] = (reasons[reason] || 0) + 1;
    });
    $("qualifiedPicks").innerHTML = `<div class="no-action"><span class="no-action-mark">0</span><div><h3>No qualified picks</h3><p>The correct action is to wait. ${data.forecasts.length} forecasts were analysed; none cleared every freshness, availability, minutes, calibration, uncertainty, and payout gate.</p><div class="listmeta">${Object.entries(reasons).map(([reason, count]) => `<span class="tag">${count} ${esc(reason.replaceAll("_", " "))}</span>`).join("")}</div></div></div>`;
    return;
  }
  $("qualifiedPicks").innerHTML = qualified.slice(0, 8).map((item) => {
    const drivers = item.primary_drivers || [];
    const risks = item.risk_flags || [];
    return `<article class="qualified-card"><div class="qualified-head"><div><span class="side ${item.side}">${item.side}</span><h3>${esc(item.full_name)}</h3><p>${esc(item.prop_type.replaceAll("_", " "))} ${item.line} · ${esc(item.team || "team unknown")}${item.opponent ? ` vs ${esc(item.opponent)}` : ""}</p></div><div class="edge-stack"><b>${pp(item.conservative_edge)}</b><small>lower-bound edge</small></div></div><div class="evidence-strip"><div><span>Model</span><b>${pct(item.shrunk_probability)}</b></div><div><span>95% lower</span><b>${pct(item.probability_lower_bound)}</b></div><div><span>Break-even</span><b>${pct(item.breakeven_probability)}</b></div><div><span>Value proxy</span><b>${pct(item.conservative_leg_value)}</b></div></div><div class="market-track"><span>Open <b>${item.opening_line ?? "—"}</b></span><span>Current <b>${item.line}</b></span><span>Consensus <b>${item.consensus_line ?? "—"}</b></span><span>${Number(item.books || 0)} source${Number(item.books || 0) === 1 ? "" : "s"}</span><span>${ago(item.quote_seen_at)}</span><span>${Number(item.similar_settled || 0)} similar settled${Number(item.similar_settled || 0) >= 30 ? ` · ${pct(item.similar_hit_rate)} hit` : " · too thin for a rate"}</span></div>${minutesScenarioMarkup(item)}<div class="reason-columns"><div><label>Why it qualifies</label>${drivers.length ? drivers.map((driver) => `<p class="ok">+ ${esc(driver)}</p>`).join("") : '<p class="muted">The measured probability and operational gates drive this qualification.</p>'}</div><div><label>What could be wrong</label>${risks.length ? risks.map((risk) => `<p class="warn">! ${esc(risk)}</p>`).join("") : '<p class="muted">No material risk flag is currently open.</p>'}</div></div><div class="feedback"><button class="primary" onclick="addForecastPick('${item.projection_id}')">Add conservative leg</button><button class="button" onclick="openAudit('${item.projection_id}')">Full audit</button></div></article>`;
  }).join("");
}

const renderTodayBeforeQualification = renderToday;
renderToday = function () {
  renderTodayBeforeQualification();
  const qualified = qualifiedForecasts();
  const best = qualified.length ? qualified[0].conservative_edge : null;
  const operations = data.operations || {};
  const readiness = data.readiness || {};
  $("todayKpis").innerHTML =
    kpi("Qualified picks", qualified.length, qualified.length ? "Cleared every gate" : "No action is valid") +
    kpi("Conservative edge", pp(best), "95% lower bound over break-even") +
    kpi("Analysed", data.forecasts.length, "Full board remains below") +
    kpi("System", operations.status || "unknown", readiness.evaluation?.overall_ready ? "Readiness passed" : "Paper mode remains locked");
  renderQualifiedPicks();
};

renderPickCandidates = function () {
  const qualified = qualifiedForecasts().slice(0, 8);
  $("pickCandidates").innerHTML = qualified.map((item) => `<article class="slip"><div class="sliphead"><div><b>${esc(item.full_name)}</b><div class="market">${esc(item.prop_type.replaceAll("_", " "))} · ${item.side} ${item.line}</div></div><span class="mono accent">${pp(item.conservative_edge)}</span></div><div class="listmeta"><span class="tag">lower ${pct(item.probability_lower_bound)}</span><span class="tag">model ${pct(item.shrunk_probability)}</span><span class="tag">break-even ${pct(item.breakeven_probability)}</span></div><button class="button wide" onclick="addForecastPick('${item.projection_id}')">Add conservative leg</button></article>`).join("") || empty("No pick clears every evidence gate. The recommended action is no entry.");
};

addForecastPick = function (id) {
  const item = data.forecasts.find((forecast) => forecast.projection_id === id);
  if (!item || !item.qualified || item.probability_lower_bound == null) return;
  addPickLeg({player_name:item.full_name,prop_type:item.prop_type,side:item.side,line:Number(item.line),projection_id:item.projection_id,model_probability:Number(item.probability_lower_bound),extraction_confidence:1,player_id:item.player_id,team:item.team,game_id:item.game_id});
  switchView("picks");
  toast("Conservative probability added to the entry builder");
};

adoptEntry = function (ids) {
  pickDraft = [];
  ids.split("|").forEach((id) => {
    const item = data.forecasts.find((forecast) => forecast.projection_id === id);
    if (item && item.qualified && item.probability_lower_bound != null) pickDraft.push({player_name:item.full_name,prop_type:item.prop_type,side:item.side,line:Number(item.line),projection_id:item.projection_id,model_probability:Number(item.probability_lower_bound),extraction_confidence:1,player_id:item.player_id,team:item.team,game_id:item.game_id});
  });
  pickDraftSource = "board";
  renderPickBuilder();
  pricePick();
  toast("Conservatively priced entry loaded for review");
};
