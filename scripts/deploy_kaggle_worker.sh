#!/usr/bin/env bash
set -euo pipefail

if [ -z "${KAGGLE_KERNEL_ID:-}" ]; then
  echo "Set KAGGLE_KERNEL_ID as username/kernel-slug." >&2
  exit 2
fi

if [ ! -f "$HOME/.kaggle/kaggle.json" ] && { [ -z "${KAGGLE_USERNAME:-}" ] || [ -z "${KAGGLE_KEY:-}" ]; }; then
  echo "Configure ~/.kaggle/kaggle.json or KAGGLE_USERNAME and KAGGLE_KEY." >&2
  exit 2
fi

python -m pip install --upgrade pip
python -m pip install "kaggle>=2.0"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT
cp -R workers/kaggle_helios/. "$TMP_DIR/"

python - "$TMP_DIR/kernel-metadata.json" "$KAGGLE_KERNEL_ID" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
payload = json.loads(path.read_text(encoding="utf-8"))
payload["id"] = sys.argv[2]
payload["title"] = "Open video fallback worker"
payload["is_private"] = "true"
payload["enable_gpu"] = "true"
payload["enable_internet"] = "true"
path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
PY

cat > "$TMP_DIR/request.json" <<'JSON'
{
  "prompt": "A calm cinematic sunrise over mountains, natural camera motion",
  "duration_seconds": 5,
  "width": 832,
  "height": 480,
  "fps": 16,
  "seed": 42
}
JSON

kaggle kernels push \
  -p "$TMP_DIR" \
  --accelerator "${KAGGLE_ACCELERATOR:-NvidiaTeslaT4}" \
  --timeout "${KAGGLE_SCENE_TIMEOUT:-21600}"

cat <<EOF

Kaggle worker submitted: $KAGGLE_KERNEL_ID
Check it with:
  kaggle kernels status $KAGGLE_KERNEL_ID
Download the latest MP4 with:
  kaggle kernels output $KAGGLE_KERNEL_ID -p ./kaggle-output -o

The first run downloads and installs the model unless you attach a preloaded
Wan2.1 model dataset. Treat Kaggle as a batch fallback, not a guaranteed service.
EOF
