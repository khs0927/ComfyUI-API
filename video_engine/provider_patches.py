from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import Any

import httpx

from .core import ScenePlan, VideoRequest


def install_provider_patches(remote_module: Any) -> None:
    """Install compatibility fixes without changing the public provider contract."""
    base_beam = remote_module.BeamHeliosProvider

    class CompatibleBeamHeliosProvider(base_beam):
        async def generate(
            self,
            scene: ScenePlan,
            request: VideoRequest,
            workdir: Path,
        ) -> Path:
            await self.ledger.reserve(scene.duration_seconds)
            scheme = os.getenv("BEAM_AUTH_SCHEME", "Bearer").strip() or "Bearer"
            headers = {
                "Authorization": f"{scheme} {self.token}",
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
                try:
                    body: Any = response.json()
                except ValueError:
                    body = response.text
                task_id = remote_module._extract_task_id(body)
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
                        media = remote_module._extract_media(status.get("outputs") or status)
                        if not media or not media.startswith(("http://", "https://")):
                            raise RuntimeError(
                                f"Beam task completed without output URL: {status}"
                            )
                        output = workdir / f"scene-{scene.index:05d}.mp4"
                        return await remote_module._download(media, output, headers=headers)
                    if state in {"FAILED", "ERROR", "CANCELLED", "CANCELED"}:
                        raise RuntimeError(f"Beam task {task_id} ended as {state}: {status}")
                    await asyncio.sleep(self.poll_seconds)
            raise TimeoutError(f"Beam task exceeded {self.timeout:.0f}s")

    remote_module.BeamHeliosProvider = CompatibleBeamHeliosProvider
