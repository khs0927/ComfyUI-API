from __future__ import annotations

import os
from dataclasses import asdict

from mcp.server.fastmcp import FastMCP

from video_engine import LongVideoOrchestrator, VideoRequest

mcp = FastMCP("HS Unlimited AI Video")
engine = LongVideoOrchestrator()


@mcp.tool()
async def create_long_video(
    prompt: str,
    script: str = "",
    target_duration_minutes: float | None = None,
    scene_seconds: int = 8,
    provider: str = "auto",
    aspect_ratio: str = "16:9",
    width: int = 1280,
    height: int = 720,
    fps: int = 24,
    style: str = "cinematic, coherent characters, natural motion",
    native_audio: bool = True,
) -> dict:
    """Start a scene-based AI video job. Final duration has no fixed application limit."""
    request = VideoRequest(
        prompt=prompt,
        script=script or None,
        target_duration_minutes=target_duration_minutes,
        scene_seconds=scene_seconds,
        provider=provider,
        aspect_ratio=aspect_ratio,
        width=width,
        height=height,
        fps=fps,
        style=style,
        native_audio=native_audio,
    )
    job = await engine.create(request)
    return {
        "job_id": job.id,
        "state": job.state,
        "message": job.message,
        "status_tool": "get_video_status",
    }


@mcp.tool()
async def get_video_status(job_id: str) -> dict:
    """Read scene-level progress and return the final download URL when complete."""
    job = await engine.get(job_id)
    if not job:
        return {"found": False, "job_id": job_id}
    result = asdict(job)
    public_base = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
    if result.get("output_url") and public_base:
        result["output_url"] = public_base + result["output_url"]
        result["download_url"] = f"{public_base}/v1/videos/{job_id}/download"
    result["found"] = True
    return result


@mcp.tool()
async def cancel_video(job_id: str) -> dict:
    """Cancel a queued or running long-form video job."""
    job = await engine.cancel(job_id)
    return {"cancelled": bool(job), "job_id": job_id, "state": job.state if job else None}


@mcp.tool()
async def estimate_video_plan(
    prompt: str,
    script: str = "",
    target_duration_minutes: float | None = None,
    scene_seconds: int = 8,
) -> dict:
    """Estimate scene count and generation workload without starting a paid or GPU job."""
    request = VideoRequest(
        prompt=prompt,
        script=script or None,
        target_duration_minutes=target_duration_minutes,
        scene_seconds=scene_seconds,
        provider="mock",
    )
    scenes = engine.planner.plan(request)
    return {
        "scene_count": len(scenes),
        "estimated_duration_seconds": sum(scene.duration_seconds for scene in scenes),
        "scene_seconds": scene_seconds,
        "note": "Generation time and provider cost depend on the selected backend.",
    }


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
