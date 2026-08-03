# WNBA video intelligence

An isolated experimental sensor. Excluded from the uv workspace, imported by nothing in the
forecasting path, and unable to block or bias a forecast. A test enforces that isolation.

The target is spatial and role information ordinary box scores cannot express: court position,
spacing, defender distance, matchup time, touches, possession duration, shot quality, rebound
positioning and movement load. Recreating points and rebounds from video is a *validation*
task, not the product — those numbers are already available for free.

## Start here

```bash
bash services/video_intelligence/setup_upstream.sh
```

That clones [`abdullahtarek/basketball_analysis`](https://github.com/abdullahtarek/basketball_analysis)
into `upstream/` (gitignored). It provides player/ball tracking, court keypoints, jersey-colour
team assignment, possession, and pass/interception detection via YOLOv5/v8/v11.

## The gate, before anything else

**Measure the identity-switch rate on one real clip.** Not shot detection, not spacing — that
one number decides whether this stack survives contact with broadcast footage.

Everything downstream assumes persistent player identity. Broadcast basketball is close to the
worst case for it: five same-uniform players, constant occlusion, hard cuts, a panning camera.
If the tracker swaps two players under the basket, a defender-distance feature is not noisy —
it is wrong in a way that looks entirely plausible, which is worse.

```python
from wnba_video_intelligence import Association, evaluate_tracking

report = evaluate_tracking(associations)   # (frame, gt_id, pred_id) triples
print(report.summary())
print(report.verdict())                    # PASS / FAIL / NO DATA, against fixed thresholds
```

An id change across an occlusion gap counts as a switch, deliberately: forgiving it would
flatter the tracker exactly where it matters most. The metrics run with no footage, no torch
and no GPU, so the go/no-go decision is testable before committing to a 2 GB dependency.

## What upstream does not give you

Honest gap list, roughly in dependency order:

| Gap | Why it matters |
|---|---|
| Persistent player identity | Every feature below depends on it |
| Jersey-number OCR | The only reliable way to re-acquire identity after occlusion |
| Broadcast camera calibration | Without homography there are no court coordinates |
| Occlusion handling | Five same-coloured players in the paint |
| Substitution detection | Tracks must end when a player leaves |
| Shot attempt + make/miss | Needed to align with play-by-play |
| Rebound and assist attribution | Multi-agent, ambiguous |
| Defensive matchup tracking | The headline feature |
| Play-by-play synchronisation | Frame time to game clock |
| WNBA-specific training data | Upstream is trained on other footage |

## Promotion

`feature_registry.yaml` declares every candidate feature with its metric and threshold **fixed
in advance**. A threshold chosen after seeing the result is not a threshold, it is a
rationalisation. `load_registry()` refuses to load a registry that declares itself a
production dependency.

Video-derived fields reach the domain only as optional, flagged inputs
(`Shot.is_video_derived`, `DefensiveMatchup.confidence`) and never gate a recommendation alone.

## Footage and rights

Nothing here downloads video. Broadcast basketball is licensed material, and the technical
ability to fetch something is not permission to use it. Supply footage you have documented
rights to analyse.
