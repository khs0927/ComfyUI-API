import pytest
from pydantic import ValidationError

from video_engine.core import ScenePlanner, VideoRequest


def test_planner_has_no_fixed_final_duration_cap():
    request = VideoRequest(
        prompt="A coherent documentary about restoration",
        script="회복의 이야기입니다. " * 200,
        target_duration_minutes=30,
        scene_seconds=30,
        provider="mock",
    )
    scenes = ScenePlanner().plan(request)
    assert len(scenes) == 60
    assert sum(scene.duration_seconds for scene in scenes) == 1800
    assert all(scene.visual_prompt for scene in scenes)


def test_korean_script_duration_is_estimated_when_unspecified():
    request = VideoRequest(prompt="테스트", script="가" * 580, scene_seconds=30)
    scenes = ScenePlanner().plan(request)
    assert sum(scene.duration_seconds for scene in scenes) == 120


def test_only_free_local_providers_are_accepted():
    for provider in ("helios", "wan21", "comfyui", "mock", "auto"):
        assert VideoRequest(prompt="test prompt", provider=provider).provider == provider
    with pytest.raises(ValidationError):
        VideoRequest(prompt="test prompt", provider="fal_ltx")
    with pytest.raises(ValidationError):
        VideoRequest(prompt="test prompt", provider="hf_space")


def test_aspect_ratio_normalizes_dimensions():
    vertical = VideoRequest(
        prompt="vertical video", aspect_ratio="9:16", width=1920, height=1080
    )
    assert vertical.height > vertical.width
    square = VideoRequest(
        prompt="square video", aspect_ratio="1:1", width=1280, height=720
    )
    assert square.width == square.height == 720
