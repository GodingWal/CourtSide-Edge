# Delivery roadmap

Updated: 2026-08-04

The platform is in **Phase 3: private shadow analysis**. Live and rescued historical markets,
five-component forecasts, rolling-origin replay, paper settlement and the learning memory are
operational. Automated wager execution remains permanently out of scope.

## Current state

| Capability | State | Evidence |
|---|---|---|
| Strict domain and ontology | Complete | Pydantic models, ontology YAML, drift tests |
| Bitemporal PostgreSQL core | Complete | `wnba` schema and point-in-time leakage tests |
| Market archive | Live | Underdog poll every 15 minutes; changed states only |
| Correlated entry pricing | Complete as a library | Copula simulation and payout tests |
| VPS web console | Live | `https://courtside-edge.com` |
| PostgreSQL backup | Nightly local backup | Restore-list and checksum verification; 14-day retention |
| Historical statistical data | Canonical | More than 16,000 player lines with ESPN lineage |
| Historical market data | Partially normalized | 4,571 quotes mapped to 46 games; ambiguous rows retained |
| Forecasts and recommendations | Shadow/live | Five components and immutable paper episodes |
| Shared production/replay scorer | Complete | `score_prop` is the only forecasting code path |
| Fitted calibration and weights | Operational | Isotonic maps, stacked weights, edge shrinkage |
| Analyst rules in the forecast path | Operational | Active and shadow firings recorded per episode |
| Walk-forward evaluation | Operational | Five pre-tip snapshots and benchmark comparisons |
| PAT-style research | Organization built, awaiting API key | Coordinator, blocking data auditor, two rounds, synthesizer, credibility |
| Agent-authored rules | Built | Closed-vocabulary proposals with leakage inspection; no self-approval |
| Learning loop | Operational | Calibration, drift, attribution, feedback and proposals |
| Independent-sample accounting | Complete | Repeats collapsed; game clustering corrected |
| Rule proposal and backtest | Operational | Weekly proposal/backtest; named CLI approval only |
| Incident lifecycle | Complete | Cleared conditions resolve; persisting ones refresh |
| Cross-source consensus | Built, one source live | Consensus, dispersion, best price, closing line |
| End-to-end pipeline tests | Complete | Quote -> forecast -> decision -> settlement |
| Champion/challenger | Two families, shadow-ready | Hierarchical Bayes and state-space role models; promotion is human-only |
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
- Availability, rotation and matchup analysts plus an independent skeptic.
- Reject uncited claims; agents may propose features but never write forecast probabilities.
- Research is now an organization rather than a panel. A coordinator plans investigations from
  measured changes -- an injury row written after the forecast, a role row that postdates it, a
  claim past its refresh time, a candidate recommendation, component spread above the level at
  which pooling has historically been an artefact -- and sends only the roles that trigger needs.
  A deterministic data auditor runs first and can block: an `out` designation beside a full
  rotation workload, a near-certain start beside bench minutes, an archival quote. Blocking is a
  correct outcome, not an error, and it costs nothing because no model has been called yet.
- Round one is independent and the schema says so: `saw_peers` is false for every first-round
  analysis and the database refuses a first-round row claiming otherwise. Round two is
  adversarial -- each analyst reads its peers, concedes or restates its disagreement, and the
  revision is stored beside what it revised rather than replacing it.
- Claims carry a refresh time derived from how fast their domain actually goes stale (availability
  in 45 minutes, matchup in six hours), and a new claim about the same predicate supersedes the
  old one instead of sitting beside it.
- Comparable historical decisions are retrieved by situation rather than by player, over
  point-in-time fields only, and reported with their own sample size. A precedent hit rate is a
  base rate of a small self-selected reference class and is never combined with the model's
  probability.
- The decision synthesizer combines model output, research, market state and policy into one
  posture stored beside the episode. It has no column for a probability of its own and computes
  none. Agent credibility is scored per role *and domain*, pooled hard toward 0.5, and weights the
  agreement statistic only -- it never gates a claim.
- Evidence ranking now reads the analyst usefulness labels that `analyst_feedback` has carried
  since migration 018 and that nothing had ever read, and source reliability is scored per source
  and domain from those labels plus how often a source's evidence ends up contradicted. Both are
  *consumed*, not merely stored: the bundle handed to the analysts is ordered by learned
  usefulness, and an evidence row's reliability is the weaker of the snapshot's completeness and
  the source's measured track record.
- Ranking is per evidence *kind*, not per evidence id. Ids are content-hashed per prop and are
  seen exactly once, so a per-id ranking could never accumulate a sample -- it was an elaborate
  way of storing the prior. Kinds recur on every prop of every slate. Ordering is the only
  consequence: no kind is ever withheld because analysts disliked it, since a feedback loop that
  could hide evidence would quietly narrow what the next investigation is allowed to see.
- Investigations run automatically as well as queue automatically. `wnba-research.timer` plans and
  then drains the top of the queue under a hard cap, so a trigger storm cannot run away and the
  overflow either waits for the next pass or expires with the market.
- Postgame error attribution, analyst feedback, hypothesis registry and weekly proposals.
- Champion/challenger evaluation, shadow deployment and human-only promotion.
- Feed measured miscalibration back into the forecast rather than only recording it. Done:
  calibration maps, ensemble weights and edge shrinkage are refit at settlement and applied on
  the next run, each adopted only when it beats the status quo out of fold.
- Carry a discovered pattern through proposal, backtest, approval and activation. Done as far as
  approval: repeated measured errors now generate hypotheses with stated mechanisms and candidate
  rules in the closed vocabulary, and every proposed rule is replayed against the settled record
  to produce the evidence the schema demands. Activation requires the dedicated CLI command and
  a named human; no scheduled job or research-agent path can activate a rule.
- Candidate rules no longer come only from the curated catalogue. The research director is handed
  a measured failure pattern, the closed vocabulary, the four available actions and the observed
  range of every field, and returns a structured document -- never code, because the schema has no
  field an expression could occupy and rejects unknown keys. The draft is compiled through the
  DSL's own validators, inspected for leakage, and stored as `proposed` with its evidence ids,
  mechanism, confounders, expiry conditions and withdrawal criteria attached. The database
  enforces that provenance for agent-authored rules and refuses any approval whose approver is
  the proposer, so no agent can activate its own rule even by naming itself.
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
