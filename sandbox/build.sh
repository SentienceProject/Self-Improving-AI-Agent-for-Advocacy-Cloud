#!/usr/bin/env bash
# Rebuilds the analysis image and records its ID in sandbox/image.lock.
#
# Run by the human reviewer after approving a package request. The agent must
# never run or edit this script, the Dockerfile, or the requirements files
# (enforce with deny rules).
set -euo pipefail

SANDBOX_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SANDBOX_DIR"

IMAGE_TAG="welfare-analysis:latest"

# Reuse the Dockerfile's pinned base image, so dependencies are resolved in
# exactly the environment the image runs in.
BASE_IMAGE="$(grep -m1 '^FROM ' Dockerfile | awk '{print $2}')"
if [[ "$BASE_IMAGE" != *@sha256:* || "$BASE_IMAGE" == *REPLACE* ]]; then
  echo "Error: pin the FROM line in the Dockerfile to a real digest first." >&2
  exit 1
fi

echo "==> Resolving requirements.in -> requirements.txt (exact versions + hashes)"
docker run --rm \
  -v "$SANDBOX_DIR":/sandbox -w /sandbox \
  --user "$(id -u):$(id -g)" -e HOME=/tmp \
  "$BASE_IMAGE" \
  sh -c 'pip install --quiet --disable-pip-version-check --timeout 60 uv \
         && python -m uv pip compile --quiet --generate-hashes \
              --output-file requirements.txt requirements.in'

echo "==> Building image"
docker build --iidfile image.lock.new -t "$IMAGE_TAG" .
NEW_ID="$(cat image.lock.new)"

echo "==> Smoke test (same restrictions run_analysis.py will use)"
if ! docker run --rm \
    --network none --read-only --tmpfs /tmp:size=64m \
    --cap-drop ALL --security-opt no-new-privileges \
    --user 1000:1000 \
    "$NEW_ID" \
    python -c "import pandas, numpy, scipy, matplotlib, networkx, sklearn, yaml, rapidfuzz, pypdf, pyarrow; print('imports ok')"
then
  rm -f image.lock.new
  echo "Error: smoke test failed; image.lock left unchanged." >&2
  exit 1
fi

mv image.lock.new image.lock
echo "==> Done. image.lock now pins: $(cat image.lock)"
