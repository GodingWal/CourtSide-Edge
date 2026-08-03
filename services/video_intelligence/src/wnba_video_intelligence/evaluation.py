"""Multi-object tracking evaluation: the go/no-go gate for the whole video track.

One number decides whether this stack is worth investing in: **how often does the tracker
swap two players' identities?** Everything downstream is built on persistent identity. A
defender-distance feature, a touches count, a matchup-time total -- each is meaningless if
player 7 silently becomes player 12 halfway through a possession, and worse than meaningless
because it will look like a plausible number rather than an error.

Broadcast basketball is close to the worst case for this: five same-uniform players, constant
occlusion, hard cuts, and a panning camera. So the identity metrics get measured *first*, on
real footage, before any effort goes into shot detection or spacing features.

Nothing here imports the upstream tracker or any production module. It scores association
data -- ``(frame, gt_id, pred_id)`` triples -- which makes it testable on synthetic sequences
with known switches, today, without footage.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

__all__ = [
    "Association",
    "TrackingReport",
    "evaluate_tracking",
]

# A ground-truth object is "mostly tracked" when a single predicted id covers at least this
# share of its visible frames. The MOTChallenge convention, kept so numbers are comparable
# with published trackers rather than being locally invented.
MOSTLY_TRACKED_RATIO = 0.8
MOSTLY_LOST_RATIO = 0.2


@dataclass(frozen=True)
class Association:
    """One matched observation: at ``frame``, ground-truth ``gt_id`` was called ``pred_id``.

    ``pred_id`` of ``None`` means the tracker missed the object in that frame -- a gap, not a
    switch. The two are counted separately because they call for different fixes: gaps point
    at the detector, switches at the association step.
    """

    frame: int
    gt_id: str
    pred_id: str | None


@dataclass
class TrackingReport:
    """Identity-quality summary for one clip."""

    frames: int = 0
    gt_objects: int = 0
    matched_observations: int = 0
    missed_observations: int = 0
    id_switches: int = 0
    fragmentations: int = 0
    mostly_tracked: int = 0
    partially_tracked: int = 0
    mostly_lost: int = 0
    switches_by_gt: dict[str, int] = field(default_factory=dict)

    @property
    def total_observations(self) -> int:
        return self.matched_observations + self.missed_observations

    @property
    def recall(self) -> float:
        """Share of ground-truth appearances the tracker found at all."""
        total = self.total_observations
        return self.matched_observations / total if total else 0.0

    @property
    def id_switch_rate(self) -> float:
        """Switches per matched observation. The headline go/no-go number.

        Reported per observation rather than per clip so that clips of different lengths can
        be compared without arithmetic sleight of hand.
        """
        return self.id_switches / self.matched_observations if self.matched_observations else 0.0

    @property
    def switches_per_object(self) -> float:
        return self.id_switches / self.gt_objects if self.gt_objects else 0.0

    def verdict(self, *, max_switch_rate: float = 0.01, min_recall: float = 0.9) -> str:
        """Plain-language gate decision.

        Thresholds are deliberately strict and deliberately arguable. The point is that the
        decision is made against a number fixed in advance, rather than by looking at an
        annotated video and forming an impression.
        """
        if self.matched_observations == 0:
            return "NO DATA: nothing matched; the detector or the association step is broken"
        problems = []
        if self.id_switch_rate > max_switch_rate:
            problems.append(
                f"identity switch rate {self.id_switch_rate:.3%} exceeds {max_switch_rate:.1%}"
            )
        if self.recall < min_recall:
            problems.append(f"recall {self.recall:.1%} below {min_recall:.0%}")
        if not problems:
            return "PASS: identity is stable enough to build features on"
        return "FAIL: " + "; ".join(problems)

    def summary(self) -> str:
        return (
            f"frames={self.frames} objects={self.gt_objects} "
            f"recall={self.recall:.1%} idsw={self.id_switches} "
            f"rate={self.id_switch_rate:.3%} frag={self.fragmentations} "
            f"MT={self.mostly_tracked} PT={self.partially_tracked} ML={self.mostly_lost}"
        )


def evaluate_tracking(associations: Iterable[Association]) -> TrackingReport:
    """Score identity stability over a clip.

    An **identity switch** is counted when the predicted id for a ground-truth object differs
    from the last predicted id it had. Crucially, a gap does not reset that memory: if a
    player is occluded and comes back with a different id, that is exactly the failure we care
    about, and forgiving it would flatter the tracker precisely where broadcast footage is
    hardest.

    A **fragmentation** is a resumption after a gap, regardless of whether the id changed.
    """
    by_gt: dict[str, list[Association]] = defaultdict(list)
    frames: set[int] = set()
    for item in associations:
        by_gt[item.gt_id].append(item)
        frames.add(item.frame)

    report = TrackingReport(frames=len(frames), gt_objects=len(by_gt))

    for gt_id, observations in by_gt.items():
        ordered: Sequence[Association] = sorted(observations, key=lambda a: a.frame)
        last_pred: str | None = None
        was_absent = False
        switches = 0
        matched = 0

        for obs in ordered:
            if obs.pred_id is None:
                report.missed_observations += 1
                was_absent = True
                continue

            matched += 1
            report.matched_observations += 1

            if last_pred is not None and obs.pred_id != last_pred:
                switches += 1
            if was_absent and last_pred is not None:
                report.fragmentations += 1

            last_pred = obs.pred_id
            was_absent = False

        report.id_switches += switches
        if switches:
            report.switches_by_gt[gt_id] = switches

        # Coverage by the single most persistent predicted id: a track that is "found" in 90%
        # of frames but under four different ids is not tracked, it is guessed repeatedly.
        counts: dict[str, int] = defaultdict(int)
        for obs in ordered:
            if obs.pred_id is not None:
                counts[obs.pred_id] += 1
        dominant = max(counts.values()) if counts else 0
        ratio = dominant / len(ordered) if ordered else 0.0

        if ratio >= MOSTLY_TRACKED_RATIO:
            report.mostly_tracked += 1
        elif ratio <= MOSTLY_LOST_RATIO:
            report.mostly_lost += 1
        else:
            report.partially_tracked += 1

    return report
