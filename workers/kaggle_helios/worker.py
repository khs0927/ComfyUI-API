from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path


WORK = Path("/kaggle/working") if Path("/kaggle/working").exists() else Path.cwd()
REQUEST_PATH = next(
    (
        path
        for path in (
            Path.cwd() / "request.json",
            Path(__file__).resolve().parent / "request.json",
            Path("/kaggle/src/request.json"),
            WORK / "request.json",
        )
        if path.exists()
    ),
    WORK / "request.json",
)
OUTPUT = WORK / "scene.mp4"
WAN_ROOT = WORK / "Wan2.1"
MODEL_DIR = WORK / "models" / "Wan2.1-T2V-1.3B"


def run(command: list[str], cwd: Path | None = None) -> None:
    print("RUN:", " ".join(command), flush=True)
    subprocess.run(command, cwd=str(cwd) if cwd else None, check=True)


def find_preloaded_model() -> Path | None:
    input_root = Path("/kaggle/input")
    if not input_root.exists():
        return None
    markers = ("models_t5_umt5-xxl-enc-bf16.pth", "Wan2.1_VAE.pth")
    for candidate in input_root.rglob("*"):
        if candidate.is_dir() and any((candidate / marker).exists() for marker in markers):
            return candidate
    return None


def prepare_runtime() -> Path:
    preloaded = find_preloaded_model()
    if not WAN_ROOT.exists():
        run(["git", "clone", "--depth=1", "https://github.com/Wan-Video/Wan2.1.git", str(WAN_ROOT)])
        run([sys.executable, "-m", "pip", "install", "-q", "-r", str(WAN_ROOT / "requirements.txt")])
    if preloaded:
        return preloaded
    if not MODEL_DIR.exists():
        run([sys.executable, "-m", "pip", "install", "-q", "huggingface_hub[hf_xet]"])
        from huggingface_hub import snapshot_download

        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        snapshot_download(
            repo_id="Wan-AI/Wan2.1-T2V-1.3B",
            local_dir=str(MODEL_DIR),
            token=os.getenv("HF_TOKEN") or None,
        )
    return MODEL_DIR


def generate_chunk(model_dir: Path, prompt: str, seed: int, index: int) -> Path:
    raw = WORK / f"wan-{index:03d}.mp4"
    run(
        [
            sys.executable,
            str(WAN_ROOT / "generate.py"),
            "--task",
            "t2v-1.3B",
            "--size",
            "832*480",
            "--frame_num",
            "81",
            "--ckpt_dir",
            str(model_dir),
            "--offload_model",
            "True",
            "--t5_cpu",
            "--sample_shift",
            "8",
            "--sample_guide_scale",
            "6",
            "--base_seed",
            str(seed),
            "--save_file",
            str(raw),
            "--prompt",
            prompt,
        ],
        cwd=WAN_ROOT,
    )
    if not raw.exists():
        candidates = sorted(WAN_ROOT.rglob("*.mp4"), key=lambda path: path.stat().st_mtime)
        if not candidates:
            raise RuntimeError("Wan2.1 produced no MP4")
        shutil.copy2(candidates[-1], raw)
    return raw


def main() -> None:
    if REQUEST_PATH.exists():
        request = json.loads(REQUEST_PATH.read_text(encoding="utf-8"))
    else:
        request = json.loads(os.getenv("KAGGLE_REQUEST_JSON", "{}"))
        request.setdefault(
            "prompt",
            "A calm cinematic sunrise over mountains, natural camera motion",
        )
        request.setdefault("duration_seconds", 5)
        request.setdefault("width", 832)
        request.setdefault("height", 480)
        request.setdefault("fps", 16)
        request.setdefault("seed", 42)
    prompt = str(request["prompt"])
    duration = max(3, min(int(request.get("duration_seconds", 10)), 30))
    width = max(256, min(int(request.get("width", 832)), 1280))
    height = max(256, min(int(request.get("height", 480)), 720))
    fps = max(12, min(int(request.get("fps", 16)), 24))
    seed = int(request.get("seed", 42))

    model_dir = prepare_runtime()
    chunk_count = max(1, math.ceil(duration / 5))
    chunks = [
        generate_chunk(
            model_dir,
            f"{prompt}. Continuous segment {index + 1} of {chunk_count}.",
            seed + index,
            index,
        )
        for index in range(chunk_count)
    ]

    concat_file = WORK / "concat.txt"
    concat_file.write_text(
        "\n".join(f"file '{path.as_posix()}'" for path in chunks),
        encoding="utf-8",
    )
    run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_file),
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
            "21",
            "-an",
            "-movflags",
            "+faststart",
            str(OUTPUT),
        ]
    )
    print(json.dumps({"output": str(OUTPUT), "duration_seconds": duration}))


if __name__ == "__main__":
    main()
