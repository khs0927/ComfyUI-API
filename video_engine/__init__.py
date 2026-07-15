"""Free long-form AI video orchestration package."""

from .core import LongVideoOrchestrator, VideoJob, VideoRequest
from .remote_providers import install as install_remote_provider_chain

install_remote_provider_chain()

__all__ = ["LongVideoOrchestrator", "VideoRequest", "VideoJob"]
