"""Free long-form AI video orchestration package."""

import os
from typing import Literal

from . import core as _core

ProviderName = Literal[
    "auto",
    "beam",
    "kaggle",
    "hf",
    "helios",
    "wan21",
    "comfyui",
    "mock",
    "modal",
]


class VideoRequest(_core.VideoRequest):
    provider: ProviderName = "auto"


# LongVideoOrchestrator resolves this module global at run time, so replacing the
# request class keeps persisted jobs compatible while extending provider values.
_core.VideoRequest = VideoRequest

from .remote_providers import (  # noqa: E402
    BeamHeliosProvider,
    HuggingFaceSpaceProvider,
    KaggleHeliosProvider,
    ModalReservedProvider,
    install as install_remote_provider_chain,
)

install_remote_provider_chain()

LongVideoOrchestrator = _core.LongVideoOrchestrator
VideoJob = _core.VideoJob
_previous_provider_for = LongVideoOrchestrator.provider_for


def _provider_for(self: LongVideoOrchestrator, name: ProviderName):
    if name == "beam":
        if not (os.getenv("BEAM_TASK_QUEUE_URL") and os.getenv("BEAM_TOKEN")):
            raise RuntimeError("Beam is not configured")
        return BeamHeliosProvider()
    if name == "kaggle":
        if not os.getenv("KAGGLE_KERNEL_ID"):
            raise RuntimeError("Kaggle is not configured")
        return KaggleHeliosProvider()
    if name == "hf":
        if not os.getenv("HF_VIDEO_SPACE_ID"):
            raise RuntimeError("Hugging Face Space is not configured")
        return HuggingFaceSpaceProvider()
    if name == "modal":
        return ModalReservedProvider()
    return _previous_provider_for(self, name)


LongVideoOrchestrator.provider_for = _provider_for  # type: ignore[method-assign]

__all__ = ["LongVideoOrchestrator", "VideoRequest", "VideoJob", "ProviderName"]
