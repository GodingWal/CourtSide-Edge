"""Grounded-narrative claim verification (P0-3).

Claim extraction here is deterministic (regex over the narrative); an
LLM-as-judge extractor can be layered on top, but verification is always
deterministic against the payload — a claim either maps to a payload field or
the narrative is rejected with UNGROUNDED_CLAIM and the offending span.

Verified claim kinds:
- "debut" / "first career game"  → requires player.career_games == 0
- "rookie"                       → requires player.seasons <= 1
- "first game back"              → requires a returning-flagged injury record
                                   for the pick's player
- entity names                   → must map to the pick player, teams,
                                   opponent, book, or an injuries[] entry
- injury-context sentences       → every name in a sentence with injury
                                   keywords must exist in injuries[]
"""
import re
from dataclasses import dataclass

from shared.picks.models import NarrativePayload
from shared.picks.reason_codes import ReasonCode

_DEBUT_RE = re.compile(r"\b(?:(?:WNBA|league|pro)\s+)?debut\b|\bfirst\s+career\s+game\b", re.I)
_ROOKIE_RE = re.compile(r"\brookie\b", re.I)
_RETURN_RE = re.compile(r"\bfirst\s+game\s+back\b", re.I)
_INJURY_KEYWORD_RE = re.compile(
    r"\b(?:out|injur\w*|sidelined|questionable|doubtful|probable|ruled\s+out)\b", re.I
)
_SENTENCE_RE = re.compile(r"[^.!?\n]+[.!?]?")
# Proper-noun candidates: runs of capitalized words (allows "Las Vegas Aces").
_NAME_RE = re.compile(r"\b[A-Z][a-z]+(?:[\s'-][A-Z][a-z]+)+\b")
_SINGLE_NAME_RE = re.compile(r"\b[A-Z][a-z]+\b")

# Sentence-position words and common narrative openers that look like names.
_STOPWORDS = {
    "the", "she", "her", "his", "with", "without", "while", "after", "before",
    "expect", "look", "take", "this", "that", "despite", "against", "over",
    "under", "buy", "sell", "points", "rebounds", "assists", "tonight", "if",
    "in", "on", "at", "and", "but", "for", "a", "an", "as", "monday", "tuesday",
    "wednesday", "thursday", "friday", "saturday", "sunday",
}


@dataclass
class Claim:
    kind: str
    span: str
    position: int


def extract_claims(narrative: str) -> list[Claim]:
    claims = []
    for regex, kind in (
        (_DEBUT_RE, "debut"),
        (_ROOKIE_RE, "rookie"),
        (_RETURN_RE, "first_game_back"),
    ):
        for match in regex.finditer(narrative):
            claims.append(Claim(kind=kind, span=match.group(), position=match.start()))
    for match in _NAME_RE.finditer(narrative):
        claims.append(Claim(kind="entity", span=match.group(), position=match.start()))
    return claims


def _known_entities(payload: NarrativePayload) -> list[str]:
    known = [
        payload.player.name,
        payload.player.team,
        payload.matchup.opponent,
        payload.stat.book,
    ]
    for record in payload.injuries:
        known += [record.player, record.team]
    return [k.lower() for k in known if k]


def _maps_to_known(candidate: str, known: list[str]) -> bool:
    cand = candidate.lower().strip()
    return any(cand in entity or entity in cand for entity in known)


def _injury_sentence_names(sentence: str) -> list[str]:
    """Name candidates in an injury-context sentence, including bare surnames."""
    names = [m.group() for m in _NAME_RE.finditer(sentence)]
    covered = " ".join(names)
    for match in _SINGLE_NAME_RE.finditer(sentence):
        word = match.group()
        if word.lower() in _STOPWORDS or word in covered:
            continue
        if match.start() == 0:  # sentence-leading capitalization is ambiguous
            continue
        names.append(word)
    return names


def verify_claims(narrative: str, payload: NarrativePayload) -> list[dict]:
    """Deterministically verify every extracted claim against the payload."""
    violations: list[dict] = []
    known = _known_entities(payload)
    injured = [rec.player.lower() for rec in payload.injuries]
    injured += [rec.team.lower() for rec in payload.injuries]
    player_name = payload.player.name.lower()

    def reject(claim_span: str, reason: str, position: int = -1):
        violations.append(
            {
                "code": ReasonCode.UNGROUNDED_CLAIM.value,
                "span": claim_span,
                "reason": reason,
                "position": position,
            }
        )

    for claim in extract_claims(narrative):
        if claim.kind == "debut" and payload.player.career_games != 0:
            reject(
                claim.span,
                f"'debut' requires career_games == 0; payload has {payload.player.career_games}",
                claim.position,
            )
        elif claim.kind == "rookie" and payload.player.seasons > 1:
            reject(
                claim.span,
                f"'rookie' requires seasons <= 1; payload has {payload.player.seasons}",
                claim.position,
            )
        elif claim.kind == "first_game_back":
            returning = any(
                rec.returning and player_name in rec.player.lower() for rec in payload.injuries
            )
            if not returning:
                reject(
                    claim.span,
                    "'first game back' requires a returning-flagged injury record "
                    "for this player",
                    claim.position,
                )
        elif claim.kind == "entity" and not _maps_to_known(claim.span, known):
            reject(claim.span, "name not present in payload", claim.position)

    # Injury-context sentences: every named player/team must be in injuries[].
    for sentence_match in _SENTENCE_RE.finditer(narrative):
        sentence = sentence_match.group()
        if not _INJURY_KEYWORD_RE.search(sentence):
            continue
        for name in _injury_sentence_names(sentence):
            lowered = name.lower()
            if lowered in player_name or player_name in lowered:
                # The pick's own player legitimately appears in injury-adjacent
                # prose ("X benefits with ... out"); her own status is enforced
                # by the roster/staleness gates, not narrative text.
                continue
            if not any(lowered in entry or entry in lowered for entry in injured):
                reject(
                    name,
                    "injury context references a player/team absent from injuries[]",
                    sentence_match.start() + sentence.find(name),
                )

    return violations
