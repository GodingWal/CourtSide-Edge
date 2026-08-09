// Owner approval actions for the learning loop. The panels stay read-only by default; every
// button here is a named human act recorded as "owner" with an audit reason, and each one
// calls the same lifecycle function the CLI wraps. Automation never calls these endpoints.
const CHALLENGER_FAMILIES = ["hierarchical-bayes", "state-space-role"];

function learningItems(id) {
  const el = $(id);
  return el ? Array.from(el.querySelectorAll(".listitem")) : [];
}

async function learningAction(path, body, done) {
  try {
    await api(path, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    toast(done);
    await loadAll();
  } catch (e) {
    toast(e.message);
  }
}

function approveRule(id) {
  const reason = prompt("Approval reason (recorded in the audit trail):");
  if (!reason) return;
  learningAction(`/api/learning/rules/${encodeURIComponent(id)}/approve`, { reason }, "Rule activated");
}

function retireRule(id) {
  const reason = prompt("Retirement reason (recorded in the audit trail):");
  if (!reason) return;
  learningAction(`/api/learning/rules/${encodeURIComponent(id)}/retire`, { reason }, "Rule retired");
}

function reviewProposal(id, verdict) {
  const reason = verdict === "rejected" ? prompt("Rejection reason (optional):") || "" : "";
  learningAction(
    `/api/learning/proposals/${encodeURIComponent(id)}/review`,
    { verdict, reason },
    `Proposal ${verdict}`
  );
}

function promoteExperiment(id) {
  const reason = prompt("Promotion reason (recorded in the audit trail):");
  if (!reason) return;
  learningAction(`/api/learning/experiments/${encodeURIComponent(id)}/promote`, { reason }, "Challenger promoted to champion");
}

function rollbackExperiment(id) {
  const reason = prompt("Rollback reason (recorded in the audit trail):");
  if (!reason) return;
  learningAction(`/api/learning/experiments/${encodeURIComponent(id)}/rollback`, { reason }, "Previous champion restored");
}

function abandonExperiment(id) {
  const reason = prompt("Abandon reason (recorded in the audit trail):");
  if (!reason) return;
  learningAction(`/api/learning/experiments/${encodeURIComponent(id)}/abandon`, { reason }, "Experiment abandoned");
}

function openExperiment() {
  const sel = $("openChallenger");
  if (!sel) return;
  learningAction(
    "/api/learning/experiments/open",
    { challenger: sel.value, primary_metric: "brier" },
    "Shadow experiment opened"
  );
}

function actionRow(buttons) {
  return `<div class="feedback" style="margin-top:8px">${buttons.join("")}</div>`;
}

function renderLearningActions() {
  const l = data.learning || {};

  const rules = l.rules || [];
  learningItems("analystRules").forEach((el, i) => {
    const rule = rules[i];
    if (!rule) return;
    const buttons = [];
    const verdict = (rule.backtest || {}).verdict;
    if (rule.status === "backtested" && verdict === "helpful") {
      buttons.push(`<button class="primary" onclick="approveRule('${rule.rule_id}')">Approve</button>`);
    }
    if (rule.status === "active") {
      buttons.push(`<button class="button" onclick="retireRule('${rule.rule_id}')">Retire</button>`);
    }
    if (buttons.length) el.insertAdjacentHTML("beforeend", actionRow(buttons));
  });

  const experiments = l.experiments || [];
  learningItems("experiments").forEach((el, i) => {
    const x = experiments[i];
    if (!x) return;
    const buttons = [];
    const degraded = (x.subgroups || []).some((g) => g.degraded);
    const gated = Number(x.independent_sample_size || 0) >= Number(x.minimum_sample || 0);
    if (x.status === "running") {
      buttons.push(`<button class="button" onclick="abandonExperiment('${x.experiment_id}')">Abandon</button>`);
    }
    if (x.status === "evaluated" && x.verdict === "challenger_better" && gated && !degraded) {
      buttons.push(`<button class="primary" onclick="promoteExperiment('${x.experiment_id}')">Promote to champion</button>`);
    }
    if (x.status === "promoted") {
      buttons.push(`<button class="button" onclick="rollbackExperiment('${x.experiment_id}')">Roll back</button>`);
    }
    if (buttons.length) el.insertAdjacentHTML("beforeend", actionRow(buttons));
  });

  const running = new Set(
    experiments.filter((x) => x.status === "running").map((x) => x.challenger_name)
  );
  const choices = CHALLENGER_FAMILIES.filter((name) => !running.has(name));
  const panel = $("experiments");
  if (panel && choices.length) {
    panel.insertAdjacentHTML(
      "beforeend",
      `<div class="feedback" style="margin-top:12px"><select id="openChallenger" aria-label="Challenger family">${choices
        .map((name) => `<option value="${name}">${name}</option>`)
        .join("")}</select><button class="button" onclick="openExperiment()">Open shadow experiment</button></div>`
    );
  }

  const proposals = l.proposals || [];
  learningItems("proposals").forEach((el, i) => {
    const proposal = proposals[i];
    if (!proposal || proposal.status !== "proposed") return;
    el.insertAdjacentHTML(
      "beforeend",
      actionRow([
        `<button class="primary" onclick="reviewProposal('${proposal.proposal_id}','approved')">Approve</button>`,
        `<button class="button" onclick="reviewProposal('${proposal.proposal_id}','rejected')">Reject</button>`,
      ])
    );
  });
}

// Wrap the base renderer so every refresh re-arms the buttons after the panels re-render.
const renderLearningBase = renderLearning;
renderLearning = function () {
  renderLearningBase();
  renderLearningActions();
};
renderLearningActions();
