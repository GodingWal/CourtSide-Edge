"""Bitemporal selection. The single chokepoint through which historical reads must pass.

Every look-ahead bug this platform could have takes the same shape: reading a row that was
*true* before the forecast moment but only *known* after it. A player ruled out at 17:40 was
questionable, as far as we were concerned, at 15:00 -- and a model trained against the 17:40
row learns to forecast the past with uncanny skill.

The defence is to make the correct read the easy one. Feature builders call :func:`as_of` and
:func:`latest_known`; nothing else touches history.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from datetime import datetime

from wnba_domain.base import BitemporalRecord

__all__ = ["LeakageError", "as_of", "assert_no_leakage", "latest_known", "timeline"]


class LeakageError(AssertionError):
    """Raised when a record that could not have been known is used as a forecast input."""


def as_of[R: BitemporalRecord](
    records: Iterable[R],
    *,
    valid_time: datetime,
    system_time: datetime,
) -> list[R]:
    """Records that were true at ``valid_time`` and known to us by ``system_time``.

    Both bounds are required keyword arguments. That is not pedantry: a positional-friendly
    signature is exactly how ``as_of(records, t)`` gets written at 1am and silently defaults
    the system axis to "now", which reintroduces every leak this module exists to prevent.
    """
    return [r for r in records if r.is_observable_at(valid_time, system_time)]


def latest_known[R: BitemporalRecord](
    records: Iterable[R],
    *,
    system_time: datetime,
    valid_time: datetime | None = None,
    key: Callable[[R], object] | None = None,
) -> R | None:
    """The most recently *observed* record we had by ``system_time``.

    When ``valid_time`` is omitted it defaults to ``system_time`` -- i.e. "our current best
    belief about the present". Ordering is by ``system_from``, not ``valid_from``, because the
    question being asked is "what did we believe", not "what was true".
    """
    effective_valid = system_time if valid_time is None else valid_time
    candidates = as_of(records, valid_time=effective_valid, system_time=system_time)
    if not candidates:
        return None
    if key is not None:
        candidates = sorted(candidates, key=lambda r: (r.system_from, str(key(r))))
    else:
        candidates.sort(key=lambda r: r.system_from)
    return candidates[-1]


def timeline[R: BitemporalRecord](records: Iterable[R]) -> list[R]:
    """Records ordered by when we learned them. The replay order for an audit."""
    return sorted(records, key=lambda r: (r.system_from, r.valid_from))


def assert_no_leakage[R: BitemporalRecord](
    records: Sequence[R], *, forecast_time: datetime
) -> None:
    """Guard for feature builders: every input must have been knowable at ``forecast_time``.

    Call this on the exact record set that produced a feature vector. It is cheap, and it turns
    a class of silent, career-limiting bug into a loud one.
    """
    leaked = [r for r in records if not r.is_known_at(forecast_time)]
    if leaked:
        first = leaked[0]
        raise LeakageError(
            f"{len(leaked)} record(s) were used for a forecast at {forecast_time.isoformat()} "
            f"but were not known until {first.system_from.isoformat()}; "
            f"first offender: {type(first).__name__}"
        )
