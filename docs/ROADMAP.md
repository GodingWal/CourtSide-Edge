# Delivery roadmap

Updated: 2026-08-05

The platform is in **Phase 3: private shadow analysis**. Live and rescued historical markets,
five-component forecasts, rolling-origin replay, paper settlement and the learning memory are
operational. Automated wager execution remains permanently out of scope.

## Current state

| Capability | State | Evidence |
|---|---|---|
| Strict domain and ontology | Complete | Pydantic models, ontology YAML, drift tests |
| Bitemporal PostgreSQL core | Complete | `wnba` schema and point-in-time leakage tests |
| Market archive | Live | Underdog poll every 15 minutes; changed states only |
| Correlated entry pricing | Wired into the console | Copula simulation, fitted leg correlation with priors where unmeasured, and entry construction over tonight's candidates |
| VPS web console | Live | `https://courtside-edge.com` |
| PostgreSQL backup | Nightly local backup | Restore-list and checksum verification; 14-day retention |
| Historical statistical data | Canonical | More than 16,000 player lines with ESPN lineage |
| Historical market data | Partially normalized | 4,571 quotes mapped to 46 games; ambiguous rows retained |
| Forecasts and recommendations | Shadow/live | Five components and immutable paper episodes; the board ranks on shrunk edge over the payout table's break-even |
| Shared production/replay scorer | Complete | `score_prop` is the only forecasting code path |
| Fitted calibration and weights | Operational | Isotonic maps, stacked weights, edge shrinkage |
| Analyst rules in the forecast path | Operational | Active and shadow firings recorded per episode |
| Walk-forward evaluation | Operational | Five pre-tip snapshots and benchmark comparisons |
| PAT-style research | Built, not yet deployed | Five DeepSeek roles with cited expiring claims over a corpus carrying recent form, line movement, the de-vigged market price, teammate availability and settled precedents; a failed analyst costs one voice rather than the run; in the advisory forecast rounds one seat is held blind as a herding control and the skeptic answers in failure modes rather than a probability |
| Learning loop | Closed | Hypotheses reviewed against post-creation evidence; active rules re-scored on live firings and auto-suspended when harmful; measured drift widens forecasts |
| Error attribution | Scored, two causes | Ordered if-chain replaced by ranked causes over the minutes-residual; `data_quality` and `modeling` added |
| Model participation in learning | Advisory, audited | DeepSeek drafts rules, proposal designs, hypothesis critiques and withdrawal rationales; every call recorded in `model_advisories` with used/fallback/rejected plus tokens, latency and retries |
| Model participation in learning | Advisory, audited | DeepSeek drafts rules, proposal designs, hypothesis critiques and withdrawal rationales; every call recorded in `model_advisories` with used/fallback/rejected plus tokens, latency and attempts |
| Adversarial skeptic | Complete | Four analysts, then a skeptic over their conclusions as citable evidence |
| Research verdict | Complete | Contested claims and caution computed in code, never by the model |
| Provider resilience | Complete | Congestion retried with capped backoff; rejected responses never are; token spend recorded |
| Independent-sample accounting | Complete | Repeats collapsed; game clustering corrected |
| Rule proposal and backtest | Operational | Weekly proposal/backtest; named CLI approval only |
| Rule withdrawal | Automatic, one-way | Harmful active rules suspend without a human; reactivation still needs a named one |
| Incident lifecycle | Complete | Cleared conditions resolve; persisting ones refresh |
| Cross-source consensus | Built, one source live | Consensus, dispersion, best price, closing line |
| End-to-end pipeline tests | Complete | Quote -> forecast -> decision -> settlement |
| Champion/challenger | Two families, shadow-ready | Hierarchical Bayes and state-space role models; promotion is human-only |
| Owner pick settlement | Operational | Confirmed slips settle against their own lines from settled episodes; unresolved players stay pending rather than being fuzzy-matched |
| Agent measurement | Proper scoring | Brier and skill against the model probability per role and per round; the consensus is credibility-weighted with a floor |
| Video intelligence | Not started | Experimental track remains isolated |

## Critical path

### 1. Normalize historical basketball data

- Preserve the legacy SQLite tables in immutable staging tables.
- Backfill canonical schedules, teams, players and complete box scores from ESPN/wehoop.
- Resolve aliases using source IDs; do not fuzzy-merge players automatically.
- Add daily schedule, roster, box-score and injury ingestion.
- Reconcile game/player totals across sources and quarantine disagreements.

Exit gate: at least two complete seasons can be reconstructed point-in-time, with documented
coverage and no unresolved identity collisions in the training set.

### 2. Build the statistical baseline

- Minutes distribution: availability, start probability, rotation stability and blowout risk.
- Per-minute opportunity models for points, rebounds, assists and three-pointers.
- Conversion models and a correlated possession/stat simulation.
- Baselines: season average, last-five, minutes x rate and market-implied probability.
- Rolling-origin backtests at evening-before, 6h, 2h, 30m and 10m snapshots.

Exit gate: production candidates beat the simple minutes x rate baseline on held-out log loss
and calibration without material subgroup degradation.

Status: the replay and the live board now share one scorer, so this comparison finally measures
the deployed model. The comparison itself has not been re-run since the change, and the prior
0.3.1 figures were withdrawn rather than carried forward -- they described a replay-only ensemble
that shared a single component with production.

The replay now also scores both challenger families on the same snapshots, so the model
comparison on the Validation page shows champion, naive baselines and challengers side by side.
That is fitted-period evidence about historical markets, and it is labelled as such: it is not
the live shadow record and the two sample counts are never added together.

### 3. Produce paper decisions and settle them

- Generate immutable `DecisionEpisode` records from live quotes.
- Store model run, feature snapshot, evidence and random seed.
- Freeze paper decisions before tip; never rewrite them after news.
- Settle outcomes, Brier score, log loss and line value automatically.
- Add calibration, drift, segment and drawdown views to the console.

Exit gate: 500+ out-of-sample paper recommendations with stable calibration and positive line
value. Profit alone is not a gate.

### 4. Add market intelligence

- Add another lawful free source if one remains stable and permitted.
- Build consensus, stale-line detection, opening/closing designation and source reliability.
- Verify payout tables from the live products; remove the unverified defaults.
- Model cross-player and same-game dependence, not a single scalar correlation.

Status: the consensus layer is built and tested -- weighted-median line, price dispersion,
best-price selection, source reliability and an explicit pre-lock closing designation. It runs
today against one source and reports itself as uncorroborated rather than presenting a lone quote
as agreement.

What remains is not code. Choosing the second source means checking that operator's terms,
confirming the collection is lawful and permitted, and dating that verification. Until an owner
makes that call, closing-line value stays a pending readiness gate.

### 5. Add PAT-style research and the learning loop

- Evidence retrieval with immutable source documents and claim expiry.
- Availability, rotation and matchup analysts plus an independent skeptic. Done, and the
  independence is now structural rather than nominal: the four analysts read identical frozen
  evidence concurrently, and the skeptic runs afterwards over that evidence plus their four
  conclusions, each supplied as a citable evidence row. Until that ordering existed the skeptic
  had nothing to point at, so its `contradicting_evidence_ids` could never name a peer's claim.
- Reduce each run to a verdict computed from the citation graph -- how many cited claims the
  skeptic contradicted, and how confidently -- rather than from a summarizing model call. Done.
  A provider-written summary cannot be replayed and would be the obvious place for a probability
  to reappear; the verdict has no field for one and no input from which to derive one.
- Reject uncited claims; agents may propose features but never write forecast probabilities.
- Postgame error attribution, analyst feedback, hypothesis registry and weekly proposals.
- Champion/challenger evaluation, shadow deployment and human-only promotion.
- The PAT organization is implemented: deterministic coordinators create trigger-specific plans;
  a data auditor blocks stale, locked, incomplete, or low-quality evidence; comparable settled
  episodes are retrieved strictly from before the forecast; five agents state independent
  advisory views before seeing peers and revise once after adversarial disclosure; and a decision
  synthesizer records support, caution, block, or insufficient evidence without changing the
  statistical model probability.
- Claims expire automatically. Normal analyst usage records viewed/useful/misleading evidence and
  feeds a ranker. Settled advisory views update domain-specific credibility, while source
  timeliness updates source reliability. Material injuries, unresolved roles, stale quotes,
  teammate changes, and high model disagreement trigger scheduled research automatically.
- DeepSeek may propose only declarative rules over the closed DSL. The proposal must cite evidence
  and state a mechanism, confounders, expiry, and withdrawal criteria. It lands as `proposed`, is
  replayed by the existing rule backtester, runs in shadow, and still needs a named human approval
  before it can affect a forecast. There is no executable-code or self-activation path.
- Feed measured miscalibration back into the forecast rather than only recording it. Done:
  calibration maps, ensemble weights and edge shrinkage are refit at settlement and applied on
  the next run, each adopted only when it beats the status quo out of fold.
- Carry a discovered pattern through proposal, backtest, approval and activation. Done as far as
  approval: repeated measured errors now generate hypotheses with stated mechanisms and candidate
  rules in the closed vocabulary, and every proposed rule is replayed against the settled record
  to produce the evidence the schema demands. Activation requires the dedicated CLI command and
  a named human; no scheduled job or research-agent path can activate a rule.
- Champion/challenger experiments are implemented. What was blocking them was never the schema:
  an experiment row requires two model versions and only one existed, and a synthetic challenger
  wrapping the same five components would have satisfied the table while teaching nothing. Two
  genuinely different families now exist -- a conjugate Gamma-Poisson model that carries its
  posterior variance into the forecast, and a local-level state-space model whose gain adapts to
  observed role change -- and both run in live shadow from the same point-in-time inputs the
  champion reads. They are scored paired on identical episodes, deduplicated to one row per
  market, gated on the effective sample after clustering, and checked for subgroup degradation.
  The evaluation reaches a verdict and stops; promotion needs a named human, and the database
  rejects a promotion without one. A gradient-boosted family remains unbuilt rather than stubbed.

### 6. Add video intelligence as an experimental sensor

- Import the referenced basketball-analysis project into an isolated dependency group.
- Establish player/ball tracking and court-calibration benchmarks on licensed footage.
- Add identity resolution, jersey OCR, event alignment and a low-confidence review queue.
- Promote only features that improve held-out forecasts; production cannot depend on video.

## VPS work

### Required before model training

- Automated off-host backup replication and a tested restore drill.
- Normalize the rescued legacy data into staging, then canonical tables.
- Monitoring for stale polls, parser rejection, database growth, backup failure and API health.
- Separate least-privilege database roles for migrations, ingestion and read-only web access.

### Required before public analyst use

- Authentication for non-public evidence, overrides and administrative actions.
- Rate limits, structured request logs, error tracking and an incident runbook.
- CI/CD that runs the full quality gate and migrations before a controlled restart.
- Retire the 46 legacy containers only after every useful collector is replaced and observed in
  parallel; preserve the archived source and SQLite snapshot.

### Required before any real-money consideration

- Rotate all credentials exposed during development and remove root-based deployment.
- Verify source terms and footage rights.
- Complete the paper-performance gates above.
- Keep execution manual. Automated book login, wagering and bankroll transfer remain out of
  scope even after model validation.
