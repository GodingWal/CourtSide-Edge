"""Scoring the human the same way everything else here is scored.

The platform measures its models, its rules, its research agents and its evidence. It has never
measured the one participant whose judgement actually governs promotion, activation and every
other irreversible step: the analyst. That asymmetry is backwards. The person with the authority
to overrule the system is the person whose track record matters most, and "I have a good feel for
minutes" is exactly the kind of claim this codebase refuses to accept from a model.

Two things get scored:

**Overrides.** An override is the most informative label available -- a claim that the model is
wrong about a specific decision, made *before* the outcome is known and therefore falsifiable. It
is scored against what happened and against the recommendation it replaced, in both directions.
An analyst who remembers only the overrides that worked is the default state of every analyst,
which is why the table records the ones that cost as well.

**Labels.** Whether the analyst's stated concern matches the error the settlement actually
attributed. Saying "the minutes projection is the weak link here" on a decision that then missed
because of minutes is a hit; saying it on one that missed on shooting variance is not.

THE ORDER-OF-INFORMATION RULE
-----------------------------
Only labels written *before* the outcome was known count toward expertise. A label written
afterwards is contaminated by it -- and not through any dishonesty, simply because knowing the
answer makes the relevant assumption obvious. Post-outcome labels are still stored and still feed
error attribution, where hindsight is the point rather than the problem.

WHAT THIS SCORE IS FOR
----------------------
Reading. It gates nothing, weights nothing and reaches no forecast. There is one human here, and
a score that started discounting their overrides would be a bug with a statistics paper attached.
Its honest use is the analyst asking themselves whether their instinct about rotations has
actually earned the confidence they give it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb
from wnba_store.db import connect

from wnba_services.research_agents.credibility import POOLING_STRENGTH, pooled_score

__all__ = [
    "FEEDBACK_DOMAINS",
    "MINIMUM_DOMAIN_LABELS",
    "WEAKEST_ASSUMPTION_DOMAINS",
    "ExpertiseBatch",
    "override_verdict",
    "score_analyst_expertise",
]

# Which domain a label is about. Derived from the analyst's stated weakest assumption where they
# gave one, and from the rejection reason otherwise -- an analyst who rejects a recommendation
# "on minutes" has told you the domain even if they skipped the taxonomy.
WEAKEST_ASSUMPTION_DOMAINS: dict[str, str] = {
    "minutes": "rotation",
    "role": "rotation",
    "usage_rate": "production",
    "efficiency": "production",
    "matchup": "matchup",
    "market_price": "market",
    "availability": "availability",
    "data_quality": "data_quality",
    "sample_size": "data_quality",
    "correlation": "portfolio",
    "other": "general",
}

FEEDBACK_DOMAINS: dict[str, str] = {
    "accepted": "general",
    "rejected_bad_data": "data_quality",
    "rejected_minutes": "rotation",
    "rejected_matchup": "matchup",
    "rejected_price": "market",
    "rejected_uncertainty": "general",
    "missing_evidence": "data_quality",
    "explanation_unclear": "general",
}

# The error category the settlement attributes, mapped into the same domain space so a label and
# an attribution can be compared at all.
_ERROR_DOMAINS: dict[str, str] = {
    "minutes_projection": "rotation",
    "rate_or_efficiency": "production",
    "market_movement": "market",
    "random_variance": "general",
}

# Below this many pre-outcome labels in a domain, the score is reported as provisional. Deliberately
# the same shape of gate the agent credibility uses: a human's twelve opinions are not more
# evidence than a model's twelve.
MINIMUM_DOMAIN_LABELS = 15

_OVERRIDES = {"override_higher", "override_lower"}


@dataclass(frozen=True)
class ExpertiseBatch:
    overrides_evaluated: int
    domains_scored: int
    labels_scored: int
    analysts: int


def domain_for(feedback_type: str, weakest_assumption_kind: str | None) -> str:
    """The domain a label belongs to. The stated assumption wins when there is one."""
    if weakest_assumption_kind:
        return WEAKEST_ASSUMPTION_DOMAINS.get(weakest_assumption_kind, "general")
    return FEEDBACK_DOMAINS.get(feedback_type, "general")


def override_verdict(
    *,
    analyst_decision: str,
    system_recommendation: str,
    hit: bool,
) -> tuple[str, bool, bool]:
    """``(verdict, system_was_right, analyst_was_right)`` for one settled decision.

    "Right" means: would acting on this view have been correct. The system is right when it
    recommended something that landed or declined something that missed. An analyst who agreed
    is right exactly when the system was; one who overrode is right exactly when it was not.

    An override on a decision the system had already declined is recorded as neutral rather than
    scored. Both parties said no in effect, so the outcome does not separate them, and counting it
    either way would be measuring agreement with a non-event.
    """
    system_acted = system_recommendation == "candidate"
    system_was_right = hit if system_acted else not hit

    if analyst_decision not in _OVERRIDES:
        return (
            "agreed_and_right" if system_was_right else "agreed_and_wrong",
            system_was_right,
            system_was_right,
        )
    if not system_acted:
        return "override_neutral", system_was_right, system_was_right
    analyst_was_right = not system_was_right
    return (
        "override_helped" if analyst_was_right else "override_hurt",
        system_was_right,
        analyst_was_right,
    )


def _evaluate_overrides(cur: Any, at: datetime) -> int:
    """Score every settled episode the analyst expressed a view on."""
    cur.execute(
        """SELECT d.episode_id,d.analyst_decision,d.analyst_override_reason,
                  d.system_recommendation,d.predicted_probability,d.breakeven_probability,
                  o.hit,
                  coalesce(
                      (SELECT fb.analyst FROM wnba.analyst_feedback fb
                        WHERE fb.episode_id=d.episode_id ORDER BY fb.submitted_at LIMIT 1),
                      'owner') AS analyst
           FROM wnba.decision_episodes d
           JOIN wnba.episode_outcomes o ON o.episode_id=d.episode_id
           WHERE d.analyst_decision IS NOT NULL
             AND NOT o.was_voided AND NOT o.was_push
             AND NOT EXISTS (
                 SELECT 1 FROM wnba.override_evaluations e WHERE e.episode_id=d.episode_id)"""
    )
    written = 0
    for raw in cur.fetchall():
        row = dict(raw)
        hit = bool(row["hit"])
        verdict, system_right, analyst_right = override_verdict(
            analyst_decision=str(row["analyst_decision"]),
            system_recommendation=str(row["system_recommendation"]),
            hit=hit,
        )
        cur.execute(
            """INSERT INTO wnba.override_evaluations
               (evaluation_id,episode_id,evaluated_at,analyst,analyst_decision,override_reason,
                system_recommendation,system_probability,breakeven,hit,system_was_right,
                analyst_was_right,verdict,detail)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (episode_id) DO NOTHING""",
            (
                uuid4(),
                row["episode_id"],
                at,
                str(row["analyst"]),
                str(row["analyst_decision"]),
                row["analyst_override_reason"],
                str(row["system_recommendation"]),
                float(str(row["predicted_probability"])),
                (
                    None
                    if row["breakeven_probability"] is None
                    else float(str(row["breakeven_probability"]))
                ),
                hit,
                system_right,
                analyst_right,
                verdict,
                Jsonb({"scored_by": "analyst_expertise", "gates_nothing": True}),
            ),
        )
        written += cur.rowcount
    return written


def _score_domains(cur: Any, at: datetime) -> tuple[int, int, int]:
    """Expertise per (analyst, domain) from pre-outcome labels and override outcomes."""
    cur.execute(
        """SELECT fb.analyst,fb.feedback_type,fb.weakest_assumption_kind,fb.domain,
                  fb.labelled_after_outcome,
                  a.primary_error,
                  o.hit
           FROM wnba.analyst_feedback fb
           JOIN wnba.decision_episodes d ON d.episode_id=fb.episode_id
           JOIN wnba.episode_outcomes o ON o.episode_id=d.episode_id
           LEFT JOIN wnba.error_attributions a ON a.episode_id=fb.episode_id
           WHERE NOT o.was_voided AND NOT o.was_push"""
    )
    labels: dict[tuple[str, str], list[bool]] = {}
    label_rows = 0
    for raw in cur.fetchall():
        row = dict(raw)
        # Hindsight is not foresight. A label written after the result is still useful for error
        # attribution and is deliberately excluded here.
        if bool(row["labelled_after_outcome"]):
            continue
        analyst = str(row["analyst"])
        domain = str(
            row["domain"]
            or domain_for(
                str(row["feedback_type"]),
                None
                if row["weakest_assumption_kind"] is None
                else str(row["weakest_assumption_kind"]),
            )
        )
        attributed = row["primary_error"]
        if attributed is None:
            continue
        # Did the analyst name the domain the settlement went on to blame?
        agreed = _ERROR_DOMAINS.get(str(attributed), "general") == domain
        labels.setdefault((analyst, domain), []).append(agreed)
        label_rows += 1

    cur.execute(
        """SELECT analyst,verdict,count(*) AS n FROM wnba.override_evaluations
           GROUP BY analyst,verdict"""
    )
    override_counts: dict[str, dict[str, int]] = {}
    for raw in cur.fetchall():
        row = dict(raw)
        override_counts.setdefault(str(row["analyst"]), {})[str(row["verdict"])] = int(
            str(row["n"])
        )

    analysts = {analyst for analyst, _ in labels} | set(override_counts)
    written = 0
    for analyst in sorted(analysts):
        counts = override_counts.get(analyst, {})
        helped = counts.get("override_helped", 0)
        hurt = counts.get("override_hurt", 0)
        overrides = helped + hurt
        override_rate = helped / overrides if overrides else None

        domains = {domain for scored_analyst, domain in labels if scored_analyst == analyst}
        # An analyst with overrides but no labelled domain still gets a general-domain row, so
        # the override record is never invisible for want of a taxonomy entry.
        for domain in sorted(domains or {"general"}):
            agreements = labels.get((analyst, domain), [])
            agreement = (
                math.fsum(float(value) for value in agreements) / len(agreements)
                if agreements
                else None
            )
            # Label agreement carries most of the score; override success is a smaller term
            # because overrides are rare and their outcome is noisier than a single label.
            components: list[tuple[float, float]] = []
            if agreement is not None:
                components.append((agreement, float(len(agreements))))
            if override_rate is not None and domain in {"general", *domains}:
                components.append((override_rate, float(overrides) * 0.5))
            if components:
                weight = math.fsum(count for _, count in components)
                raw_score = (
                    math.fsum(value * count for value, count in components) / weight
                    if weight
                    else 0.5
                )
            else:
                raw_score, weight = 0.5, 0.0
            expertise = pooled_score(raw_score * weight, weight)
            cur.execute(
                """INSERT INTO wnba.analyst_expertise
                   (analyst,domain,calculated_at,labels,overrides,overrides_helped,
                    override_hit_rate,label_agreement,expertise,sample_size,is_provisional,
                    method,detail)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                (
                    analyst,
                    domain,
                    at,
                    len(agreements),
                    overrides,
                    helped,
                    override_rate,
                    agreement,
                    expertise,
                    len(agreements),
                    len(agreements) < MINIMUM_DOMAIN_LABELS,
                    "label agreement + override outcome, pooled",
                    Jsonb(
                        {
                            "pooling_strength": POOLING_STRENGTH,
                            "minimum_labels": MINIMUM_DOMAIN_LABELS,
                            "pre_outcome_labels_only": True,
                            "gates_nothing": True,
                        }
                    ),
                ),
            )
            written += 1
    return written, label_rows, len(analysts)


def score_analyst_expertise(*, now: datetime | None = None) -> ExpertiseBatch:
    """Evaluate overrides and refresh per-domain analyst expertise. Changes no forecast."""
    at = now or datetime.now(UTC)
    with connect() as conn, conn.cursor() as cur:
        overrides = _evaluate_overrides(cur, at)
        domains, labels, analysts = _score_domains(cur, at)
    return ExpertiseBatch(
        overrides_evaluated=overrides,
        domains_scored=domains,
        labels_scored=labels,
        analysts=analysts,
    )


def unlabelled_settled_episodes(cur: Any, *, limit: int = 25) -> list[dict[str, Any]]:
    """Settled decisions nobody has labelled yet, newest first.

    The review queue. "Little structured feedback has accumulated" is not primarily a code
    problem, but a queue that says *which* twenty decisions are worth ten minutes is the
    difference between labelling regularly and labelling when you remember to.
    """
    cur.execute(
        """SELECT d.episode_id,d.prop_type,d.side,d.line,d.predicted_probability,
                  d.system_recommendation,d.forecast_timestamp,d.decision_reason,
                  o.hit,o.actual_stat,o.settled_at,
                  p.full_name,
                  a.primary_error
           FROM wnba.decision_episodes d
           JOIN wnba.episode_outcomes o ON o.episode_id=d.episode_id
           JOIN wnba.players p ON p.player_id=d.player_id
           LEFT JOIN wnba.error_attributions a ON a.episode_id=d.episode_id
           WHERE NOT o.was_voided AND NOT o.was_push
             AND NOT EXISTS (
                 SELECT 1 FROM wnba.analyst_feedback fb WHERE fb.episode_id=d.episode_id)
           ORDER BY (d.system_recommendation='candidate') DESC,o.settled_at DESC
           LIMIT %s""",
        (max(1, limit),),
    )
    return [dict(row) for row in cur.fetchall()]


def episode_evidence(cur: Any, episode_id: UUID) -> list[dict[str, Any]]:
    """The evidence cited on this episode's research, so a label can name specific items.

    Without this the console can only collect "the evidence was unhelpful" in the abstract, which
    is precisely the label that cannot improve a retrieval order.
    """
    cur.execute(
        """SELECT DISTINCT e.evidence_id,e.kind,left(e.excerpt,400) AS excerpt,e.reliability
           FROM wnba.decision_episodes d
           JOIN wnba.stat_forecasts f ON f.quote_id=d.quote_id AND f.model_run_id=d.model_run_id
           JOIN wnba.research_runs r ON r.projection_id=f.projection_id
           JOIN wnba.agent_analyses a ON a.research_run_id=r.research_run_id
           JOIN wnba.evidence e ON e.evidence_id = ANY(a.evidence_ids)
           WHERE d.episode_id=%s
           ORDER BY e.kind""",
        (episode_id,),
    )
    return [dict(row) for row in cur.fetchall()]
