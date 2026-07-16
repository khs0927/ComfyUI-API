from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from .core import (
    ClipProvider,
    HeliosProvider,
    LongVideoOrchestrator,
    MockProvider,
    ScenePlan,
    VideoRequest,
    Wan21Provider,
)


VIDEO_SUFFIXES = (".mp4", ".webm", ".mov", ".mkv")


async def _download(url: str, destination: Path, headers: dict[str, str] | None = None) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=None, follow_redirects=True, headers=headers) as client:
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            with destination.open("wb") as output:
                async for chunk in response.aiter_bytes():
                    output.write(chunk)
    return destination


def _extract_task_id(payload: Any) -> str | None:
    if isinstance(payload, str):
        return payload
    if isinstance(payload, dict):
        for key in ("task_id", "taskId", "id", "task"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
            if isinstance(value, dict):
                nested = _extract_task_id(value)
                if nested:
                    return nested
    return None


def _extract_media(value: Any) -> str | None:
    if isinstance(value, str):
        if value.startswith(("http://", "https://")) or value.lower().endswith(VIDEO_SUFFIXES):
            return value
        return None
    if isinstance(value, dict):
        for key in ("url", "path", "video", "file", "name"):
            candidate = _extract_media(value.get(key))
            if candidate:
                return candidate
        for nested in value.values():
            candidate = _extract_media(nested)
            if candidate:
                return candidate
    if isinstance(value, (list, tuple)):
        for nested in value:
            candidate = _extract_media(nested)
            if candidate:
                return candidate
    return None


class DailyUsageLedger:
    """Small local guardrail. It limits requested output duration, not provider billing."""

    def __init__(self, provider: str, limit_seconds: int) -> None:
        root = Path(os.getenv("VIDEO_DATA_ROOT", "./video-data")).resolve()
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "remote-provider-usage.json"
        self.provider = provider
        self.limit_seconds = max(0, limit_seconds)
        self._lock = asyncio.Lock()

    async def reserve(self, seconds: int) -> None:
        if self.limit_seconds <= 0:
            return
        async with self._lock:
            today = datetime.now(UTC).date().isoformat()
            payload: dict[str, Any] = {}
            if self.path.exists():
                try:
                    payload = json.loads(self.path.read_text(encoding="utf-8"))
                except (OSError, ValueError, json.JSONDecodeError):
                    payload = {}
            provider_usage = payload.setdefault(self.provider, {})
            used = int(provider_usage.get(today, 0))
            if used + seconds > self.limit_seconds:
                raise RuntimeError(
                    f"{self.provider} daily video-duration guard reached: "
                    f"{used}/{self.limit_seconds}s"
                )
            provider_usage[today] = used + seconds
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            temporary.replace(self.path)


class BeamHeliosProvider(ClipProvider):
    """Submit one Helios scene to an authenticated Beam task queue."""

    def __init__(self) -> None:
        self.queue_url = os.environ["BEAM_TASK_QUEUE_URL"]
        self.token = os.environ["BEAM_TOKEN"]
        self.status_template = os.getenv(
            "BEAM_TASK_STATUS_URL", "https://api.beam.cloud/v2/task/{task_id}/"
        )
        self.poll_seconds = float(os.getenv("BEAM_POLL_SECONDS", "10"))
        self.timeout = float(os.getenv("BEAM_SCENE_TIMEOUT", "21600"))
        self.ledger = DailyUsageLedger(
            "beam", int(os.getenv("BEAM_DAILY_VIDEO_SECONDS", "1200"))
        )

    async def generate(
        self, scene: ScenePlan, request: VideoRequest, workdir: Path
    ) -> Path:
        await self.ledger.reserve(scene.duration_seconds)
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        payload = {
            "prompt": scene.visual_prompt,
            "duration_seconds": scene.duration_seconds,
            "width": request.width,
            "height": request.height,
            "fps": request.fps,
            "seed": scene.seed,
            "negative_prompt": request.negative_prompt,
        }
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            response = await client.post(self.queue_url, headers=headers, json=payload)
            response.raise_for_status()
            body: Any
            try:
                body = response.json()
            except ValueError:
                body = response.text
            task_id = _extract_task_id(body)
            if not task_id:
                raise RuntimeError(f"Beam response did not contain a task id: {body}")

            deadline = time.monotonic() + self.timeout
            while time.monotonic() < deadline:
                status_url = self.status_template.format(task_id=task_id)
                status_response = await client.get(status_url, headers=headers)
                status_response.raise_for_status()
                status = status_response.json()
                state = str(status.get("status", "")).upper()
                if state in {"COMPLETE", "COMPLETED", "SUCCESS"}:
                    media = _extract_media(status.get("outputs") or status)
                    if not media or not media.startswith(("http://", "https://")):
                        raise RuntimeError(f"Beam task completed without output URL: {status}")
                    output = workdir / f"scene-{scene.index:05d}.mp4"
                    return await _download(media, output, headers=headers)
                if state in {"FAILED", "ERROR", "CANCELLED", "CANCELED"}:
                    raise RuntimeError(f"Beam task {task_id} ended as {state}: {status}")
                await asyncio.sleep(self.poll_seconds)
        raise TimeoutError(f"Beam task exceeded {self.timeout:.0f}s")


class KaggleHeliosProvider(ClipProvider):
    """Run a private Kaggle GPU kernel through the official Kaggle CLI."""

    def __init__(self) -> None:
        self.kernel_id = os.environ["KAGGLE_KERNEL_ID"]
        self.template = Path(
            os.getenv("KAGGLE_WORKER_TEMPLATE", "workers/kaggle_helios")
        ).resolve()
        self.accelerator = os.getenv("KAGGLE_ACCELERATOR", "NvidiaTeslaT4")
        self.timeout = int(os.getenv("KAGGLE_SCENE_TIMEOUT", "21600"))
        self.poll_seconds = float(os.getenv("KAGGLE_POLL_SECONDS", "30"))
        self.ledger = DailyUsageLedger(
            "kaggle", int(os.getenv("KAGGLE_DAILY_VIDEO_SECONDS", "600"))
        )

    @staticmethod
    def _run(command: list[str], cwd: Path | None = None) -> str:
        completed = subprocess.run(
            command,
            cwd=str(cwd) if cwd else None,
            check=True,
            capture_output=True,
            text=True,
        )
        return (completed.stdout or completed.stderr or "").strip()

    async def generate(
        self, scene: ScenePlan, request: VideoRequest, workdir: Path
    ) -> Path:
        if not shutil.which("kaggle"):
            raise RuntimeError("Kaggle CLI is not installed")
        if not self.template.exists():
            raise RuntimeError(f"Kaggle worker template not found: {self.template}")
        await self.ledger.reserve(scene.duration_seconds)

        job_dir = workdir / f"kaggle-{scene.index:05d}"
        if job_dir.exists():
            shutil.rmtree(job_dir)
        shutil.copytree(self.template, job_dir)
        metadata_path = job_dir / "kernel-metadata.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["id"] = self.kernel_id
        metadata["title"] = os.getenv("KAGGLE_KERNEL_TITLE", "Helios video worker")
        metadata["is_private"] = "true"
        metadata["enable_gpu"] = "true"
        metadata["enable_internet"] = os.getenv("KAGGLE_ENABLE_INTERNET", "true")
        metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        (job_dir / "request.json").write_text(
            json.dumps(
                {
                    "prompt": scene.visual_prompt,
                    "duration_seconds": scene.duration_seconds,
                    "width": request.width,
                    "height": request.height,
                    "fps": request.fps,
                    "seed": scene.seed,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        await asyncio.to_thread(
            self._run,
            [
                "kaggle",
                "kernels",
                "push",
                "-p",
                str(job_dir),
                "--accelerator",
                self.accelerator,
                "--timeout",
                str(self.timeout),
            ],
        )

        deadline = time.monotonic() + self.timeout
        last_status = ""
        while time.monotonic() < deadline:
            last_status = await asyncio.to_thread(
                self._run, ["kaggle", "kernels", "status", self.kernel_id]
            )
            normalized = last_status.lower()
            if any(word in normalized for word in ("complete", "completed")):
                break
            if any(word in normalized for word in ("error", "failed", "cancel")):
                raise RuntimeError(f"Kaggle kernel failed: {last_status}")
            await asyncio.sleep(self.poll_seconds)
        else:
            raise TimeoutError(f"Kaggle kernel exceeded {self.timeout}s: {last_status}")

        output_dir = job_dir / "outputs"
        output_dir.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(
            self._run,
            [
                "kaggle",
                "kernels",
                "output",
                self.kernel_id,
                "-p",
                str(output_dir),
                "-o",
                "-q",
                "--file-pattern",
                r".*\.(mp4|webm|mov|mkv)$",
            ],
        )
        candidates = [
            path
            for path in output_dir.rglob("*")
            if path.is_file() and path.suffix.lower() in VIDEO_SUFFIXES
        ]
        if not candidates:
            raise RuntimeError(f"Kaggle completed without a video output: {output_dir}")
        source = max(candidates, key=lambda path: path.stat().st_mtime)
        destination = workdir / f"scene-{scene.index:05d}{source.suffix.lower()}"
        shutil.copy2(source, destination)
        return destination


def _replace_templates(value: Any, tokens: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        return {key: _replace_templates(item, tokens) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_templates(item, tokens) for item in value]
    if isinstance(value, str):
        if value in tokens:
            return tokens[value]
        for token, replacement in tokens.items():
            value = value.replace(token, str(replacement))
    return value


class HuggingFaceSpaceProvider(ClipProvider):
    """Best-effort public Gradio Space fallback; free quotas and schemas vary."""

    def __init__(self) -> None:
        self.space_id = os.environ["HF_VIDEO_SPACE_ID"]
        self.api_name = os.getenv("HF_VIDEO_SPACE_API_NAME", "/generate")
        self.inputs_template = json.loads(os.getenv("HF_VIDEO_SPACE_INPUTS_JSON", "[]"))
        self.kwargs_template = json.loads(os.getenv("HF_VIDEO_SPACE_KWARGS_JSON", "{}"))
        self.ledger = DailyUsageLedger(
            "huggingface", int(os.getenv("HF_DAILY_VIDEO_SECONDS", "120"))
        )

    async def generate(
        self, scene: ScenePlan, request: VideoRequest, workdir: Path
    ) -> Path:
        await self.ledger.reserve(scene.duration_seconds)
        try:
            from gradio_client import Client
        except ImportError as error:
            raise RuntimeError("Install gradio-client for Hugging Face Space fallback") from error

        tokens = {
            "{{PROMPT}}": scene.visual_prompt,
            "{{NEGATIVE_PROMPT}}": request.negative_prompt,
            "{{DURATION_SECONDS}}": scene.duration_seconds,
            "{{WIDTH}}": request.width,
            "{{HEIGHT}}": request.height,
            "{{FPS}}": request.fps,
            "{{SEED}}": scene.seed,
        }
        args = _replace_templates(self.inputs_template, tokens)
        kwargs = _replace_templates(self.kwargs_template, tokens)

        def call() -> Any:
            client = Client(self.space_id, token=os.getenv("HF_TOKEN") or None)
            return client.predict(*args, **kwargs, api_name=self.api_name)

        result = await asyncio.to_thread(call)
        media = _extract_media(result)
        if not media:
            raise RuntimeError(f"Hugging Face Space returned no video: {result}")
        suffix = Path(media).suffix.lower()
        if suffix not in VIDEO_SUFFIXES:
            suffix = ".mp4"
        destination = workdir / f"scene-{scene.index:05d}{suffix}"
        if media.startswith(("http://", "https://")):
            return await _download(media, destination)
        source = Path(media)
        if not source.exists():
            raise RuntimeError(f"Space returned an unavailable local path: {media}")
        shutil.copy2(source, destination)
        return destination


class ModalReservedProvider(ClipProvider):
    async def generate(
        self, scene: ScenePlan, request: VideoRequest, workdir: Path
    ) -> Path:
        raise RuntimeError(
            "Modal adapter is intentionally reserved but disabled. "
            "Implement it behind MODAL_ENABLED=true without changing the orchestrator contract."
        )


@dataclass
class ProviderCandidate:
    name: str
    provider: ClipProvider


class FallbackProvider(ClipProvider):
    def __init__(self, providers: list[ProviderCandidate]) -> None:
        self.providers = providers

    async def generate(
        self, scene: ScenePlan, request: VideoRequest, workdir: Path
    ) -> Path:
        errors: list[str] = []
        for candidate in self.providers:
            try:
                return await candidate.provider.generate(scene, request, workdir)
            except Exception as error:  # Each provider has different remote failures.
                errors.append(f"{candidate.name}: {error}")
        raise RuntimeError("All configured providers failed: " + " | ".join(errors))


def _configured_candidates() -> list[ProviderCandidate]:
    factories: dict[str, tuple[bool, type[ClipProvider]]] = {
        "beam": (
            bool(os.getenv("BEAM_TASK_QUEUE_URL") and os.getenv("BEAM_TOKEN")),
            BeamHeliosProvider,
        ),
        "kaggle": (bool(os.getenv("KAGGLE_KERNEL_ID")), KaggleHeliosProvider),
        "hf": (bool(os.getenv("HF_VIDEO_SPACE_ID")), HuggingFaceSpaceProvider),
        "helios": (bool(os.getenv("HELIOS_ROOT")), HeliosProvider),
        "wan21": (bool(os.getenv("WAN21_ROOT")), Wan21Provider),
        "modal": (os.getenv("MODAL_ENABLED", "false").lower() == "true", ModalReservedProvider),
        "mock": (os.getenv("VIDEO_ALLOW_MOCK", "false").lower() == "true", MockProvider),
    }
    order = [
        item.strip().lower()
        for item in os.getenv(
            "VIDEO_PROVIDER_ORDER", "beam,kaggle,hf,helios,wan21"
        ).split(",")
        if item.strip()
    ]
    candidates: list[ProviderCandidate] = []
    for name in order:
        configured, factory = factories.get(name, (False, MockProvider))
        if configured:
            candidates.append(ProviderCandidate(name=name, provider=factory()))
    return candidates


_ORIGINAL_PROVIDER_FOR = LongVideoOrchestrator.provider_for


def _provider_for(self: LongVideoOrchestrator, name: Any) -> ClipProvider:
    if name == "auto":
        candidates = _configured_candidates()
        if candidates:
            return FallbackProvider(candidates)
    return _ORIGINAL_PROVIDER_FOR(self, name)


def install() -> None:
    if getattr(LongVideoOrchestrator.provider_for, "_remote_chain_installed", False):
        return
    setattr(_provider_for, "_remote_chain_installed", True)
    LongVideoOrchestrator.provider_for = _provider_for  # type: ignore[method-assign]
