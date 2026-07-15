from __future__ import annotations

import os
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from video_engine import LongVideoOrchestrator, VideoRequest

app = FastAPI(
    title="HS Unlimited AI Video API",
    version="0.2.0",
    description=(
        "Scene-based long-form AI video orchestration for Beam Helios, Kaggle, "
        "Hugging Face Spaces, and local open-source workers. The service has no "
        "fixed final-duration cap; free quotas and available compute still apply."
    ),
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[item.strip() for item in os.getenv("CORS_ORIGINS", "*").split(",")],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

engine = LongVideoOrchestrator()
engine.output_root.mkdir(parents=True, exist_ok=True)
app.mount("/outputs", StaticFiles(directory=str(engine.output_root)), name="outputs")


def authorize(authorization: str | None) -> None:
    expected = os.getenv("VIDEO_API_TOKEN")
    if not expected:
        return
    supplied = (authorization or "").removeprefix("Bearer ").strip()
    if supplied != expected:
        raise HTTPException(status_code=401, detail="Invalid bearer token")


def provider_status() -> dict[str, object]:
    order = [
        item.strip()
        for item in os.getenv(
            "VIDEO_PROVIDER_ORDER", "beam,kaggle,hf,helios,wan21,comfyui"
        ).split(",")
        if item.strip()
    ]
    configured = {
        "beam": bool(os.getenv("BEAM_TASK_QUEUE_URL") and os.getenv("BEAM_TOKEN")),
        "kaggle": bool(os.getenv("KAGGLE_KERNEL_ID")),
        "hf": bool(os.getenv("HF_VIDEO_SPACE_ID")),
        "helios": bool(os.getenv("HELIOS_ROOT")),
        "wan21": bool(os.getenv("WAN21_ROOT")),
        "comfyui": bool(
            os.getenv("COMFYUI_URL") and os.getenv("COMFYUI_VIDEO_WORKFLOW")
        ),
        "modal": os.getenv("MODAL_ENABLED", "false").lower() == "true",
    }
    return {
        "order": order,
        "configured": configured,
        "active_order": [name for name in order if configured.get(name, False)],
        "modal_reserved": True,
        "daily_video_second_guards": {
            "beam": int(os.getenv("BEAM_DAILY_VIDEO_SECONDS", "1200")),
            "kaggle": int(os.getenv("KAGGLE_DAILY_VIDEO_SECONDS", "600")),
            "hf": int(os.getenv("HF_DAILY_VIDEO_SECONDS", "120")),
        },
    }


@app.get("/health")
async def health() -> dict[str, object]:
    return {
        "ok": True,
        "service": "hs-unlimited-ai-video",
        "providers": provider_status(),
    }


@app.get("/v1/providers")
async def get_providers(
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    authorize(authorization)
    return provider_status()


@app.post("/v1/videos", status_code=202)
async def create_video(
    request: VideoRequest,
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    authorize(authorization)
    job = await engine.create(request)
    return asdict(job)


@app.get("/v1/videos")
async def list_videos(
    authorization: str | None = Header(default=None),
) -> list[dict[str, object]]:
    authorize(authorization)
    return [asdict(job) for job in await engine.store.list()]


@app.get("/v1/videos/{job_id}")
async def get_video(
    job_id: str,
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    authorize(authorization)
    job = await engine.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Video job not found")
    return asdict(job)


@app.post("/v1/videos/{job_id}/cancel")
async def cancel_video(
    job_id: str,
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    authorize(authorization)
    job = await engine.cancel(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Video job not found")
    return asdict(job)


@app.get("/v1/videos/{job_id}/download")
async def download_video(
    job_id: str,
    authorization: str | None = Header(default=None),
) -> FileResponse:
    authorize(authorization)
    job = await engine.get(job_id)
    if not job or job.state != "completed" or not job.output_path:
        raise HTTPException(status_code=409, detail="Video is not completed")
    path = Path(job.output_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Rendered file is missing")
    return FileResponse(path, media_type="video/mp4", filename=path.name)
