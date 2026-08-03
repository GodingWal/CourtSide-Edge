#!/usr/bin/env bash
# Clone the upstream tracker into this sandbox.
#
# abdullahtarek/basketball_analysis is cloned rather than vendored. Copying someone else's
# source into our tree would blur the licence boundary and make their updates painful to
# take; a pinned clone keeps the provenance obvious and the diff honest.
#
# The clone lands in ./upstream/, which is gitignored along with model weights and footage.
#
# Usage:
#   bash services/video_intelligence/setup_upstream.sh          # clone/update only
#   bash services/video_intelligence/setup_upstream.sh --vision # also install torch etc.

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
UPSTREAM_URL="${WNBA_UPSTREAM_URL:-https://github.com/abdullahtarek/basketball_analysis.git}"
UPSTREAM_DIR="$HERE/upstream"

if [[ -d "$UPSTREAM_DIR/.git" ]]; then
    echo "updating $UPSTREAM_DIR"
    git -C "$UPSTREAM_DIR" fetch --quiet origin
    git -C "$UPSTREAM_DIR" reset --quiet --hard origin/HEAD
else
    echo "cloning $UPSTREAM_URL"
    git clone --quiet --depth 50 "$UPSTREAM_URL" "$UPSTREAM_DIR"
fi
echo "  at $(git -C "$UPSTREAM_DIR" rev-parse --short HEAD)"

cat <<'NOTE'

Cloned. Two things it does NOT give you, both of which are on you:

  1. Model weights. The upstream README expects YOLOv5 (ball), YOLOv8 (court keypoints) and
     YOLO11 (players) in models/. They are downloaded separately.

  2. Footage. Nothing here fetches video. Broadcast basketball is licensed material, and the
     technical ability to download something is not permission to. Use footage you have
     documented rights to analyse.

Next step is deliberately not "run the pipeline". It is:

    measure the identity-switch rate on one real clip

Everything downstream -- defender distance, touches, matchup time, shot quality -- assumes
persistent player identity. If the tracker swaps two same-uniform players under the basket,
those features are not noisy, they are wrong in a way that looks plausible. Score it first:

    from wnba_video_intelligence import Association, evaluate_tracking
    report = evaluate_tracking(associations)   # (frame, gt_id, pred_id) triples
    print(report.summary(), report.verdict())

NOTE

if [[ "${1:-}" == "--vision" ]]; then
    echo "installing the vision stack (large; CPU wheels unless CUDA is present)"
    uv pip install --python "$(command -v python3)" \
        "ultralytics>=8.3" "opencv-python>=4.10" "numpy>=2.1"
fi
