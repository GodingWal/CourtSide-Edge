# Computer-vision implementation plan

## Objective

Extract reliable spatial and role features from licensed WNBA footage that improve held-out
player-prop forecasts. Production must continue operating when video is absent.

## Training data and rights

Before collecting frames, record the footage owner, licence, permitted storage, permitted model
training, retention period and redistribution limits. Split by game—not frame—so adjacent frames
cannot leak between train and test. Maintain separate broadcast arenas, camera angles, uniforms,
lighting conditions and playoff/regular-season games in the held-out set.

## Label program

1. Court keypoints: corners, lane intersections, circles, three-point arc landmarks and rim.
2. Object detection: player, ball and referee boxes with visibility/occlusion flags.
3. Tracking: persistent anonymous track IDs through cuts and short occlusions.
4. Identity: team, jersey number, canonical player and uncertainty; never infer identity from
   body appearance alone.
5. Possession/events: possessor, touch, pass, shot release, rim contact, make/miss, rebound,
   turnover and substitution.
6. Matchups: offensive player, primary defender and segment boundaries.
7. Review labels: disagreement type, corrected value and reviewer.

## Delivery stages

### Stage 0: benchmark harness

- Import reusable concepts from `abdullahtarek/basketball_analysis` behind adapters.
- Pin the upstream commit and preserve its licence.
- Build frame/timestamp manifests and official play-by-play alignment.
- Define game-level train/validation/test manifests before training.

### Stage 1: geometry and tracks

- Fine-tune player/ball/referee detection on WNBA broadcasts.
- Train court-keypoint detection and per-shot homography.
- Benchmark ByteTrack and BoT-SORT with HOTA, IDF1, identity switches and fragmentation.
- Reject frames whose court reprojection error exceeds the registered threshold.

### Stage 2: identity and events

- Team classification from calibrated uniform colours.
- Jersey OCR with temporal voting and roster-constrained candidates.
- Possession/touch segmentation and pass/shot/rebound events.
- Align detected events to official play-by-play using dynamic time warping.

### Stage 3: forecasting features

- Touches and possession duration per minute.
- Shot location, defender distance and expected shot quality.
- Potential assists and teammate shot quality after passes.
- Rebound position, nearby competitors and rebound chances.
- Defensive matchup time and size differentials.
- Spacing, paint occupancy, transition participation, distance and high-intensity movement.

## Review queue

Any identity below 0.95, event below its registered threshold, play-by-play conflict, unexplained
track switch or calibration failure enters human review. Corrections become new labelled data;
they never rewrite the raw detector output.

## Promotion gate

First meet task metrics in `feature_registry.yaml`. Then run the feature in shadow forecasts.
Promote only when rolling-origin log loss improves without subgroup degradation. A video feature
that merely recreates a box-score field has no production value.

## Initial dataset estimate

Start with 25 licensed games: 15 training, 5 validation and 5 untouched test games. Label about
15,000 diverse frames for detection/keypoints and fully annotate 1,000 possessions for events.
Expand based on measured failure clusters rather than indiscriminately extracting more frames.
