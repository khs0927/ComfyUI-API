from __future__ import annotations

import asyncio
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field, model_validator


ProviderName = Literal["auto", "comfyui", "fal_ltx", "hf_space", "mock"]
JobState = Literal["queued", "planning", "generating", "rendering", "completed", "failed", "cancelled"]


class VideoRequest(BaseModel):
    prompt: str = Field(min_length=3, description="Overall creative direction")
    script: str | None = Field(default=None, description="Optional full narration/script")
    target_duration_minutes: float | None = Field(default=None, gt=0)
    scene_seconds: int = Field(default=8, ge=3, le=60)
    provider: ProviderName = "auto"
    aspect_ratio: Literal["16:9", "9:16", "1:1"] = "16:9"
    width: int = Field(default=1280, ge=256, le=4096)
    height: int = Field(default=720, ge=256, le=4096)
    fps: int = Field(default=24, ge=12, le=60)
    style: str = "cinematic, coherent characters, natural motion"
    negative_prompt: str = "watermark, subtitles, distorted anatomy, flicker, duplicate people"
    language: str = "ko"
    native_audio: bool = True
    seed: int | None = None

    @model_validator(mode="after")
    def normalize_dimensions(self) -> "VideoRequest":
        if self.aspect_ratio == "9:16" and self.width > self.height:
            self.width, self.height = self.height, self.width
        elif self.aspect_ratio == "16:9" and self.height > self.width:
            self.width, self.height = self.height, self.width
        elif self.aspect_ratio == "1:1":
            side = min(self.width, self.height)
            self.width = self.height = side
        return self


@dataclass
class ScenePlan:
    index: int
    duration_seconds: int
    narration: str
    visual_prompt: str
    seed: int
    clip_path: str | None = None
    error: str | None = None


@dataclass
class VideoJob:
    id: str
    state: JobState
    request: dict[str, Any]
    created_at: float
    updated_at: float
    progress: float = 0.0
    message: str = ""
    scenes: list[dict[str, Any]] = field(default_factory=list)
    output_path: str | None = None
    output_url: str | None = None
    error: str | None = None
    cancelled: bool = False


class JsonJobStore:
    """Small durable job store suitable for a single free CPU controller instance."""

    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "jobs.json"
        self._lock = asyncio.Lock()
        self._jobs: dict[str, VideoJob] = {}
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                self._jobs = {key: VideoJob(**value) for key, value in raw.items()}
            except Exception:
                self._jobs = {}

    async def _flush(self) -> None:
        payload = {key: asdict(value) for key, value in self._jobs.items()}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    async def put(self, job: VideoJob) -> None:
        async with self._lock:
            job.updated_at = time.time()
            self._jobs[job.id] = job
            await self._flush()

    async def get(self, job_id: str) -> VideoJob | None:
        async with self._lock:
            return self._jobs.get(job_id)

    async def list(self) -> list[VideoJob]:
        async with self._lock:
            return sorted(self._jobs.values(), key=lambda item: item.created_at, reverse=True)


class ScenePlanner:
    SENTENCE_BREAK = re.compile(r"(?<=[.!?。！？]|다\.|요\.)\s+|\n+")

    @staticmethod
    def estimate_minutes(text: str) -> float:
        # Korean narration generally lands around 260-330 characters/minute.
        return max(0.25, len(text.strip()) / 290)

    def plan(self, request: VideoRequest) -> list[ScenePlan]:
        source = (request.script or request.prompt).strip()
        target_minutes = request.target_duration_minutes or self.estimate_minutes(source)
        total_seconds = max(request.scene_seconds, int(round(target_minutes * 60)))
        scene_count = max(1, math.ceil(total_seconds / request.scene_seconds))

        fragments = [part.strip() for part in self.SENTENCE_BREAK.split(source) if part.strip()]
        if not fragments:
            fragments = [source]

        buckets = [""] * scene_count
        for index, fragment in enumerate(fragments):
            slot = min(scene_count - 1, int(index * scene_count / max(1, len(fragments))))
            buckets[slot] = (buckets[slot] + " " + fragment).strip()

        # Fill empty visual scenes by carrying the nearest narrative context forward.
        last = fragments[0]
        base_seed = request.seed if request.seed is not None else int(uuid.uuid4().int % 2_147_483_647)
        scenes: list[ScenePlan] = []
        for index, narration in enumerate(buckets):
            if narration:
                last = narration
            else:
                narration = last
            duration = request.scene_seconds
            if index == scene_count - 1:
                duration = max(3, total_seconds - request.scene_seconds * (scene_count - 1))
            visual_prompt = (
                f"{request.prompt}. Scene {index + 1} of {scene_count}. "
                f"Narrative content: {narration}. {request.style}. "
                "Maintain the same subjects, wardrobe, environment logic, color palette, and visual identity "
                "as adjacent scenes. Deliberate camera movement, realistic temporal continuity, no on-screen text."
            )
            scenes.append(
                ScenePlan(
                    index=index,
                    duration_seconds=duration,
                    narration=narration,
                    visual_prompt=visual_prompt,
                    seed=base_seed + index,
                )
            )
        return scenes


class ClipProvider:
    async def generate(self, scene: ScenePlan, request: VideoRequest, workdir: Path) -> Path:
        raise NotImplementedError


class MockProvider(ClipProvider):
    async def generate(self, scene: ScenePlan, request: VideoRequest, workdir: Path) -> Path:
        output = workdir / f"scene-{scene.index:05d}.mp4"
        color = f"hue=h={scene.index * 17 % 360}:s=0.35"
        text = re.sub(r"[':%]", "", scene.narration[:80])
        cmd = [
            "ffmpeg", "-y", "-f", "lavfi", "-i",
            f"color=c=0x172033:s={request.width}x{request.height}:r={request.fps}:d={scene.duration_seconds}",
            "-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo:d={scene.duration_seconds}",
            "-vf", f"{color},drawtext=text='{text}':fontcolor=white:fontsize=32:x=(w-text_w)/2:y=(h-text_h)/2",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(output),
        ]
        await asyncio.to_thread(subprocess.run, cmd, check=True, capture_output=True)
        return output


class FalLTXProvider(ClipProvider):
    def __init__(self) -> None:
        self.endpoint = os.getenv("FAL_VIDEO_ENDPOINT", "fal-ai/ltx-2.3/text-to-video")

    async def generate(self, scene: ScenePlan, request: VideoRequest, workdir: Path) -> Path:
        if not os.getenv("FAL_KEY"):
            raise RuntimeError("FAL_KEY is required for provider=fal_ltx")
        import fal_client

        arguments: dict[str, Any] = {
            "prompt": scene.visual_prompt,
            "negative_prompt": request.negative_prompt,
            "duration": scene.duration_seconds,
            "resolution": f"{request.width}x{request.height}",
            "seed": scene.seed,
            "generate_audio": request.native_audio,
        }

        def call() -> dict[str, Any]:
            return fal_client.submit(self.endpoint, arguments=arguments).get()

        result = await asyncio.to_thread(call)
        video = result.get("video") or result.get("videos")
        if isinstance(video, list):
            video = video[0]
        url = video.get("url") if isinstance(video, dict) else video
        if not url:
            raise RuntimeError(f"fal response did not contain a video URL: {result}")
        output = workdir / f"scene-{scene.index:05d}.mp4"
        await download_file(url, output)
        return output


class HuggingFaceSpaceProvider(ClipProvider):
    """Calls a Gradio Space. Intended for demos; public ZeroGPU quotas are finite."""

    def __init__(self) -> None:
        self.space_id = os.getenv("HF_VIDEO_SPACE_ID", "Lightricks/ltx-video-distilled")
        self.api_name = os.getenv("HF_VIDEO_SPACE_API_NAME", "/generate")

    async def generate(self, scene: ScenePlan, request: VideoRequest, workdir: Path) -> Path:
        from gradio_client import Client

        def call() -> Any:
            client = Client(self.space_id, token=os.getenv("HF_TOKEN") or None)
            return client.predict(
                prompt=scene.visual_prompt,
                negative_prompt=request.negative_prompt,
                duration=scene.duration_seconds,
                seed=scene.seed,
                api_name=self.api_name,
            )

        result = await asyncio.to_thread(call)
        candidate = result[0] if isinstance(result, (tuple, list)) else result
        if isinstance(candidate, dict):
            candidate = candidate.get("video") or candidate.get("path") or candidate.get("url")
        if not candidate:
            raise RuntimeError(f"Space returned no video: {result}")
        output = workdir / f"scene-{scene.index:05d}.mp4"
        if str(candidate).startswith(("http://", "https://")):
            await download_file(str(candidate), output)
        else:
            shutil.copy2(str(candidate), output)
        return output


class ComfyUIProvider(ClipProvider):
    """Runs an LTX workflow through the standard ComfyUI /prompt API."""

    def __init__(self) -> None:
        self.base_url = os.getenv("COMFYUI_URL", "http://127.0.0.1:8188").rstrip("/")
        self.workflow_path = Path(os.getenv("COMFYUI_VIDEO_WORKFLOW", "workflows/ltx23_video_api.json"))
        self.timeout = float(os.getenv("COMFYUI_SCENE_TIMEOUT", "1800"))

    @staticmethod
    def replace_tokens(value: Any, tokens: dict[str, Any]) -> Any:
        if isinstance(value, dict):
            return {key: ComfyUIProvider.replace_tokens(item, tokens) for key, item in value.items()}
        if isinstance(value, list):
            return [ComfyUIProvider.replace_tokens(item, tokens) for item in value]
        if isinstance(value, str):
            if value in tokens:
                return tokens[value]
            for token, replacement in tokens.items():
                value = value.replace(token, str(replacement))
        return value

    async def generate(self, scene: ScenePlan, request: VideoRequest, workdir: Path) -> Path:
        if not self.workflow_path.exists():
            raise RuntimeError(
                f"ComfyUI workflow not found: {self.workflow_path}. Export an API-format workflow and use the documented tokens."
            )
        workflow = json.loads(self.workflow_path.read_text(encoding="utf-8"))
        tokens = {
            "{{PROMPT}}": scene.visual_prompt,
            "{{NEGATIVE_PROMPT}}": request.negative_prompt,
            "{{WIDTH}}": request.width,
            "{{HEIGHT}}": request.height,
            "{{FPS}}": request.fps,
            "{{DURATION_SECONDS}}": scene.duration_seconds,
            "{{FRAME_COUNT}}": scene.duration_seconds * request.fps + 1,
            "{{SEED}}": scene.seed,
        }
        workflow = self.replace_tokens(workflow, tokens)
        client_id = str(uuid.uuid4())
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(f"{self.base_url}/prompt", json={"prompt": workflow, "client_id": client_id})
            response.raise_for_status()
            prompt_id = response.json()["prompt_id"]

            deadline = time.monotonic() + self.timeout
            while time.monotonic() < deadline:
                history_response = await client.get(f"{self.base_url}/history/{prompt_id}")
                history_response.raise_for_status()
                history = history_response.json().get(prompt_id)
                if history:
                    for node in history.get("outputs", {}).values():
                        files = node.get("videos") or node.get("gifs") or node.get("images") or []
                        for item in files:
                            filename = item.get("filename", "")
                            if filename.lower().endswith((".mp4", ".webm", ".mov", ".gif")):
                                params = {
                                    "filename": filename,
                                    "subfolder": item.get("subfolder", ""),
                                    "type": item.get("type", "output"),
                                }
                                media = await client.get(f"{self.base_url}/view", params=params)
                                media.raise_for_status()
                                output = workdir / f"scene-{scene.index:05d}{Path(filename).suffix or '.mp4'}"
                                output.write_bytes(media.content)
                                return output
                    status = history.get("status", {})
                    if status.get("status_str") == "error":
                        raise RuntimeError(f"ComfyUI generation failed: {status}")
                await asyncio.sleep(2)
        raise TimeoutError(f"ComfyUI scene timed out after {self.timeout} seconds")


async def download_file(url: str, destination: Path) -> None:
    async with httpx.AsyncClient(timeout=None, follow_redirects=True) as client:
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            with destination.open("wb") as handle:
                async for chunk in response.aiter_bytes():
                    handle.write(chunk)


class FFmpegRenderer:
    def __init__(self, output_root: Path):
        self.output_root = output_root
        self.output_root.mkdir(parents=True, exist_ok=True)
        if not shutil.which("ffmpeg"):
            raise RuntimeError("ffmpeg must be installed")

    async def render(self, job_id: str, clips: list[Path], request: VideoRequest, workdir: Path) -> Path:
        normalized: list[Path] = []
        for index, clip in enumerate(clips):
            target = workdir / f"normalized-{index:05d}.mp4"
            vf = (
                f"scale={request.width}:{request.height}:force_original_aspect_ratio=decrease,"
                f"pad={request.width}:{request.height}:(ow-iw)/2:(oh-ih)/2,"
                f"fps={request.fps},format=yuv420p"
            )
            cmd = [
                "ffmpeg", "-y", "-i", str(clip), "-vf", vf,
                "-c:v", "libx264", "-preset", os.getenv("FFMPEG_PRESET", "veryfast"),
                "-crf", os.getenv("FFMPEG_CRF", "20"), "-c:a", "aac", "-ar", "48000",
                "-ac", "2", "-movflags", "+faststart", str(target),
            ]
            try:
                await asyncio.to_thread(subprocess.run, cmd, check=True, capture_output=True)
            except subprocess.CalledProcessError:
                # Some generated clips contain no audio stream. Add silence and retry.
                fallback = [
                    "ffmpeg", "-y", "-i", str(clip), "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
                    "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                    "-c:a", "aac", "-shortest", "-movflags", "+faststart", str(target),
                ]
                await asyncio.to_thread(subprocess.run, fallback, check=True, capture_output=True)
            normalized.append(target)

        concat_file = workdir / "concat.txt"
        concat_file.write_text("\n".join(f"file '{path.as_posix()}'" for path in normalized), encoding="utf-8")
        output = self.output_root / f"{job_id}.mp4"
        copy_cmd = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file),
            "-c", "copy", "-movflags", "+faststart", str(output),
        ]
        await asyncio.to_thread(subprocess.run, copy_cmd, check=True, capture_output=True)
        return output


class LongVideoOrchestrator:
    def __init__(self, data_root: str | Path | None = None):
        self.data_root = Path(data_root or os.getenv("VIDEO_DATA_ROOT", "./video-data")).resolve()
        self.work_root = self.data_root / "work"
        self.output_root = self.data_root / "outputs"
        self.work_root.mkdir(parents=True, exist_ok=True)
        self.store = JsonJobStore(self.data_root)
        self.planner = ScenePlanner()
        self.renderer = FFmpegRenderer(self.output_root)
        self.tasks: dict[str, asyncio.Task[Any]] = {}
        self.scene_parallelism = max(1, int(os.getenv("VIDEO_SCENE_PARALLELISM", "1")))

    def provider_for(self, name: ProviderName) -> ClipProvider:
        if name == "auto":
            if os.getenv("COMFYUI_URL") and os.getenv("COMFYUI_VIDEO_WORKFLOW"):
                return ComfyUIProvider()
            if os.getenv("FAL_KEY"):
                return FalLTXProvider()
            if os.getenv("HF_VIDEO_SPACE_ID"):
                return HuggingFaceSpaceProvider()
            return MockProvider()
        return {
            "comfyui": ComfyUIProvider,
            "fal_ltx": FalLTXProvider,
            "hf_space": HuggingFaceSpaceProvider,
            "mock": MockProvider,
        }[name]()

    async def create(self, request: VideoRequest) -> VideoJob:
        now = time.time()
        job = VideoJob(
            id=str(uuid.uuid4()), state="queued", request=request.model_dump(), created_at=now, updated_at=now,
            message="Queued for scene planning",
        )
        await self.store.put(job)
        self.tasks[job.id] = asyncio.create_task(self._run(job.id))
        return job

    async def get(self, job_id: str) -> VideoJob | None:
        return await self.store.get(job_id)

    async def cancel(self, job_id: str) -> VideoJob | None:
        job = await self.store.get(job_id)
        if not job:
            return None
        job.cancelled = True
        job.state = "cancelled"
        job.message = "Cancellation requested"
        task = self.tasks.get(job_id)
        if task:
            task.cancel()
        await self.store.put(job)
        return job

    async def _run(self, job_id: str) -> None:
        job = await self.store.get(job_id)
        if not job:
            return
        request = VideoRequest.model_validate(job.request)
        workdir = self.work_root / job_id
        workdir.mkdir(parents=True, exist_ok=True)
        try:
            job.state = "planning"
            job.message = "Building an unlimited-length scene plan"
            await self.store.put(job)
            scenes = self.planner.plan(request)
            job.scenes = [asdict(scene) for scene in scenes]
            await self.store.put(job)

            provider = self.provider_for(request.provider)
            semaphore = asyncio.Semaphore(self.scene_parallelism)
            completed = 0
            clips: list[Path | None] = [None] * len(scenes)

            async def generate_one(scene: ScenePlan) -> None:
                nonlocal completed
                async with semaphore:
                    current = await self.store.get(job_id)
                    if not current or current.cancelled:
                        raise asyncio.CancelledError
                    clip = await provider.generate(scene, request, workdir)
                    scene.clip_path = str(clip)
                    clips[scene.index] = clip
                    completed += 1
                    current.state = "generating"
                    current.progress = completed / max(1, len(scenes)) * 0.88
                    current.message = f"Generated scene {completed}/{len(scenes)}"
                    current.scenes[scene.index] = asdict(scene)
                    await self.store.put(current)

            await asyncio.gather(*(generate_one(scene) for scene in scenes))
            valid_clips = [clip for clip in clips if clip is not None]
            if len(valid_clips) != len(scenes):
                raise RuntimeError("One or more scenes did not produce a clip")

            job = await self.store.get(job_id) or job
            job.state = "rendering"
            job.progress = 0.92
            job.message = "Normalizing and concatenating all scenes"
            await self.store.put(job)
            output = await self.renderer.render(job_id, valid_clips, request, workdir)
            job.state = "completed"
            job.progress = 1.0
            job.message = "Video completed"
            job.output_path = str(output)
            job.output_url = f"/outputs/{output.name}"
            await self.store.put(job)
        except asyncio.CancelledError:
            job = await self.store.get(job_id) or job
            job.state = "cancelled"
            job.message = "Video job cancelled"
            await self.store.put(job)
        except Exception as exc:
            job = await self.store.get(job_id) or job
            job.state = "failed"
            job.error = str(exc)
            job.message = "Video generation failed"
            await self.store.put(job)
