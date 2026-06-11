"""Grounded narrative generation (P0-3 / P0-2).

Two supported modes, both of which keep the LLM away from numbers and
unpayloaded facts:

1. Template mode: prose templates with {placeholders} interpolated in code
   from the frozen Pick + payload — the LLM never touches a digit.
2. LLM mode: the structured JSON payload is the ENTIRE user message, the
   system prompt forbids facts outside it, and the output still passes
   through the numeric scan and claim verifier before publication.
"""
import json

from shared.picks.models import NarrativePayload, Pick, Recommendation

GROUNDED_SYSTEM_PROMPT = (
    "You write one short betting rationale for a WNBA player prop pick. "
    "The user message is a JSON payload; it is your ONLY source of truth. "
    "You may reference ONLY facts present in that payload. "
    "Never state a number that is not in the payload — numeric values are "
    "injected by the system, not written by you. "
    "Never describe a player's career stage (debut, rookie, return from "
    "injury) or any injury unless the payload field for it explicitly "
    "supports the claim. If a fact is not in the payload, it does not exist. "
    "Two to three sentences, no headers."
)

DEFAULT_TEMPLATE = (
    "{recommendation} {player} {direction} {line} {stat} ({book}): the model "
    "projects {projection} (edge {edge:+.1f}) with a {hit_pct:.0f}% hit "
    "probability against a {breakeven_pct:.1f}% breakeven. L5 average "
    "{l5_avg} vs {opponent}."
)


def template_context(pick: Pick, payload: NarrativePayload) -> dict:
    """The only values a narrative template can interpolate — all sourced
    from the frozen pick/payload, never recomputed."""
    return {
        "recommendation": pick.recommendation.value,
        "player": payload.player.name,
        "direction": "over" if pick.recommendation == Recommendation.BUY else "under",
        "stat": payload.stat.category,
        "line": pick.line,
        "book": pick.book,
        "projection": pick.projection,
        "edge": pick.edge,
        "hit_pct": pick.hit_probability * 100,
        "breakeven_pct": pick.breakeven_probability * 100,
        "l5_avg": payload.form.l5_avg,
        "l10_avg": payload.form.l10_avg,
        "opponent": payload.matchup.opponent,
        "team": payload.player.team,
        "win_prob_pct": payload.game.win_probability * 100,
    }


def render_template(pick: Pick, payload: NarrativePayload,
                    template: str = DEFAULT_TEMPLATE) -> str:
    return template.format(**template_context(pick, payload))


def generate_grounded(payload: NarrativePayload, ask,
                      temperature: float = 0.4) -> str | None:
    """LLM mode: `ask(question, system=..., temperature=...)` is the Hermes
    client signature; the payload JSON is the entire user message."""
    return ask(
        json.dumps(payload.model_dump(), sort_keys=True),
        system=GROUNDED_SYSTEM_PROMPT,
        temperature=temperature,
    )
