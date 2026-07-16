from __future__ import annotations

import os
from dataclasses import asdict

from mcp.server.fastmcp import FastMCP

from video_engine import LongVideoOrchestrator, VideoRequest

mcp = FastMCP("HS Unlimited AI Video")
engine = LongVideoOrchestrator()


def _provider_status() -> dict:
    order = [
        item.strip()
        for item in os.getenv(
            "VIDEO_PROVIDER_ORDER", "beam,kaggle,hf,helios,wan21"
        ).split(",")
        if item.strip()
    ]
    configured = {
        "beam": bool(os.getenv("BEAM_TASK_QUEUE_URL") and os.getenv("BEAM_TOKEN")),
        "kaggle": bool(os.getenv("KAGGLE_KERNEL_ID")),
        "hf": bool(os.getenv("HF_VIDEO_SPACE_ID")),
        "helios": bool(os.getenv("HELIOS_ROOT")),
        "wan21": bool(os.getenv("WAN21_ROOT")),
        "modal": os.getenv("MODAL_ENABLED", "false").lower() == "true",
    }
    return {
        "order": order,
        "configured": configured,
        "active_order": [name for name in order if configured.get(name, False)],
        "modal_reserved": True,
    }


@mcp.tool()
async def create_long_video(
    prompt: str,
    script: str = "",
    target_duration_minutes: float | None = None,
    scene_seconds: int = 30,
    provider: str = "auto",
    aspect_ratio: str = "16:9",
    width: int = 960,
    height: int = 544,
    fps: int = 24,
    style: str = "cinematic, coherent characters, natural motion",
) -> dict:
    """Start a scene-based AI video job using the configured free provider chain."""
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
    )
    job = await engine.create(request)
    return {
        "job_id": job.id,
        "state": job.state,
        "message": job.message,
        "status_tool": "get_video_status",
        "providers": _provider_status(),
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
    return {
        "cancelled": bool(job),
        "job_id": job_id,
        "state": job.state if job else None,
    }


@mcp.tool()
async def estimate_video_plan(
    prompt: str,
    script: str = "",
    target_duration_minutes: float | None = None,
    scene_seconds: int = 30,
) -> dict:
    """Estimate scene count and workload without starting a GPU job."""
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
        "provider_order": _provider_status()["active_order"],
        "note": "Generation time depends on free quota, queueing, and available GPU hardware.",
    }


@mcp.tool()
async def get_video_provider_status() -> dict:
    """Show which Beam, Kaggle, Hugging Face, and local backends are configured."""
    return _provider_status()


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
