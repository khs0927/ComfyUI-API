#!/usr/bin/env bash
set -euo pipefail

ROOT="${1:-$HOME/hs-video-worker}"
PYTHON_BIN="${PYTHON_BIN:-python3.11}"
mkdir -p "$ROOT"
cd "$ROOT"

if [ ! -d ComfyUI ]; then
  git clone --depth 1 https://github.com/comfyanonymous/ComfyUI.git
fi

if [ ! -d LTX-2 ]; then
  git clone --depth 1 https://github.com/Lightricks/LTX-2.git
fi

if [ ! -d .venv ]; then
  "$PYTHON_BIN" -m venv .venv
fi
source .venv/bin/activate
python -m pip install --upgrade pip wheel
python -m pip install -r ComfyUI/requirements.txt
python -m pip install huggingface_hub hf_transfer

mkdir -p ComfyUI/models/checkpoints ComfyUI/user/default/workflows
cat <<'EOF'

Base software is installed.

Next steps:
1. Download the LTX-2.3 weights accepted by the Lightricks license:
   hf download Lightricks/LTX-2.3 --local-dir "$ROOT/models/LTX-2.3"
2. Put/link the required checkpoints in the ComfyUI model directories described by LTX-2 docs.
3. Open ComfyUI, load an official LTX-2.3 workflow, and export it in API format.
4. Replace editable values with these exact string tokens:
   {{PROMPT}} {{NEGATIVE_PROMPT}} {{WIDTH}} {{HEIGHT}}
   {{FPS}} {{DURATION_SECONDS}} {{FRAME_COUNT}} {{SEED}}
5. Save it as workflows/ltx23_video_api.json in this repository.
6. Start ComfyUI:
   cd "$ROOT/ComfyUI" && ../.venv/bin/python main.py --listen 0.0.0.0 --port 8188

For remote mobile access, expose only through a private authenticated tunnel.
Do not publish an unauthenticated ComfyUI port to the public Internet.
EOF
