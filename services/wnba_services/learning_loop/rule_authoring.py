"""From a research finding to a rule proposal a human can responsibly refuse.

The rule lifecycle worked but its candidates came from a hand-written catalogue, so the system
could only ever propose rules somebody had already thought of. This is the path that lets a
research finding become a candidate, with every step that makes that safe rather than alarming:

    finding -> structured causal hypothesis -> validated DSL proposal -> leakage inspection
            -> historical backtest -> shadow firing -> human review -> named approval -> active

This module owns the first four. The rest already exist and are deliberately untouched:
``run_rule_backtests`` produces the evidence, the engine fires proposed rules in shadow, and
``approve_rule`` requires a human name. Nothing here shortens that path by a single step.

WHY A LANGUAGE MODEL CAN BE TRUSTED WITH THIS
---------------------------------------------
It cannot write code. It fills in a Pydantic schema whose fields are a rule id, some thresholds,
one action from a four-member enum, and prose. There is no field into which an expression could
be placed, so "DeepSeek must never generate executable Python" is not a policy anybody has to
enforce at review time -- there is nowhere for Python to go.

It cannot widen anything. Every action in the vocabulary restricts or annotates: block, shrink
toward even, flag for review, require evidence. A proposal that wanted to raise a probability
could not be expressed.

It cannot activate anything. Proposals land as ``proposed``. The database now also refuses an
approval whose approver equals the proposer, which closes the one remaining path: an agent
naming itself.

WHAT THE LEAKAGE INSPECTION IS ACTUALLY FOR
-------------------------------------------
The closed vocabulary already excludes every field that postdates tipoff, so a proposal cannot
reference an outcome. The subtler failure is a rule whose thresholds are so narrow that it
fingerprints the handful of episodes that motivated it -- ``model_disagreement between 0.114 and
0.119`` is not a judgement about forecasts, it is a lookup table for four bad nights, and it will
backtest beautifully on the sample it was extracted from. The inspection checks that every
threshold sits inside the range the field actually takes, that no condition is degenerately
narrow, and that the rule is not so specific that only its motivating episodes could match.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb
from pydantic import ValidationError
from wnba_rules.dsl import (
    RULE_FIELDS,
    Action,
    ActionKind,
    Condition,
    Operator,
    Rule,
    RuleParseError,
)
from wnba_store.db import connect

__all__ = [
    "AGENT_AUTHOR",
    "LeakageInspection",
    "RuleAuthoringBatch",
    "author_rules_from_findings",
    "compile_proposal",
    "inspect_for_leakage",
    "observed_field_ranges",
]

# The identity every agent-authored proposal is stored under. A single stable string rather than
# the model name, because the constraint that matters -- an approver may not be the proposer --
# needs the proposer to be something a human would never type into `--approved-by`.
AGENT_AUTHOR = "deepseek_research_director"

# A numeric condition whose threshold sits outside the range the field has ever taken is either a
# typo or a rule about a world this system has not observed.
_RANGE_TOLERANCE = 0.05

# A rule matching fewer than this share of settled episodes is suspiciously specific. Not fatal on
# its own -- some genuinely rare situations deserve a rule -- but it is recorded, and combined
# with narrow thresholds it blocks.
_MINIMUM_PLAUSIBLE_COVERAGE = 0.005

# Two thresholds on the same field closer together than this fraction of the field's observed
# range describe a window rather than a judgement.
_DEGENERATE_WINDOW = 0.02


@dataclass(frozen=True)
class LeakageInspection:
    """What the inspection found, in the shape the rule's ``leakage_inspection`` column stores."""

    passed: bool
    findings: tuple[dict[str, Any], ...] = ()
    checked_fields: tuple[str, ...] = ()
    coverage: float | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "findings": list(self.findings),
            "checked_fields": list(self.checked_fields),
            "estimated_coverage": None if self.coverage is None else round(self.coverage, 5),
            "vocabulary_is_closed": True,
            "outcome_fields_unreachable": True,
            "inspected_at_proposal_time": True,
        }


@dataclass
class RuleAuthoringBatch:
    considered: int = 0
    compiled: int = 0
    rejected_vocabulary: int = 0
    rejected_leakage: int = 0
    proposed: int = 0
    hypotheses_created: int = 0
    reasons: list[str] = field(default_factory=list)


def observed_field_ranges(cur: Any, *, limit: int = 5000) -> dict[str, dict[str, float]]:
    """The range each numeric rule field has actually taken on settled episodes.

    Handed to the model before it proposes, so a threshold it invents is anchored to values that
    occur, and used afterwards to check that it was. Both directions matter: telling a model the
    range prevents most nonsense, and checking afterwards catches the rest.
    """
    cur.execute(
        """SELECT d.predicted_probability,d.confidence,d.model_disagreement,
                  d.data_quality_score,d.projected_minutes,d.line,
                  (f.features->>'minutes_std')::double precision AS minutes_std,
                  (f.features->>'start_probability')::double precision AS start_probability,
                  (f.features->>'availability_probability')::double precision
                      AS availability_probability,
                  (f.features->>'closing_lineup_probability')::double precision
                      AS closing_lineup_probability,
                  d.teammate_effect_count
           FROM wnba.decision_episodes d
           JOIN wnba.feature_snapshots f ON f.feature_snapshot_id=d.feature_snapshot_id
           JOIN wnba.episode_outcomes o ON o.episode_id=d.episode_id
           ORDER BY d.forecast_timestamp DESC LIMIT %s""",
        (max(1, limit),),
    )
    columns: dict[str, list[float]] = {}
    for raw in cur.fetchall():
        for key, value in dict(raw).items():
            if value is None or key not in RULE_FIELDS or RULE_FIELDS[key] is str:
                continue
            try:
                columns.setdefault(key, []).append(float(str(value)))
            except (TypeError, ValueError):
                continue
    ranges: dict[str, dict[str, float]] = {}
    for key, values in columns.items():
        if len(values) < 20:
            continue
        ordered = sorted(values)
        ranges[key] = {
            "min": ordered[0],
            "p05": ordered[int(0.05 * (len(ordered) - 1))],
            "median": ordered[len(ordered) // 2],
            "p95": ordered[int(0.95 * (len(ordered) - 1))],
            "max": ordered[-1],
        }
    return ranges


def compile_proposal(draft: Any, *, proposed_by: str = AGENT_AUTHOR) -> Rule:
    """Turn a model's structured draft into a ``Rule``, or refuse it.

    The DSL's own validators do the work: an unknown field, an operator that does not apply to a
    textual field, a list where a scalar belongs, an action outside the enum, or a magnitude on an
    action that takes none all raise here. This function adds nothing to that -- it exists so
    there is exactly one place where an agent's document becomes a rule object, and so the failure
    is a ``RuleParseError`` with a message a human can read rather than a stack trace.
    """
    try:
        return Rule(
            rule_id=draft.rule_id,
            title=draft.title,
            rationale=draft.rationale,
            conditions=[
                Condition(
                    field=condition.field,
                    operator=Operator(str(condition.operator)),
                    value=condition.value,
                )
                for condition in draft.conditions
            ],
            combinator=draft.combinator,
            action=Action(
                kind=ActionKind(str(draft.action.kind)),
                magnitude=draft.action.magnitude,
                note=draft.action.note,
            ),
            proposed_by=proposed_by,
            evidence_ids=[str(value) for value in draft.evidence_ids],
        )
    except ValueError as error:
        # ValidationError and RuleParseError are both ValueError; normalising to RuleParseError
        # means callers have one exception to catch and the message keeps the detail.
        if isinstance(error, RuleParseError):
            raise
        detail = error.errors() if isinstance(error, ValidationError) else str(error)
        raise RuleParseError(
            f"proposal is not expressible in the rule vocabulary: {detail}"
        ) from error


def inspect_for_leakage(
    rule: Rule,
    ranges: Mapping[str, Mapping[str, float]],
    *,
    settled_facts: Sequence[Mapping[str, object]] = (),
) -> LeakageInspection:
    """Check a proposed rule for thresholds that fingerprint their own motivating episodes.

    Every finding is reported; ``passed`` is False only when something makes the rule untestable
    or unsound. A rule that is merely rare is recorded as such and allowed through to the
    backtest, which is where rarity is properly measured -- pre-judging it here would let this
    function reject rules the evidence stage exists to evaluate.
    """
    findings: list[dict[str, Any]] = []
    checked: list[str] = []
    fatal = False

    by_field: dict[str, list[float]] = {}
    for condition in rule.conditions:
        name = condition.field
        checked.append(name)
        expected = RULE_FIELDS.get(name)
        # Belt and braces: `Condition` already rejects unknown fields, so reaching this means
        # the vocabulary and this module have diverged and the safe answer is to stop.
        if expected is None:
            findings.append(
                {
                    "code": "unknown_field",
                    "field": name,
                    "detail": "field is outside the closed vocabulary",
                }
            )
            fatal = True
            continue
        if expected is str or condition.operator in (Operator.IN, Operator.NOT_IN):
            continue
        try:
            threshold = float(str(condition.value))
        except (TypeError, ValueError):
            findings.append(
                {"code": "non_numeric_threshold", "field": name, "detail": str(condition.value)}
            )
            fatal = True
            continue
        by_field.setdefault(name, []).append(threshold)

        observed = ranges.get(name)
        if observed is None:
            findings.append(
                {
                    "code": "unobserved_field",
                    "field": name,
                    "detail": (
                        "no settled episodes carry this field, so the threshold cannot be "
                        "checked against anything and the rule cannot be backtested"
                    ),
                }
            )
            fatal = True
            continue
        low = float(observed["min"])
        high = float(observed["max"])
        span = max(1e-9, high - low)
        if threshold < low - _RANGE_TOLERANCE * span or threshold > high + _RANGE_TOLERANCE * span:
            findings.append(
                {
                    "code": "threshold_outside_observed_range",
                    "field": name,
                    "threshold": threshold,
                    "observed_min": low,
                    "observed_max": high,
                    "detail": (
                        "the threshold sits outside every value this field has ever taken, so "
                        "the rule either never fires or always does"
                    ),
                }
            )
            fatal = True

    # Two thresholds on one field describe a window. A very narrow window over a continuous
    # field is the classic overfit: it selects the episodes it was derived from and nothing else.
    for name, thresholds in by_field.items():
        if len(thresholds) < 2:
            continue
        observed = ranges.get(name)
        if observed is None:
            continue
        span = max(1e-9, float(observed["max"]) - float(observed["min"]))
        width = max(thresholds) - min(thresholds)
        if width / span < _DEGENERATE_WINDOW:
            findings.append(
                {
                    "code": "degenerate_window",
                    "field": name,
                    "window": width,
                    "field_span": span,
                    "detail": (
                        "the two thresholds on this field enclose a window narrower than 2% of "
                        "its range, which selects specific episodes rather than a situation"
                    ),
                }
            )
            fatal = True

    coverage: float | None = None
    if settled_facts:
        matched = sum(1 for facts in settled_facts if rule.matches(facts))
        coverage = matched / len(settled_facts)
        if coverage == 0.0:
            findings.append(
                {
                    "code": "never_fires",
                    "coverage": 0.0,
                    "detail": (
                        "the rule matches none of the settled record, so no backtest could "
                        "produce evidence about it"
                    ),
                }
            )
            fatal = True
        elif coverage < _MINIMUM_PLAUSIBLE_COVERAGE:
            findings.append(
                {
                    "code": "very_rare",
                    "coverage": coverage,
                    "detail": (
                        "the rule fires on well under 1% of settled decisions; the backtest will "
                        "decide whether that is a real situation or an artefact"
                    ),
                }
            )

    return LeakageInspection(
        passed=not fatal,
        findings=tuple(findings),
        checked_fields=tuple(sorted(set(checked))),
        coverage=coverage,
    )


def _finding_prompt(row: Mapping[str, Any]) -> str:
    """The measured pattern handed to the model, stated as evidence rather than as a conclusion."""
    return (
        f"Across settled decisions, the error category {row['primary_error']!r} recurred "
        f"{row['failures']} times over {row['markets']} independent markets "
        f"({', '.join(str(market) for market in row['markets_affected'])}). "
        f"Mean stated probability on those decisions was {float(str(row['mean_probability'])):.3f} "
        f"and the observed hit rate was {float(str(row['hit_rate'])):.3f}. "
        "Propose at most one rule that would have restricted these decisions. If the pattern does "
        "not support a rule expressible in the vocabulary, say so by proposing nothing."
    )


def author_rules_from_findings(
    *,
    client: Any | None = None,
    now: datetime | None = None,
    minimum_markets: int = 12,
    limit: int = 3,
) -> RuleAuthoringBatch:
    """Ask the research director to propose rules for the failure patterns that keep recurring.

    Everything proposed lands as ``proposed`` with its provenance attached and its leakage
    inspection stored. A proposal that fails the vocabulary or the inspection is *not* written --
    it is counted and its reason recorded, because a rejected proposal that sits in the table
    looking like a candidate is worse than no proposal.
    """
    from wnba_services.research_agents.deepseek import DeepSeekResearchClient

    at = now or datetime.now(UTC)
    provider = client or DeepSeekResearchClient()
    batch = RuleAuthoringBatch()

    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT a.primary_error,
                      count(*) AS failures,
                      count(DISTINCT (d.player_id,d.game_id,d.prop_type)) AS markets,
                      array_agg(DISTINCT d.prop_type) AS markets_affected,
                      avg(d.predicted_probability) AS mean_probability,
                      avg(CASE WHEN o.hit THEN 1.0 ELSE 0.0 END) AS hit_rate,
                      array_agg(DISTINCT a.episode_id) AS episodes
               FROM wnba.error_attributions a
               JOIN wnba.decision_episodes d ON d.episode_id=a.episode_id
               JOIN wnba.episode_outcomes o ON o.episode_id=d.episode_id
               WHERE a.avoidable AND NOT o.was_voided AND NOT o.was_push
               GROUP BY a.primary_error
               HAVING count(DISTINCT (d.player_id,d.game_id,d.prop_type))>=%s
               ORDER BY count(*) DESC LIMIT %s""",
            (minimum_markets, max(1, limit)),
        )
        findings = [dict(row) for row in cur.fetchall()]
        if not findings:
            return batch

        ranges = observed_field_ranges(cur)
        settled_facts = _settled_facts(cur)
        cur.execute("SELECT rule_id FROM wnba.analyst_rules")
        known = {str(row["rule_id"]) for row in cur.fetchall()}

        # The evidence the model is allowed to cite: one immutable document per finding, so a
        # proposal's `evidence_ids` point at something a reviewer can read.
        for row in findings:
            batch.considered += 1
            evidence_id, evidence_text = _record_finding_evidence(cur, row, at=at)
            try:
                draft, _, _ = provider.propose_rule(
                    finding=_finding_prompt(row),
                    vocabulary={
                        name: ("text" if kind is str else "number")
                        for name, kind in sorted(RULE_FIELDS.items())
                    },
                    actions=[kind.value for kind in ActionKind],
                    evidence={evidence_id: evidence_text},
                    observed_ranges={name: dict(values) for name, values in sorted(ranges.items())},
                )
            except Exception as error:
                batch.reasons.append(f"{row['primary_error']}: provider failed: {error}")
                continue

            try:
                rule = compile_proposal(draft)
            except RuleParseError as error:
                batch.rejected_vocabulary += 1
                batch.reasons.append(f"{draft.rule_id}: {error}")
                continue
            batch.compiled += 1

            if rule.rule_id in known:
                batch.reasons.append(f"{rule.rule_id}: a rule with this id already exists")
                continue

            inspection = inspect_for_leakage(rule, ranges, settled_facts=settled_facts)
            if not inspection.passed:
                batch.rejected_leakage += 1
                codes = ", ".join(str(finding["code"]) for finding in inspection.findings)
                batch.reasons.append(f"{rule.rule_id}: leakage inspection failed ({codes})")
                continue

            hypothesis_id = _record_hypothesis(cur, draft, row, at=at)
            batch.hypotheses_created += 1
            cur.execute(
                """INSERT INTO wnba.analyst_rules
                   (rule_id,title,rationale,definition,status,priority,proposed_by,proposed_at,
                    authored_by_agent,hypothesis_id,evidence_ids,mechanism,confounders,
                    expiry_conditions,withdrawal_criteria,leakage_inspection)
                   VALUES (%s,%s,%s,%s,'proposed',50,%s,%s,true,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    rule.rule_id,
                    rule.title,
                    rule.rationale,
                    Jsonb(
                        {
                            "conditions": [
                                {
                                    "field": condition.field,
                                    "operator": condition.operator.value,
                                    "value": condition.value,
                                }
                                for condition in rule.conditions
                            ],
                            "combinator": rule.combinator,
                            "action": {
                                "kind": rule.action.kind.value,
                                "magnitude": rule.action.magnitude,
                                "note": rule.action.note,
                            },
                        }
                    ),
                    AGENT_AUTHOR,
                    at,
                    hypothesis_id,
                    [evidence_id],
                    draft.mechanism,
                    list(draft.confounders),
                    draft.expiry_conditions,
                    draft.withdrawal_criteria,
                    Jsonb(inspection.to_payload()),
                ),
            )
            known.add(rule.rule_id)
            batch.proposed += 1
    return batch


def _record_finding_evidence(cur: Any, row: Mapping[str, Any], *, at: datetime) -> tuple[UUID, str]:
    """Store the measured pattern as citable evidence before anything is proposed from it."""
    import hashlib
    import json
    from uuid import NAMESPACE_URL, uuid5

    payload = json.dumps(
        {
            "primary_error": str(row["primary_error"]),
            "failures": int(str(row["failures"])),
            "independent_markets": int(str(row["markets"])),
            "mean_probability": float(str(row["mean_probability"])),
            "observed_hit_rate": float(str(row["hit_rate"])),
        },
        sort_keys=True,
    )
    digest = hashlib.sha256(payload.encode()).hexdigest()
    document_id = uuid5(NAMESPACE_URL, f"courtside-edge:finding:{digest}")
    evidence_id = uuid5(NAMESPACE_URL, f"courtside-edge:finding-evidence:{digest}")
    cur.execute(
        """INSERT INTO wnba.source_documents
           (document_id,source,title,content_sha256,content_excerpt,retrieved_at)
           VALUES (%s,'measured',%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
        (
            document_id,
            f"Measured failure pattern: {row['primary_error']}",
            digest,
            payload[:4000],
            at,
        ),
    )
    cur.execute(
        """INSERT INTO wnba.evidence
           (evidence_id,document_id,subject_id,excerpt,observed_at,reliability)
           VALUES (%s,%s,NULL,%s,%s,0.9) ON CONFLICT DO NOTHING""",
        (evidence_id, document_id, payload, at),
    )
    return evidence_id, payload


def _record_hypothesis(cur: Any, draft: Any, row: Mapping[str, Any], *, at: datetime) -> UUID:
    """The causal claim behind the rule, stored separately so it can be argued with alone."""
    hypothesis_id = uuid4()
    markets = row["markets_affected"]
    cur.execute(
        """INSERT INTO wnba.hypotheses
           (hypothesis_id,name,causal_statement,mechanism,target_markets,expected_direction,
            required_features,confounders,status,confidence,created_by,created_at)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'proposed',0.5,%s,%s)""",
        (
            hypothesis_id,
            draft.title[:200],
            draft.causal_statement,
            draft.mechanism,
            [str(market) for market in markets] if isinstance(markets, list) else [],
            draft.expected_direction,
            sorted({condition.field for condition in draft.conditions}),
            list(draft.confounders),
            AGENT_AUTHOR,
            at,
        ),
    )
    return hypothesis_id


def _settled_facts(cur: Any, *, limit: int = 3000) -> list[dict[str, object]]:
    """Facts from settled episodes, in the shape the DSL evaluates, for the coverage check."""
    cur.execute(
        """SELECT d.predicted_probability,d.confidence,d.model_disagreement,
                  d.data_quality_score,d.projected_minutes,d.line,d.prop_type,d.source,d.side,
                  d.teammate_effect_count,
                  f.features
           FROM wnba.decision_episodes d
           JOIN wnba.feature_snapshots f ON f.feature_snapshot_id=d.feature_snapshot_id
           JOIN wnba.episode_outcomes o ON o.episode_id=d.episode_id
           WHERE NOT o.was_voided AND NOT o.was_push
           ORDER BY d.forecast_timestamp DESC LIMIT %s""",
        (max(1, limit),),
    )
    rows: list[dict[str, object]] = []
    for raw in cur.fetchall():
        row = dict(raw)
        features = row.pop("features")
        facts: dict[str, object] = {
            key: value for key, value in row.items() if key in RULE_FIELDS and value is not None
        }
        if isinstance(features, dict):
            for key in (
                "minutes_std",
                "start_probability",
                "availability_probability",
                "closing_lineup_probability",
                "injury_designation",
                "quote_age_seconds",
            ):
                value = features.get(key)
                if value is not None and key in RULE_FIELDS:
                    facts[key] = value
        rows.append(facts)
    return rows
