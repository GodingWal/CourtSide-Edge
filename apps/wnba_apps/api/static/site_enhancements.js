// Cross-page reliability and navigation enhancements kept separate from the rendering modules.
let forecastPageSize = 120;
let lastSuccessfulRefresh = null;

const baseSwitchView = switchView;
switchView = function (name) {
  baseSwitchView(name);
  if (name === "history") $("pageTitle").textContent = "Historical box scores";
  if (location.hash !== `#/${name}`) history.pushState({ view: name }, "", `#/${name}`);
};

function routeFromLocation() {
  const requested = location.hash.replace(/^#\//, "");
  if (document.querySelector(`#view-${requested}`)) {
    baseSwitchView(requested);
    if (requested === "history") $("pageTitle").textContent = "Historical box scores";
  }
}
window.addEventListener("popstate", routeFromLocation);
document.querySelectorAll(".nav").forEach((button) => {
  button.onclick = () => switchView(button.dataset.view);
});
routeFromLocation();

renderForecasts = function () {
  const original = data.forecasts;
  const query = $("search").value.toLowerCase();
  const market = $("marketFilter").value;
  const team = $("teamFilter").value;
  const side = $("sideFilter").value;
  const quality = $("qualityFilter").value;
  const filtered = original.filter((item) =>
    (`${item.full_name} ${item.prop_type}`).toLowerCase().includes(query) &&
    (market === "all" || item.prop_type === market) &&
    (team === "all" || item.team === team || item.opponent === team) &&
    (side === "all" || item.side === side) &&
    (quality === "all" || item.system_recommendation === quality)
  );
  const visible = filtered.slice(0, forecastPageSize);
  $("forecastRows").innerHTML = visible.map((item) => `<tr><td><div class="player">${esc(item.full_name)}</div><div class="market">${esc(item.prop_type.replaceAll("_", " "))}${item.team ? ` · ${esc(item.team)}${item.opponent ? ` vs ${esc(item.opponent)}` : ""}` : ""}</div></td><td>${esc(item.source)}</td><td class="mono">${item.line}</td><td class="mono">${fixed(item.mean, 1)}</td><td><span class="side ${item.side}">${item.side}</span></td><td><b class="mono">${pct(item.predicted_probability)}</b><div class="market">shrunk ${pct(item.shrunk_probability)}</div></td><td><b class="mono ${item.edge > 0 ? "ok" : item.edge != null ? "bad" : ""}">${pp(item.edge)}</b><div class="market">break-even ${pct(item.breakeven_probability)}</div></td><td><span class="status ${item.system_recommendation}">${esc(item.system_recommendation)}</span></td><td><button class="inspect" onclick="addForecastPick('${item.projection_id}')">+ Pick</button> <button class="inspect" onclick="openAudit('${item.projection_id}')">Audit</button></td></tr>`).join("") || `<tr><td colspan="9">${empty("No forecasts match these filters.")}</td></tr>`;
  $("forecastCount").textContent = filtered.length > forecastPageSize
    ? `${Math.min(forecastPageSize, filtered.length)} of ${filtered.length} matches`
    : `${filtered.length} / ${original.length}`;
  $("loadMoreForecasts").hidden = filtered.length <= forecastPageSize;
};

$("loadMoreForecasts").onclick = () => {
  forecastPageSize += 120;
  renderForecasts();
};
[$("search"), $("marketFilter"), $("teamFilter"), $("sideFilter"), $("qualityFilter")].forEach((element) => {
  element.addEventListener(element.tagName === "INPUT" ? "input" : "change", () => {
    forecastPageSize = 120;
  });
});

const endpointBindings = [
  ["forecasts", "forecasts", (value) => value.forecasts || []],
  ["picks", "picks", (value) => value],
  ["archive", "archive", (value) => value],
  ["operations", "operations", (value) => value],
  ["performance", "performance", (value) => value],
  ["backtests/latest", "backtest", (value) => value],
  ["readiness", "readiness", (value) => value],
  ["validation", "validation", (value) => value],
  ["learning", "learning", (value) => value],
  ["injuries", "injuries", (value) => value],
  ["operations/timeline", "timeline", (value) => value],
];

function renderAvailablePanels() {
  const renderers = [renderToday, renderPickBuilder, renderPickHistory, renderPickCandidates, renderMarkets, renderResearchQueue, renderValidation, renderLearning, renderOperations];
  renderers.forEach((renderer) => {
    try { renderer(); } catch (error) { console.warn(`Panel render failed: ${renderer.name}`, error); }
  });
}

loadAll = async function () {
  $("connection").textContent = "Refreshing";
  const settled = await Promise.allSettled(endpointBindings.map(([path]) => api(`/api/${path}`)));
  const failures = [];
  settled.forEach((result, index) => {
    const [, key, transform] = endpointBindings[index];
    if (result.status === "fulfilled") data[key] = transform(result.value);
    else failures.push(endpointBindings[index][0]);
  });
  if (settled.some((result) => result.status === "fulfilled")) lastSuccessfulRefresh = new Date();
  const markets = [...new Set(data.forecasts.map((item) => item.prop_type))].sort();
  $("marketFilter").innerHTML = '<option value="all">All markets</option>' + markets.map((item) => `<option value="${esc(item)}">${esc(item.replaceAll("_", " "))}</option>`).join("");
  const teams = [...new Set(data.forecasts.flatMap((item) => [item.team, item.opponent]).filter(Boolean))].sort();
  $("teamFilter").innerHTML = '<option value="all">All teams</option>' + teams.map((item) => `<option value="${esc(item)}">${esc(item)}</option>`).join("");
  renderAvailablePanels();
  await loadLearningEvidence();
  const freshness = lastSuccessfulRefresh ? `updated ${ago(lastSuccessfulRefresh)}` : "never updated";
  $("connection").textContent = failures.length ? `Partial · ${failures.length} panel${failures.length === 1 ? "" : "s"} unavailable · ${freshness}` : `Live · ${freshness}`;
};

$("refresh").onclick = loadAll;
setInterval(() => {
  if (document.visibilityState === "visible") loadAll();
}, 60_000);
loadAll();
