from __future__ import annotations

import asyncio
import json
import math
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .core import LocalProvider, ScenePlan, VideoRequest


PRESETS: dict[str, dict[str, Any]] = {
    "helios_realtime": {
        "space_id": "BestWishYsh/Helios-14B-RealTime-AOTI",
        "api_name": "/generate_video",
        "max_chunk_seconds": 9,
    },
    "ltx_fast": {
        "space_id": "Lightricks/ltx-video-distilled",
        "api_name": "/text_to_video",
        "max_chunk_seconds": 8,
    },
    "wan22_5b": {
        "space_id": "Wan-AI/Wan-2.2-5B",
        "api_name": "/generate_video",
        "max_chunk_seconds": 5,
        "paused": True,
    },
}


def apply_hf_preset_env() -> str | None:
    """Populate generic Space settings from a verified built-in preset."""
    preset_name = os.getenv("HF_VIDEO_SPACE_PRESET", "").strip().lower()
    if not preset_name:
        return None
    preset = PRESETS.get(preset_name)
    if not preset:
        allowed = ", ".join(sorted(PRESETS))
        raise RuntimeError(f"Unknown HF_VIDEO_SPACE_PRESET={preset_name!r}; use {allowed}")
    if preset.get("paused") and os.getenv("HF_ALLOW_PAUSED_PRESET", "false").lower() != "true":
        raise RuntimeError(f"Hugging Face preset {preset_name} is currently marked paused")
    os.environ.setdefault("HF_VIDEO_SPACE_ID", str(preset["space_id"]))
    os.environ.setdefault("HF_VIDEO_SPACE_API_NAME", str(preset["api_name"]))
    return preset_name


def _helios_inputs(scene: ScenePlan) -> list[Any]:
    frame_count = max(33, min(231, math.ceil(scene.duration_seconds * 24 / 33) * 33))
    return [
        "Text-to-Video",
        scene.visual_prompt,
        None,
        None,
        384,
        640,
        frame_count,
        2,
        scene.seed,
        True,
    ]


def _ltx_inputs(scene: ScenePlan, request: VideoRequest) -> list[Any]:
    return [
        scene.visual_prompt,
        request.negative_prompt,
        None,
        None,
        512,
        704,
        "text-to-video",
        min(float(scene.duration_seconds), 8.5),
        9,
        scene.seed,
        False,
        1.0,
        False,
    ]


def _wan_inputs(scene: ScenePlan) -> list[Any]:
    return [
        None,
        scene.visual_prompt,
        704,
        1280,
        min(float(scene.duration_seconds), 5.0),
        38,
        5.0,
        5.0,
        scene.seed,
    ]


def build_inputs(preset_name: str, scene: ScenePlan, request: VideoRequest) -> list[Any]:
    if preset_name == "helios_realtime":
        return _helios_inputs(scene)
    if preset_name == "ltx_fast":
        return _ltx_inputs(scene, request)
    if preset_name == "wan22_5b":
        return _wan_inputs(scene)
    raise RuntimeError(f"No input builder for preset {preset_name}")


def install_hf_preset_provider(remote_module: Any) -> None:
    """Replace the generic HF provider with a chunking preset-aware provider."""
    preset_name = apply_hf_preset_env()
    if not preset_name:
        return

    base_provider = remote_module.HuggingFaceSpaceProvider
    max_chunk = int(PRESETS[preset_name]["max_chunk_seconds"])

    class PresetHuggingFaceSpaceProvider(base_provider):
        async def _one(
            self,
            subscene: ScenePlan,
            request: VideoRequest,
            folder: Path,
        ) -> Path:
            self.inputs_template = build_inputs(preset_name, subscene, request)
            self.kwargs_template = {}
            return await super().generate(subscene, request, folder)

        async def generate(
            self,
            scene: ScenePlan,
            request: VideoRequest,
            workdir: Path,
        ) -> Path:
            remaining = scene.duration_seconds
            chunks: list[Path] = []
            part = 0
            while remaining > 0:
                chunk_seconds = min(max_chunk, remaining)
                chunk_folder = workdir / f"hf-{scene.index:05d}-{part:03d}"
                chunk_folder.mkdir(parents=True, exist_ok=True)
                subscene = ScenePlan(
                    index=scene.index * 1000 + part,
                    duration_seconds=chunk_seconds,
                    narration=scene.narration,
                    visual_prompt=scene.visual_prompt,
                    seed=scene.seed + part,
                )
                raw = await self._one(subscene, request, chunk_folder)
                trimmed = chunk_folder / f"trimmed-{part:03d}.mp4"
                await LocalProvider.trim(
                    raw,
                    trimmed,
                    chunk_seconds,
                    request.width,
                    request.height,
                    request.fps,
                )
                chunks.append(trimmed)
                remaining -= chunk_seconds
                part += 1

            destination = workdir / f"scene-{scene.index:05d}.mp4"
            if len(chunks) == 1:
                shutil.copy2(chunks[0], destination)
                return destination

            concat_file = workdir / f"hf-concat-{scene.index:05d}.txt"
            concat_file.write_text(
                "\n".join(f"file '{item.as_posix()}'" for item in chunks),
                encoding="utf-8",
            )
            await asyncio.to_thread(
                subprocess.run,
                [
                    "ffmpeg",
                    "-y",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(concat_file),
                    "-c",
                    "copy",
                    "-movflags",
                    "+faststart",
                    str(destination),
                ],
                check=True,
                capture_output=True,
            )
            return destination

    remote_module.HuggingFaceSpaceProvider = PresetHuggingFaceSpaceProvider
