"""Long-form AI video orchestration package."""

from .core import LongVideoOrchestrator, VideoJob, VideoRequest
from .fal_ltx import install as install_fal_ltx_provider

install_fal_ltx_provider()

__all__ = ["LongVideoOrchestrator", "VideoRequest", "VideoJob"]
