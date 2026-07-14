from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path
from typing import Any

from .core import FalLTXProvider, ScenePlan, VideoRequest, download_file


def _chunk_durations(total: int) -> list[tuple[int, int]]:
    """Return (provider_duration, trim_duration) pairs for LTX's 6/8/10 second API."""
    chunks: list[tuple[int, int]] = []
    remaining = total
    while remaining > 0:
        if remaining <= 6:
            chunks.append((6, remaining))
            break
        if remaining <= 8:
            chunks.append((8, remaining))
            break
        if remaining <= 10:
            chunks.append((10, remaining))
            break
        chunks.append((10, 10))
        remaining -= 10
    return chunks


def _resolution(width: int, height: int) -> str:
    long_edge = max(width, height)
    if long_edge >= 3000:
        return "2160p"
    if long_edge >= 1800:
        return "1440p"
    return "1080p"


def _fps(value: int) -> int:
    return min((24, 25, 48, 50), key=lambda allowed: abs(allowed - value))


async def _generate(self: FalLTXProvider, scene: ScenePlan, request: VideoRequest, workdir: Path) -> Path:
    if not os.getenv("FAL_KEY"):
        raise RuntimeError("FAL_KEY is required for provider=fal_ltx")
    import fal_client

    pieces: list[Path] = []
    chunks = _chunk_durations(scene.duration_seconds)
    for part_index, (provider_duration, trim_duration) in enumerate(chunks):
        prompt = scene.visual_prompt
        if len(chunks) > 1:
            prompt += (
                f" Continuous shot segment {part_index + 1} of {len(chunks)}. "
                "Preserve subjects, wardrobe, camera direction, lighting, and motion continuity."
            )
        arguments: dict[str, Any] = {
            "prompt": prompt,
            "duration": provider_duration,
            "fps": _fps(request.fps),
            "aspect_ratio": request.aspect_ratio if request.aspect_ratio != "1:1" else "16:9",
            "resolution": _resolution(request.width, request.height),
            "generate_audio": request.native_audio,
        }

        def call() -> dict[str, Any]:
            return fal_client.submit(self.endpoint, arguments=arguments).get()

        result = await asyncio.to_thread(call)
        video = result.get("video")
        url = video.get("url") if isinstance(video, dict) else video
        if not url:
            raise RuntimeError(f"fal response did not contain a video URL: {result}")
        raw = workdir / f"scene-{scene.index:05d}-part-{part_index:03d}-raw.mp4"
        trimmed = workdir / f"scene-{scene.index:05d}-part-{part_index:03d}.mp4"
        await download_file(url, raw)
        trim_cmd = [
            "ffmpeg", "-y", "-i", str(raw), "-t", str(trim_duration),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-movflags", "+faststart", str(trimmed),
        ]
        await asyncio.to_thread(subprocess.run, trim_cmd, check=True, capture_output=True)
        pieces.append(trimmed)

    if len(pieces) == 1:
        return pieces[0]
    concat = workdir / f"scene-{scene.index:05d}-fal-concat.txt"
    concat.write_text("\n".join(f"file '{path.as_posix()}'" for path in pieces), encoding="utf-8")
    output = workdir / f"scene-{scene.index:05d}.mp4"
    cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat),
        "-c", "copy", "-movflags", "+faststart", str(output),
    ]
    await asyncio.to_thread(subprocess.run, cmd, check=True, capture_output=True)
    return output


def install() -> None:
    FalLTXProvider.generate = _generate  # type: ignore[method-assign]
