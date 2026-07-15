#!/usr/bin/env bash
set -euo pipefail

if [ -z "${BEAM_TOKEN:-}" ]; then
  echo "Set BEAM_TOKEN before deploying." >&2
  exit 2
fi

python -m pip install --upgrade pip
python -m pip install -r requirements-beam.txt
beam configure default --token "$BEAM_TOKEN"
beam deploy workers/beam_helios.py:generate

cat <<'EOF'

Copy the invocation URL printed above into BEAM_TASK_QUEUE_URL.
Keep BEAM_TOKEN in the controller environment and never commit it.
The first deployment/build and first model download can take substantially longer
than later scene jobs because the image and Helios weights must be prepared.
EOF
