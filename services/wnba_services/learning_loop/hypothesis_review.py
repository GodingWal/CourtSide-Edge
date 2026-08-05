"""Asking, weekly, whether the causal claims the loop made are still true.

A hypothesis was the one thing in this system that could be stated and never checked. It was
written from a run of measured failures, given a confidence of 0.5, and left at status
``proposed`` permanently -- so "causal memory" accumulated claims and never retired one. The
supporting-episode links were written at creation and never added to, which meant every
hypothesis was judged forever on the evidence that suggested it. That is the shape of confirmation
bias with a schema.

**The test.** A hypothesis born from error category *C* claims that *C* explains failures in its
target markets. So the honest question is not "did more C-errors happen" -- more of everything
happens as the season runs -- but: *among avoidable errors in these markets, since this claim was
made, what share is still C?* A mechanism that has stopped explaining failures is refuted whether
or not the failures continue.

Evidence is strictly post-creation. An episode that motivated the hypothesis cannot also confirm
it, and the ``attributed_at > created_at`` filter is what enforces that.

**Sample.** Counted in independent markets, not rows, through the same clustering correction the
rest of the loop uses. A hypothesis "confirmed" on forty rows from one Tuesday is confirmed on
about four markets' worth of information.

**Where the model sits.** DeepSeek argues about what the tally means and proposes confounders the
template author did not think of. It does not supply the tally, the share, the confidence or the
status -- those are computed here from settled outcomes, and the model sees them only as context.
Its answer lands in ``review`` as commentary alongside the arithmetic that actually decided the
verdict, so a reader can see both and tell them apart.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field
from wnba_store.db import connect

from wnba_services.learning_loop.independence import summarise_sample
from wnba_services.research_agents.advisor import Advisor, advisory_summary

__all__ = [
    "MINIMUM_REVIEW_MARKETS",
    "REFUTED_SHARE",
    "SUPPORTED_SHARE",
    "HypothesisReviewBatch",
    "HypothesisVerdict",
    "review_hypotheses",
    "verdict_for",
]

# Independent markets of post-creation evidence before a verdict is worth reaching. Below this,
# `inconclusive` is the honest answer and the hypothesis stays open.
MINIMUM_REVIEW_MARKETS = 20

# Share of avoidable errors still attributable to the claimed category.
SUPPORTED_SHARE = 0.50
REFUTED_SHARE = 0.20


@dataclass(frozen=True)
class HypothesisVerdict:
    hypothesis_id: UUID
    name: str
    status: str
    supporting: int
    contradicting: int
    independent_markets: float
    confidence: float
    model_reviewed: bool


@dataclass(frozen=True)
class HypothesisReviewBatch:
    reviewed: int
    supported: int
    refuted: int
    inconclusive: int
    episodes_linked: int
    advisories: dict[str, int]


class HypothesisCritique(BaseModel):
    """DeepSeek's argument about a tally it did not produce."""

    model_config = ConfigDict(strict=True, extra="forbid")

    agrees_with_verdict: bool
    assessment: Annotated[str, Field(min_length=20, max_length=1500)]
    # Confounders the original statement missed. These are the actionable output: they become the
    # thing the next revision of the hypothesis has to control for.
    additional_confounders: Annotated[
        list[Annotated[str, Field(min_length=3, max_length=200)]], Field(max_length=6)
    ]
    # A restatement the model thinks the evidence would actually support. Prose only; adopting it
    # is a human edit, not something this job does.
    suggested_restatement: Annotated[str, Field(max_length=1000)] = ""


def verdict_for(
    *, supporting: int, contradicting: int, independent_markets: float
) -> tuple[str, float]:
    """Status and evidence-derived confidence. Pure, so the thresholds are testable directly."""
    total = supporting + contradicting
    if total == 0 or independent_markets < MINIMUM_REVIEW_MARKETS:
        return "inconclusive", 0.5
    share = supporting / total
    # Shrink toward 0.5 by how little independent evidence there is. Twenty markets is the point
    # at which the share is taken at close to face value; well below that it barely moves.
    weight = min(1.0, independent_markets / (independent_markets + MINIMUM_REVIEW_MARKETS))
    confidence = 0.5 + (share - 0.5) * weight
    if share >= SUPPORTED_SHARE:
        return "supported", round(min(0.95, max(0.05, confidence)), 4)
    if share <= REFUTED_SHARE:
        return "refuted", round(min(0.95, max(0.05, confidence)), 4)
    return "inconclusive", round(min(0.95, max(0.05, confidence)), 4)


def review_hypotheses(
    *, now: datetime | None = None, advisor: Advisor | None = None
) -> HypothesisReviewBatch:
    """Re-judge every open hypothesis against evidence that postdates it.

    Nothing here touches a forecast. A refuted hypothesis stops motivating new proposals; it does
    not retract a rule that was already approved on its back -- that is the rule reviewer's job,
    and it works from the rule's own measured firings rather than from the story behind it.
    """
    at = now or datetime.now(UTC)
    reviewed = supported = refuted = inconclusive = linked = 0

    with connect() as conn, conn.cursor() as cur:
        assistant = advisor or Advisor(cur)
        cur.execute(
            """SELECT hypothesis_id,name,causal_statement,mechanism,confounders,target_markets,
                      error_category,status,created_at
               FROM wnba.hypotheses
               WHERE status IN ('proposed','supported','inconclusive')
               ORDER BY created_at"""
        )
        open_hypotheses = cur.fetchall()

        for hypothesis in open_hypotheses:
            category = hypothesis["error_category"]
            markets = hypothesis["target_markets"]
            if not category or not isinstance(markets, list) or not markets:
                # A hypothesis with no claimed category cannot be tested by this method. Left
                # open rather than guessed at.
                continue
            reviewed += 1
            hypothesis_id = UUID(str(hypothesis["hypothesis_id"]))

            cur.execute(
                """SELECT d.episode_id,d.player_id,d.game_id,d.prop_type,
                          a.primary_error,a.secondary_error
                   FROM wnba.error_attributions a
                   JOIN wnba.decision_episodes d ON d.episode_id=a.episode_id
                   JOIN wnba.episode_outcomes o ON o.episode_id=d.episode_id
                   WHERE a.avoidable
                     AND NOT o.was_voided AND NOT o.was_push
                     AND d.prop_type = ANY(%s)
                     AND a.attributed_at > %s""",
                (list(markets), hypothesis["created_at"]),
            )
            evidence = cur.fetchall()
            if not evidence:
                _persist(
                    cur,
                    hypothesis_id=hypothesis_id,
                    status="inconclusive",
                    confidence=0.5,
                    supporting=0,
                    contradicting=0,
                    shape_payload={"markets": 0, "effective": 0.0},
                    critique=None,
                    at=at,
                )
                inconclusive += 1
                continue

            # A secondary cause counts as support: the ordered classifier used to hide an
            # efficiency miss behind a large minutes miss, and a hypothesis about efficiency
            # should not be refuted by the branch order of the thing measuring it.
            supporting_rows = [
                row
                for row in evidence
                if row["primary_error"] == category or row["secondary_error"] == category
            ]
            supporting_ids = {str(row["episode_id"]) for row in supporting_rows}
            contradicting_rows = [
                row for row in evidence if str(row["episode_id"]) not in supporting_ids
            ]
            shape = summarise_sample(supporting_rows + contradicting_rows)

            status, confidence = verdict_for(
                supporting=len(supporting_rows),
                contradicting=len(contradicting_rows),
                independent_markets=shape.effective,
            )

            for row, relationship in (
                *((row, "supporting") for row in supporting_rows),
                *((row, "contradicting") for row in contradicting_rows),
            ):
                cur.execute(
                    """INSERT INTO wnba.hypothesis_episodes (hypothesis_id,episode_id,relationship)
                       VALUES (%s,%s,%s) ON CONFLICT DO NOTHING""",
                    (hypothesis_id, row["episode_id"], relationship),
                )
                linked += cur.rowcount

            critique = _critique(
                assistant,
                hypothesis=hypothesis,
                status=status,
                supporting=len(supporting_rows),
                contradicting=len(contradicting_rows),
                independent_markets=shape.effective,
                at=at,
            )
            _persist(
                cur,
                hypothesis_id=hypothesis_id,
                status=status,
                confidence=confidence,
                supporting=len(supporting_rows),
                contradicting=len(contradicting_rows),
                shape_payload=shape.to_payload(),
                critique=critique,
                at=at,
            )
            supported += status == "supported"
            refuted += status == "refuted"
            inconclusive += status == "inconclusive"

        summary = advisory_summary(assistant.outcomes)

    return HypothesisReviewBatch(
        reviewed=reviewed,
        supported=supported,
        refuted=refuted,
        inconclusive=inconclusive,
        episodes_linked=linked,
        advisories=summary,
    )


def _critique(
    advisor: Advisor,
    *,
    hypothesis: Any,
    status: str,
    supporting: int,
    contradicting: int,
    independent_markets: float,
    at: datetime,
) -> HypothesisCritique | None:
    """Ask the model to argue with an arithmetic verdict it cannot change."""
    return advisor.advise(
        task="hypothesis_review",
        subject=str(hypothesis["name"]),
        system=(
            "You are the skeptic on a WNBA player-prop research desk, reviewing a causal "
            "hypothesis against settled outcomes. Return JSON only. The tally and the verdict "
            "were computed from the settled record and are not yours to change -- your job is to "
            "say whether the mechanism still explains the evidence, and to name confounders that "
            "would produce this same tally without the mechanism being true. Never output a "
            "probability for any prop, a recommendation, or a stake."
        ),
        question={
            "hypothesis": str(hypothesis["name"]),
            "causal_statement": str(hypothesis["causal_statement"]),
            "mechanism": str(hypothesis["mechanism"]),
            "stated_confounders": list(hypothesis["confounders"] or []),
            "claimed_error_category": str(hypothesis["error_category"]),
            "target_markets": list(hypothesis["target_markets"] or []),
            "evidence_since_the_claim_was_made": {
                "avoidable_errors_matching_the_claimed_category": supporting,
                "avoidable_errors_with_a_different_cause": contradicting,
                "independent_markets_after_game_clustering": round(independent_markets, 2),
                "computed_verdict": status,
                "verdict_thresholds": {
                    "supported_at_or_above_share": SUPPORTED_SHARE,
                    "refuted_at_or_below_share": REFUTED_SHARE,
                    "minimum_independent_markets": MINIMUM_REVIEW_MARKETS,
                },
            },
        },
        schema=HypothesisCritique,
        now=at,
    )


def _persist(
    cur: Any,
    *,
    hypothesis_id: UUID,
    status: str,
    confidence: float,
    supporting: int,
    contradicting: int,
    shape_payload: dict[str, Any],
    critique: HypothesisCritique | None,
    at: datetime,
) -> None:
    review: dict[str, Any] = {
        "method": "share-of-avoidable-errors-v1",
        "supporting": supporting,
        "contradicting": contradicting,
        "sample_shape": shape_payload,
        "thresholds": {
            "supported": SUPPORTED_SHARE,
            "refuted": REFUTED_SHARE,
            "minimum_independent_markets": MINIMUM_REVIEW_MARKETS,
        },
        "model_reviewed": critique is not None,
    }
    if critique is not None:
        review["model_critique"] = {
            "agrees_with_verdict": critique.agrees_with_verdict,
            "assessment": critique.assessment,
            "additional_confounders": critique.additional_confounders,
            "suggested_restatement": critique.suggested_restatement,
        }
    cur.execute(
        """UPDATE wnba.hypotheses
           SET status=%s,confidence=%s,supporting_count=%s,contradicting_count=%s,
               evaluated_at=%s,review=%s
           WHERE hypothesis_id=%s""",
        (status, confidence, supporting, contradicting, at, Jsonb(review), hypothesis_id),
    )
