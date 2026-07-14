#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-helios}"
ROOT="${2:-$HOME/hs-free-video-worker}"
PYTHON_BIN="${PYTHON_BIN:-python3.11}"

mkdir -p "$ROOT/vendor" "$ROOT/models"
cd "$ROOT"

if [ ! -d .venv ]; then
  "$PYTHON_BIN" -m venv .venv
fi
source .venv/bin/activate
python -m pip install --upgrade pip wheel setuptools
python -m pip install "huggingface_hub[cli]>=0.30" hf_transfer

case "$MODE" in
  helios)
    if [ ! -d vendor/Helios ]; then
      git clone --depth 1 https://github.com/PKU-YuanGroup/Helios.git vendor/Helios
    fi
    cd vendor/Helios
    bash install.sh
    cd "$ROOT"
    hf download BestWishYsh/Helios-Distilled \
      --local-dir "$ROOT/models/Helios-Distilled"
    cat <<EOF
Helios-Distilled installed.

Add these values to .env.video:
HELIOS_ROOT=$ROOT/vendor/Helios
HELIOS_INFER_SCRIPT=$ROOT/vendor/Helios/scripts/inference/infer_helios.py
HELIOS_MODEL=$ROOT/models/Helios-Distilled

Helios code and weights are published under Apache-2.0 metadata.
The low-VRAM group-offload path targets roughly 6GB VRAM, but generation
speed depends heavily on GPU, RAM, CPU and storage bandwidth.
EOF
    ;;
  wan21)
    if [ ! -d vendor/Wan2.1 ]; then
      git clone --depth 1 https://github.com/Wan-Video/Wan2.1.git vendor/Wan2.1
    fi
    python -m pip install -r vendor/Wan2.1/requirements.txt
    hf download Wan-AI/Wan2.1-T2V-1.3B \
      --local-dir "$ROOT/models/Wan2.1-T2V-1.3B"
    cat <<EOF
Wan2.1-T2V-1.3B installed.

Add these values to .env.video:
WAN21_ROOT=$ROOT/vendor/Wan2.1
WAN21_MODEL=$ROOT/models/Wan2.1-T2V-1.3B
WAN21_NATIVE_FPS=16

Wan2.1-T2V-1.3B is Apache-2.0 and officially requires about 8.19GB VRAM.
EOF
    ;;
  *)
    echo "Usage: $0 {helios|wan21} [install-root]" >&2
    exit 2
    ;;
esac

cat <<'EOF'

No hosted inference API or paid provider was configured.
Run the FastAPI controller on this same machine and expose only the authenticated
controller endpoint. Never expose ComfyUI or a raw model server without auth.
EOF
