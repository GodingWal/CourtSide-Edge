"""Post-generation numeric scan (P0-2).

The narrative layer never restates or recomputes numbers — every digit
sequence in LLM output must be present in the input payload (within rounding
tolerance). Anything else is FABRICATED_NUMERIC and the narrative is rejected.

Allowed forms for a payload value v:
- exact within `tolerance` (default 0.05, i.e. one-decimal rounding)
- the whole-number rounding of v (e.g. "19" for 18.9)
- the percentage form of a probability in [0, 1] (e.g. "62%"/"62" for 0.62)
"""
import re

from shared.picks.models import NarrativePayload, Pick
from shared.picks.reason_codes import ReasonCode

# Numbers not embedded inside alphanumeric tokens (skips "3PM", "L5").
_NUMBER_RE = re.compile(r"(?<![\w.])[-+]?\d+(?:\.\d+)?(?![\w.])")

DEFAULT_TOLERANCE = 0.05


def collect_numbers(obj) -> set[float]:
    """Every numeric leaf in a payload structure (dicts/lists/models)."""
    values: set[float] = set()
    if isinstance(obj, bool):
        return values
    if isinstance(obj, (int, float)):
        values.add(float(obj))
    elif isinstance(obj, dict):
        for item in obj.values():
            values |= collect_numbers(item)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            values |= collect_numbers(item)
    elif hasattr(obj, "model_dump"):
        values |= collect_numbers(obj.model_dump())
    return values


def allowed_numbers(
    payload: NarrativePayload | dict, pick: Pick | None = None, extra: tuple[float, ...] = ()
) -> set[float]:
    values = collect_numbers(payload)
    if pick is not None:
        values |= collect_numbers(pick.model_dump())  # includes the computed edge
    values |= {float(v) for v in extra}
    return values


def _matches(number: float, value: float, tolerance: float) -> bool:
    if abs(number - value) <= tolerance + 1e-9:
        return True
    # Whole-number rounding of the payload value ("19 points" for 18.9).
    if number == int(number) and abs(number - value) <= 0.5 + 1e-9:
        return True
    # Percentage form of a probability ("62%" for 0.62).
    if 0.0 <= value <= 1.0 and abs(number - 100.0 * value) <= 0.5 + 1e-9:
        return True
    return False


def scan_narrative(
    narrative: str,
    payload: NarrativePayload | dict,
    pick: Pick | None = None,
    tolerance: float = DEFAULT_TOLERANCE,
    extra: tuple[float, ...] = (),
) -> list[dict]:
    """Return one violation per narrative number absent from the payload."""
    allowed = allowed_numbers(payload, pick, extra)
    violations = []
    for match in _NUMBER_RE.finditer(narrative):
        number = float(match.group())
        if not any(_matches(number, value, tolerance) for value in allowed):
            violations.append(
                {
                    "code": ReasonCode.FABRICATED_NUMERIC.value,
                    "span": match.group(),
                    "position": match.start(),
                }
            )
    return violations
