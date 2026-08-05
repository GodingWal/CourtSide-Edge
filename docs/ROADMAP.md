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
| Analyst feedback loop | Built, awaiting labels | Review queue, structured labels, override scoring, expertise by domain |
| Learning loop | Operational | Calibration, drift, attribution, feedback and proposals |
| Independent-sample accounting | Complete | Repeats collapsed; game clustering corrected |
| Rule proposal and backtest | Operational | Weekly proposal/backtest; named CLI approval only |
| Incident lifecycle | Complete | Cleared conditions resolve; persisting ones refresh |
| Cross-source consensus | Built, one source live | Consensus, dispersion, best price, closing line |
| End-to-end pipeline tests | Complete | Quote -> forecast -> decision -> settlement |
| Champion/challenger | Three families, shadow-ready | Hierarchical Bayes, state-space role, gradient-boosted; promotion is human-only |
| Production validation | Measured from the live record | Every gate computes from settled episodes and flips when evidence arrives |
| Second market source | Built, gated on a dated verification | The Odds API adapter; collection requires a named terms reading |
| Possession simulation | Built as a feature generator | Joint PRA distributions and measured same-game correlation |
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
the deployed model. Every challenger is scored against all five baselines -- production ensemble,
minutes x rate, season average, last five, market prior -- in both the replay and the experiment
record, because a challenger that beats the champion while losing to minutes x rate has found a
way to be differently wrong, and one that beats everything except the devigged price has learned
the price. The comparison itself has not been re-run since the change, and the prior
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

Status: the gates now read the live settled record. They previously read `backtest_results` --
historical replay -- so six of them were hard-coded `pending` with prose explaining that live
evidence did not exist, and they would have stayed `pending` after five hundred live
recommendations arrived because nothing in the path ever looked at a settled episode.
`wnba learning validate` computes every requirement from settled decisions: independent markets
after the clustering correction, stable weekly windows (a week with four decisions is a quiet
week, not a window), calibration, closing-line value signed by the side taken, return at the
recorded payout structure alongside the flat break-even comparison, peak-to-trough drawdown and
daily exposure, observed voids and pushes and late line moves, and calibration stability across
players, teams, markets and line ranges.

Two things the harness refuses to do, because both manufacture a flattering number: it does not
assume even money for an unpriced leg (it excludes it and says how many it excluded), and it does
not score a void or a push as a loss.

### 4. Add market intelligence

- Add another lawful free source if one remains stable and permitted.
- Build consensus, stale-line detection, opening/closing designation and source reliability.
- Verify payout tables from the live products; remove the unverified defaults.
- Model cross-player and same-game dependence, not a single scalar correlation.

Status: the consensus layer is built and tested -- weighted-median line, price dispersion,
best-price selection, source reliability and an explicit pre-lock closing designation. It runs
today against one source and reports itself as uncorroborated rather than presenting a lone quote
as agreement.

The second source is now built: a keyed adapter for The Odds API, plus timestamp synchronisation,
which is the failure that makes naive multi-source data worse than single-source data. Quotes an
hour apart are not a disagreement, they are two moments, and the staler source is always the one
that has not seen the news -- so every consensus records the window its sources were observed
within.

Choosing to *enable* it remains an owner decision, and that decision is now a database row rather
than a runbook line. `wnba lines verify-source` records that a named person read the operator's
terms on a date and what they concluded; ingestion refuses to poll a source without a current
`permitted` verification, and `unclear` is a real outcome that does not grant permission. The row
is not legal advice and does not claim the collection is lawful -- it records who decided, when,
and on the basis of what, which is the part that can be audited afterwards.

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
- The gradient-boosted challenger is built. It was previously left out on the grounds that a tree
  model needs a dependency, a training set and its own leakage controls -- which was an argument
  about how to build it, not whether. It is the only discriminative family here: it learns
  P(over | features) directly with no count distribution in its path. Four leakage controls carry
  it. The training rows are written by the walk-forward replay from its own point-in-time
  `ScoringInputs`, so there is no second reconstruction of history that could see further than
  the replay did. Folds are chronological and cut on *market* boundaries, so the five snapshots of
  one event never straddle the wall. The devigged market prior is a feature, so it is also the
  baseline the model must beat -- one that cannot beat the price it was shown has learned the
  price. And the artifact serialises to plain text rather than a pickle, because loading a model
  from a database column should not be an arbitrary-code-execution path in a codebase that
  refuses to let a language model emit Python.
- Possession-level simulation, joint points/rebounds/assists distributions and player-to-player
  correlation are built as a feature generator rather than as a scorer. Measured at 60,000
  iterations, the two mechanisms separate: pace alone correlates a starter pair at +0.013 and a
  starter/bench pair at +0.014 -- weak and uniform -- while 45% blowout risk moves them to +0.100
  and **-0.044**. The sign flip is the finding. A single scalar correlation applied to every pair,
  which is what the copula entry pricing has always received, cannot represent it and gets the
  bench pairing backwards.
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

### 5b. Analyst feedback and credibility

- Structured labelling now collects what the schema always promised. `evidence_ids_useful` and
  `evidence_ids_misleading` were columns the retrieval ranking read, on a request model with no
  such fields and an INSERT that omitted the columns -- the loop's input was not sparse, it was
  unreachable, and the ranking looked like it worked. Both are now captured per cited evidence
  item, alongside the weakest assumption (free text *and* a closed kind, because a sentence is
  worth more individually and only the kind can be counted), the missing evidence, and an explicit
  would-repeat.
- A review queue lists settled decisions nobody has labelled, candidates first. "Little structured
  feedback has accumulated" is mostly not a code problem, but the two frictions code can remove
  are not knowing which decisions are worth ten minutes and having no way to name the specific
  evidence that misled you.
- Overrides are scored after settlement, in both directions. An override is the most informative
  label available -- a falsifiable claim that the model is wrong, made before the outcome -- and
  `analyst_decision` has existed since migration 001 with nothing ever reading it. An override on
  a decision the system had already declined is recorded as neutral rather than scored: both
  parties said no in effect, and since most of the board is declined, counting those is the
  easiest way to manufacture an impressive override record.
- Analyst expertise is computed per domain from pre-outcome labels and override outcomes, with the
  same partial pooling and sample-size gate agent credibility uses. Labels written after the
  result are stored and feed error attribution but are excluded from expertise, because knowing
  the answer makes the weak assumption obvious. The score is displayed and gates nothing: there is
  one human here, and a score that began discounting their overrides would be a bug with a
  statistics paper attached.

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
