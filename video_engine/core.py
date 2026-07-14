from __future__ import annotations

import asyncio
import json
import math
import os
import re
import shutil
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field, model_validator

ProviderName = Literal["auto", "helios", "wan21", "comfyui", "mock"]
JobState = Literal[
    "queued",
    "planning",
    "generating",
    "rendering",
    "completed",
    "failed",
    "cancelled",
]


class VideoRequest(BaseModel):
    prompt: str = Field(min_length=3)
    script: str | None = None
    target_duration_minutes: float | None = Field(default=None, gt=0)
    scene_seconds: int = Field(default=30, ge=3, le=90)
    provider: ProviderName = "auto"
    aspect_ratio: Literal["16:9", "9:16", "1:1"] = "16:9"
    width: int = Field(default=960, ge=256, le=4096)
    height: int = Field(default=544, ge=256, le=4096)
    fps: int = Field(default=24, ge=12, le=60)
    style: str = "cinematic, coherent characters, natural motion"
    negative_prompt: str = (
        "watermark, subtitles, distorted anatomy, flicker, duplicate people"
    )
    language: str = "ko"
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
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "jobs.json"
        self._lock = asyncio.Lock()
        self._jobs: dict[str, VideoJob] = {}
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                self._jobs = {
                    job_id: VideoJob(**payload) for job_id, payload in raw.items()
                }
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                self._jobs = {}

    async def _flush(self) -> None:
        payload = {key: asdict(value) for key, value in self._jobs.items()}
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(self.path)

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
            return sorted(
                self._jobs.values(), key=lambda item: item.created_at, reverse=True
            )


class ScenePlanner:
    SENTENCE_BREAK = re.compile(r"(?<=[.!?。！？])\s+|\n+")

    @staticmethod
    def estimate_minutes(text: str) -> float:
        return max(0.25, len(text.strip()) / 290)

    def plan(self, request: VideoRequest) -> list[ScenePlan]:
        source = (request.script or request.prompt).strip()
        minutes = request.target_duration_minutes or self.estimate_minutes(source)
        total_seconds = max(request.scene_seconds, int(round(minutes * 60)))
        scene_count = max(1, math.ceil(total_seconds / request.scene_seconds))
        fragments = [
            part.strip()
            for part in self.SENTENCE_BREAK.split(source)
            if part.strip()
        ] or [source]

        buckets = [""] * scene_count
        for index, fragment in enumerate(fragments):
            slot = min(
                scene_count - 1,
                int(index * scene_count / max(1, len(fragments))),
            )
            buckets[slot] = f"{buckets[slot]} {fragment}".strip()

        base_seed = request.seed
        if base_seed is None:
            base_seed = int(uuid.uuid4().int % 2_147_483_647)

        previous = fragments[0]
        scenes: list[ScenePlan] = []
        for index, narration in enumerate(buckets):
            if narration:
                previous = narration
            else:
                narration = previous
            duration = request.scene_seconds
            if index == scene_count - 1:
                duration = max(
                    3, total_seconds - request.scene_seconds * (scene_count - 1)
                )
            visual_prompt = (
                f"{request.prompt}. Scene {index + 1} of {scene_count}. "
                f"Narrative content: {narration}. {request.style}. "
                "Keep the same subject identity, wardrobe, environment logic, "
                "lighting palette and camera language across adjacent scenes. "
                "Natural temporal motion and no on-screen text."
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
    async def generate(
        self, scene: ScenePlan, request: VideoRequest, workdir: Path
    ) -> Path:
        raise NotImplementedError


class LocalProvider(ClipProvider):
    @staticmethod
    async def run(command: list[str], cwd: Path) -> None:
        try:
            await asyncio.to_thread(
                subprocess.run,
                command,
                cwd=str(cwd),
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as error:
            detail = (error.stderr or error.stdout or str(error))[-4000:]
            raise RuntimeError(detail) from error

    @staticmethod
    def newest_video(folder: Path) -> Path:
        candidates = [
            path
            for suffix in ("*.mp4", "*.webm", "*.mov", "*.mkv")
            for path in folder.rglob(suffix)
        ]
        if not candidates:
            raise RuntimeError(f"No generated video found in {folder}")
        return max(candidates, key=lambda path: path.stat().st_mtime)

    @staticmethod
    async def trim(
        source: Path,
        destination: Path,
        duration: int,
        width: int,
        height: int,
        fps: int,
    ) -> Path:
        command = [
            "ffmpeg",
            "-y",
            "-i",
            str(source),
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
            os.getenv("FFMPEG_PRESET", "veryfast"),
            "-crf",
            os.getenv("FFMPEG_CRF", "20"),
            "-an",
            "-movflags",
            "+faststart",
            str(destination),
        ]
        await asyncio.to_thread(
            subprocess.run, command, check=True, capture_output=True
        )
        return destination


class HeliosProvider(LocalProvider):
    """Apache-2.0 minute-scale local generator based on Helios-Distilled."""

    def __init__(self) -> None:
        self.root = Path(os.getenv("HELIOS_ROOT", "vendor/Helios")).resolve()
        self.script = Path(
            os.getenv(
                "HELIOS_INFER_SCRIPT",
                str(self.root / "scripts/inference/infer_helios.py"),
            )
        ).resolve()
        self.model = os.getenv("HELIOS_MODEL", "BestWishYsh/Helios-Distilled")
        self.python = os.getenv("HELIOS_PYTHON", "python")

    async def generate(
        self, scene: ScenePlan, request: VideoRequest, workdir: Path
    ) -> Path:
        if not self.script.exists():
            raise RuntimeError(
                f"Helios inference script not found: {self.script}. "
                "Run scripts/bootstrap_free_video_worker.sh helios first."
            )
        output_dir = workdir / f"helios-{scene.index:05d}"
        output_dir.mkdir(parents=True, exist_ok=True)
        frame_count = max(99, math.ceil(scene.duration_seconds * request.fps / 33) * 33)
        command = [
            self.python,
            str(self.script),
            "--base_model_path",
            self.model,
            "--transformer_path",
            self.model,
            "--sample_type",
            "t2v",
            "--prompt",
            scene.visual_prompt,
            "--num_frames",
            str(frame_count),
            "--fps",
            str(request.fps),
            "--guidance_scale",
            "1.0",
            "--is_enable_stage2",
            "--pyramid_num_inference_steps_list",
            "2",
            "2",
            "2",
            "--is_amplify_first_chunk",
            "--output_folder",
            str(output_dir),
            "--enable_low_vram_mode",
            "--group_offloading_type",
            "leaf_level",
        ]
        await self.run(command, self.script.parent)
        raw = self.newest_video(output_dir)
        destination = workdir / f"scene-{scene.index:05d}.mp4"
        return await self.trim(
            raw,
            destination,
            scene.duration_seconds,
            request.width,
            request.height,
            request.fps,
        )


class Wan21Provider(LocalProvider):
    """Apache-2.0 low-VRAM fallback using Wan2.1-T2V-1.3B."""

    def __init__(self) -> None:
        self.root = Path(os.getenv("WAN21_ROOT", "vendor/Wan2.1")).resolve()
        self.script = self.root / "generate.py"
        self.model = Path(
            os.getenv("WAN21_MODEL", "models/Wan2.1-T2V-1.3B")
        ).resolve()
        self.python = os.getenv("WAN21_PYTHON", "python")
        self.native_fps = int(os.getenv("WAN21_NATIVE_FPS", "16"))

    async def generate(
        self, scene: ScenePlan, request: VideoRequest, workdir: Path
    ) -> Path:
        if not self.script.exists():
            raise RuntimeError(
                f"Wan2.1 generate.py not found: {self.script}. "
                "Run scripts/bootstrap_free_video_worker.sh wan21 first."
            )
        if not self.model.exists():
            raise RuntimeError(f"Wan2.1 model directory not found: {self.model}")
        wanted = max(81, scene.duration_seconds * self.native_fps)
        frame_count = math.ceil((wanted - 1) / 4) * 4 + 1
        raw = workdir / f"wan-raw-{scene.index:05d}.mp4"
        command = [
            self.python,
            str(self.script),
            "--task",
            "t2v-1.3B",
            "--size",
            "832*480",
            "--frame_num",
            str(frame_count),
            "--ckpt_dir",
            str(self.model),
            "--offload_model",
            "True",
            "--t5_cpu",
            "--sample_shift",
            "8",
            "--sample_guide_scale",
            "6",
            "--base_seed",
            str(scene.seed),
            "--save_file",
            str(raw),
            "--prompt",
            scene.visual_prompt,
        ]
        await self.run(command, self.root)
        if not raw.exists():
            raw = self.newest_video(self.root)
        destination = workdir / f"scene-{scene.index:05d}.mp4"
        return await self.trim(
            raw,
            destination,
            scene.duration_seconds,
            request.width,
            request.height,
            request.fps,
        )


class MockProvider(LocalProvider):
    async def generate(
        self, scene: ScenePlan, request: VideoRequest, workdir: Path
    ) -> Path:
        output = workdir / f"scene-{scene.index:05d}.mp4"
        command = [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            (
                f"color=c=0x172033:s={request.width}x{request.height}:"
                f"r={request.fps}:d={scene.duration_seconds}"
            ),
            "-f",
            "lavfi",
            "-i",
            f"anullsrc=r=48000:cl=stereo:d={scene.duration_seconds}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(output),
        ]
        await asyncio.to_thread(
            subprocess.run, command, check=True, capture_output=True
        )
        return output


class ComfyUIProvider(ClipProvider):
    """Run a fully local open-source Wan/Helios workflow through ComfyUI."""

    def __init__(self) -> None:
        self.base_url = os.getenv(
            "COMFYUI_URL", "http://127.0.0.1:8188"
        ).rstrip("/")
        self.workflow_path = Path(
            os.getenv(
                "COMFYUI_VIDEO_WORKFLOW", "workflows/wan21_t2v_api.json"
            )
        )
        self.timeout = float(os.getenv("COMFYUI_SCENE_TIMEOUT", "7200"))

    @staticmethod
    def replace_tokens(value: Any, tokens: dict[str, Any]) -> Any:
        if isinstance(value, dict):
            return {
                key: ComfyUIProvider.replace_tokens(item, tokens)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [ComfyUIProvider.replace_tokens(item, tokens) for item in value]
        if isinstance(value, str):
            if value in tokens:
                return tokens[value]
            for token, replacement in tokens.items():
                value = value.replace(token, str(replacement))
        return value

    async def generate(
        self, scene: ScenePlan, request: VideoRequest, workdir: Path
    ) -> Path:
        if not self.workflow_path.exists():
            raise RuntimeError(f"ComfyUI workflow not found: {self.workflow_path}")
        workflow = json.loads(self.workflow_path.read_text(encoding="utf-8"))
        workflow = self.replace_tokens(
            workflow,
            {
                "{{PROMPT}}": scene.visual_prompt,
                "{{NEGATIVE_PROMPT}}": request.negative_prompt,
                "{{WIDTH}}": request.width,
                "{{HEIGHT}}": request.height,
                "{{FPS}}": request.fps,
                "{{DURATION_SECONDS}}": scene.duration_seconds,
                "{{FRAME_COUNT}}": scene.duration_seconds * request.fps + 1,
                "{{SEED}}": scene.seed,
            },
        )
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f"{self.base_url}/prompt",
                json={"prompt": workflow, "client_id": str(uuid.uuid4())},
            )
            response.raise_for_status()
            prompt_id = response.json()["prompt_id"]
            deadline = time.monotonic() + self.timeout
            while time.monotonic() < deadline:
                history_response = await client.get(
                    f"{self.base_url}/history/{prompt_id}"
                )
                history_response.raise_for_status()
                history = history_response.json().get(prompt_id)
                if history:
                    output = await self._read_output(client, history, scene, workdir)
                    if output:
                        return output
                    status = history.get("status", {})
                    if status.get("status_str") == "error":
                        raise RuntimeError(f"ComfyUI generation failed: {status}")
                await asyncio.sleep(2)
        raise TimeoutError(f"ComfyUI scene timed out after {self.timeout}s")

    async def _read_output(
        self,
        client: httpx.AsyncClient,
        history: dict[str, Any],
        scene: ScenePlan,
        workdir: Path,
    ) -> Path | None:
        for node in history.get("outputs", {}).values():
            files = (
                node.get("videos")
                or node.get("gifs")
                or node.get("images")
                or []
            )
            for item in files:
                filename = item.get("filename", "")
                if not filename.lower().endswith((".mp4", ".webm", ".mov", ".gif")):
                    continue
                media = await client.get(
                    f"{self.base_url}/view",
                    params={
                        "filename": filename,
                        "subfolder": item.get("subfolder", ""),
                        "type": item.get("type", "output"),
                    },
                )
                media.raise_for_status()
                suffix = Path(filename).suffix or ".mp4"
                output = workdir / f"scene-{scene.index:05d}{suffix}"
                output.write_bytes(media.content)
                return output
        return None


class FFmpegRenderer:
    def __init__(self, output_root: Path):
        self.output_root = output_root
        self.output_root.mkdir(parents=True, exist_ok=True)
        if not shutil.which("ffmpeg"):
            raise RuntimeError("ffmpeg must be installed")

    async def render(
        self,
        job_id: str,
        clips: list[Path],
        request: VideoRequest,
        workdir: Path,
    ) -> Path:
        normalized: list[Path] = []
        for index, clip in enumerate(clips):
            target = workdir / f"normalized-{index:05d}.mp4"
            video_filter = (
                f"scale={request.width}:{request.height}:"
                "force_original_aspect_ratio=decrease,"
                f"pad={request.width}:{request.height}:(ow-iw)/2:(oh-ih)/2,"
                f"fps={request.fps},format=yuv420p"
            )
            command = [
                "ffmpeg",
                "-y",
                "-i",
                str(clip),
                "-f",
                "lavfi",
                "-i",
                "anullsrc=r=48000:cl=stereo",
                "-vf",
                video_filter,
                "-c:v",
                "libx264",
                "-preset",
                os.getenv("FFMPEG_PRESET", "veryfast"),
                "-crf",
                os.getenv("FFMPEG_CRF", "20"),
                "-c:a",
                "aac",
                "-shortest",
                "-movflags",
                "+faststart",
                str(target),
            ]
            await asyncio.to_thread(
                subprocess.run, command, check=True, capture_output=True
            )
            normalized.append(target)

        concat_file = workdir / "concat.txt"
        concat_file.write_text(
            "\n".join(f"file '{item.as_posix()}'" for item in normalized),
            encoding="utf-8",
        )
        output = self.output_root / f"{job_id}.mp4"
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
                str(output),
            ],
            check=True,
            capture_output=True,
        )
        return output


class LongVideoOrchestrator:
    def __init__(self, data_root: str | Path | None = None):
        self.data_root = Path(
            data_root or os.getenv("VIDEO_DATA_ROOT", "./video-data")
        ).resolve()
        self.work_root = self.data_root / "work"
        self.output_root = self.data_root / "outputs"
        self.work_root.mkdir(parents=True, exist_ok=True)
        self.store = JsonJobStore(self.data_root)
        self.planner = ScenePlanner()
        self.renderer = FFmpegRenderer(self.output_root)
        self.tasks: dict[str, asyncio.Task[Any]] = {}
        self.scene_parallelism = max(
            1, int(os.getenv("VIDEO_SCENE_PARALLELISM", "1"))
        )

    def provider_for(self, name: ProviderName) -> ClipProvider:
        if name == "auto":
            if os.getenv("HELIOS_ROOT"):
                return HeliosProvider()
            if os.getenv("WAN21_ROOT"):
                return Wan21Provider()
            if os.getenv("COMFYUI_URL") and os.getenv("COMFYUI_VIDEO_WORKFLOW"):
                return ComfyUIProvider()
            raise RuntimeError(
                "No free local generator configured. Set HELIOS_ROOT, WAN21_ROOT, "
                "or COMFYUI_URL + COMFYUI_VIDEO_WORKFLOW."
            )
        providers: dict[str, type[ClipProvider]] = {
            "helios": HeliosProvider,
            "wan21": Wan21Provider,
            "comfyui": ComfyUIProvider,
            "mock": MockProvider,
        }
        return providers[name]()

    async def create(self, request: VideoRequest) -> VideoJob:
        now = time.time()
        job = VideoJob(
            id=str(uuid.uuid4()),
            state="queued",
            request=request.model_dump(),
            created_at=now,
            updated_at=now,
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
            job.message = "Building scene plan"
            await self.store.put(job)
            scenes = self.planner.plan(request)
            job.scenes = [asdict(scene) for scene in scenes]
            await self.store.put(job)
            provider = self.provider_for(request.provider)
            semaphore = asyncio.Semaphore(self.scene_parallelism)
            clips: list[Path | None] = [None] * len(scenes)
            completed = 0

            async def generate_one(scene: ScenePlan) -> None:
                nonlocal completed
                async with semaphore:
                    current = await self.store.get(job_id)
                    if not current or current.cancelled:
                        raise asyncio.CancelledError
                    clip = await provider.generate(scene, request, workdir)
                    clips[scene.index] = clip
                    scene.clip_path = str(clip)
                    completed += 1
                    current.state = "generating"
                    current.progress = completed / len(scenes) * 0.88
                    current.message = f"Generated scene {completed}/{len(scenes)}"
                    current.scenes[scene.index] = asdict(scene)
                    await self.store.put(current)

            await asyncio.gather(*(generate_one(scene) for scene in scenes))
            ready = [clip for clip in clips if clip is not None]
            if len(ready) != len(scenes):
                raise RuntimeError("One or more scenes did not produce a clip")

            job = await self.store.get(job_id) or job
            job.state = "rendering"
            job.progress = 0.92
            job.message = "Normalizing and concatenating scenes"
            await self.store.put(job)
            output = await self.renderer.render(job_id, ready, request, workdir)
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
        except Exception as error:
            job = await self.store.get(job_id) or job
            job.state = "failed"
            job.error = str(error)
            job.message = "Video generation failed"
            await self.store.put(job)
