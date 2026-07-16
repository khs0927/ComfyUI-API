from __future__ import annotations

import math
import os
import shutil
import subprocess
import traceback
import uuid
from functools import wraps
from pathlib import Path
from typing import Any, Callable

from beam import Image, Output, Volume, task_queue


MODEL_VOLUME = "/volumes/helios"
HELIOS_ROOT = "/workspace/Helios"
HELIOS_REVISION = "8f2a2faab3298c8a7630a2c73aea37c01b5bab01"
MODEL_ID = os.getenv("HELIOS_MODEL", "BestWishYsh/Helios-Distilled")

image = (
    Image(python_version="python3.11")
    .add_commands(
        [
            (
                "apt-get update -y && apt-get install -y git ffmpeg libgl1 "
                "libglib2.0-0 libopenmpi-dev"
            ),
            (
                "git clone --depth=1 https://github.com/PKU-YuanGroup/Helios.git "
                f"{HELIOS_ROOT} && git -C {HELIOS_ROOT} checkout --detach {HELIOS_REVISION}"
            ),
            # Current Helios pins huggingface-hub to 1.4.1, while its pinned
            # development Diffusers revision requires >=1.23.0.  Keep the
            # rest of Helios' upstream requirements unchanged, but replace
            # that incompatible pin before resolving the image dependencies.
            (
                f"cd {HELIOS_ROOT} && "
                "sed -i 's/huggingface-hub==1\\.4\\.1/huggingface-hub>=1.23.0,<2.0/g' "
                "requirements.txt && pip install -r requirements.txt"
            ),
            "pip install 'huggingface_hub[hf_xet]'",
        ]
    )
)


def _newest_video(folder: Path) -> Path:
    candidates = [
        path
        for path in folder.rglob("*")
        if path.is_file() and path.suffix.lower() in {".mp4", ".webm", ".mov", ".mkv"}
    ]
    if not candidates:
        raise RuntimeError(f"Helios produced no video in {folder}")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _model_path() -> Path:
    from huggingface_hub import snapshot_download

    target = Path(MODEL_VOLUME) / "models" / "Helios-Distilled"
    marker = target / ".download-complete"
    if not marker.exists():
        target.mkdir(parents=True, exist_ok=True)
        snapshot_download(
            repo_id=MODEL_ID,
            local_dir=str(target),
            token=os.getenv("HF_TOKEN") or None,
        )
        marker.touch()
    return target


def _capture_worker_errors(handler: Callable[..., dict[str, Any]]):
    @wraps(handler)
    def wrapped(**inputs: Any) -> dict[str, Any]:
        try:
            return handler(**inputs)
        except Exception as exc:
            error_path = Path("/tmp") / f"beam-{uuid.uuid4().hex}.error.txt"
            error_path.write_text(traceback.format_exc(), encoding="utf-8")
            Output(path=str(error_path)).save()
            return {"error": f"{type(exc).__name__}: {exc}"}

    return wrapped


@task_queue(
    name="helios-long-video",
    image=image,
    cpu=8,
    memory="64Gi",
    gpu=["H100", "RTX4090", "A10G"],
    timeout=21600,
    workers=1,
    keep_warm_seconds=0,
    max_pending_tasks=24,
    retries=1,
    authorized=True,
    volumes=[Volume(name="helios-models", mount_path=MODEL_VOLUME)],
)
@_capture_worker_errors
def generate(**inputs: Any) -> dict[str, Any]:
    prompt = str(inputs.get("prompt", "")).strip()
    if not prompt:
        raise ValueError("prompt is required")

    duration = max(3, min(int(inputs.get("duration_seconds", 30)), 90))
    width = max(256, min(int(inputs.get("width", 960)), 1920))
    height = max(256, min(int(inputs.get("height", 544)), 1080))
    fps = max(12, min(int(inputs.get("fps", 24)), 30))
    seed = int(inputs.get("seed", 0))
    negative_prompt = str(inputs.get("negative_prompt", "")).strip()
    frame_count = max(99, math.ceil(duration * fps / 33) * 33)

    model_path = _model_path()
    task_root = Path("/tmp") / f"helios-{uuid.uuid4().hex}"
    raw_dir = task_root / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    infer_script = Path(HELIOS_ROOT) / "infer_helios.py"
    command = [
        "python",
        str(infer_script),
        "--base_model_path",
        str(model_path),
        "--transformer_path",
        str(model_path),
        "--sample_type",
        "t2v",
        "--prompt",
        prompt,
        "--num_frames",
        str(frame_count),
        "--height",
        str(height),
        "--width",
        str(width),
        "--fps",
        str(fps),
        "--guidance_scale",
        "1.0",
        "--seed",
        str(seed),
        "--is_enable_stage2",
        "--pyramid_num_inference_steps_list",
        "2",
        "2",
        "2",
        "--is_amplify_first_chunk",
        "--output_folder",
        str(raw_dir),
    ]
    if negative_prompt:
        command.extend(["--negative_prompt", negative_prompt])

    if os.getenv("BEAM_HELIOS_LOW_VRAM", "true").lower() == "true":
        command.extend(
            [
                "--enable_low_vram_mode",
                "--group_offloading_type",
                "leaf_level",
            ]
        )

    environment = os.environ.copy()
    environment["HF_HOME"] = str(Path(MODEL_VOLUME) / "hf-cache")
    inference = subprocess.run(
        command,
        cwd=str(infer_script.parent),
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if inference.returncode != 0:
        stdout_tail = (inference.stdout or "")[-12000:]
        stderr_tail = (inference.stderr or "")[-12000:]
        raise RuntimeError(
            "Helios inference failed with exit code "
            f"{inference.returncode}.\n--- stdout (tail) ---\n{stdout_tail}"
            f"\n--- stderr (tail) ---\n{stderr_tail}"
        )

    raw = _newest_video(raw_dir)
    final = task_root / "scene.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(raw),
            "-t",
            str(duration),
            "-vf",
            (
                f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,fps={fps},format=yuv420p"
            ),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-an",
            "-movflags",
            "+faststart",
            str(final),
        ],
        check=True,
    )

    Output(path=str(final)).save()
    size_bytes = final.stat().st_size
    shutil.rmtree(raw_dir, ignore_errors=True)
    return {
        "duration_seconds": duration,
        "frame_count": frame_count,
        "size_bytes": size_bytes,
        "model": MODEL_ID,
    }
