import json

import pytest
from pydantic import ValidationError

from video_engine import VideoRequest
from video_engine.core import ScenePlanner
from video_engine.remote_providers import (
    BeamHeliosProvider,
    FallbackProvider,
    HuggingFaceSpaceProvider,
    KaggleHeliosProvider,
    _configured_candidates,
    _extract_media,
    _extract_task_id,
    _replace_templates,
)


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


def test_free_cloud_local_and_reserved_provider_values_are_accepted():
    accepted = (
        "auto",
        "beam",
        "kaggle",
        "hf",
        "helios",
        "wan21",
        "comfyui",
        "mock",
        "modal",
    )
    for provider in accepted:
        assert VideoRequest(prompt="test prompt", provider=provider).provider == provider
    with pytest.raises(ValidationError):
        VideoRequest(prompt="test prompt", provider="fal_ltx")


def test_aspect_ratio_normalizes_dimensions():
    vertical = VideoRequest(
        prompt="vertical video", aspect_ratio="9:16", width=1920, height=1080
    )
    assert vertical.height > vertical.width
    square = VideoRequest(
        prompt="square video", aspect_ratio="1:1", width=1280, height=720
    )
    assert square.width == square.height == 720


def test_remote_response_helpers_are_schema_tolerant():
    assert _extract_task_id({"task_id": "abc"}) == "abc"
    assert _extract_task_id({"task": {"id": "nested"}}) == "nested"
    assert _extract_media({"outputs": [{"url": "https://example.com/a.mp4"}]}) == (
        "https://example.com/a.mp4"
    )
    assert _extract_media((None, {"video": "/tmp/result.webm"})) == "/tmp/result.webm"


def test_hf_template_replacement_preserves_types():
    template = {
        "prompt": "{{PROMPT}}",
        "seed": "{{SEED}}",
        "description": "clip {{DURATION_SECONDS}} seconds",
    }
    replaced = _replace_templates(
        template,
        {"{{PROMPT}}": "hello", "{{SEED}}": 7, "{{DURATION_SECONDS}}": 30},
    )
    assert replaced == {"prompt": "hello", "seed": 7, "description": "clip 30 seconds"}


def test_provider_order_builds_only_configured_backends(monkeypatch, tmp_path):
    monkeypatch.setenv("VIDEO_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("VIDEO_PROVIDER_ORDER", "beam,kaggle,hf")
    monkeypatch.setenv("BEAM_TASK_QUEUE_URL", "https://beam.example/task")
    monkeypatch.setenv("BEAM_TOKEN", "token")
    monkeypatch.setenv("KAGGLE_KERNEL_ID", "owner/kernel")
    monkeypatch.setenv("KAGGLE_WORKER_TEMPLATE", str(tmp_path / "missing-template"))
    monkeypatch.setenv("HF_VIDEO_SPACE_ID", "owner/space")
    candidates = _configured_candidates()
    assert [candidate.name for candidate in candidates] == ["beam", "kaggle", "hf"]
    assert isinstance(candidates[0].provider, BeamHeliosProvider)
    assert isinstance(candidates[1].provider, KaggleHeliosProvider)
    assert isinstance(candidates[2].provider, HuggingFaceSpaceProvider)
    assert isinstance(FallbackProvider(candidates), FallbackProvider)


def test_hf_json_templates_are_valid_examples(monkeypatch, tmp_path):
    monkeypatch.setenv("VIDEO_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("HF_VIDEO_SPACE_ID", "owner/space")
    monkeypatch.setenv(
        "HF_VIDEO_SPACE_INPUTS_JSON",
        json.dumps(["{{PROMPT}}", "{{SEED}}"]),
    )
    monkeypatch.setenv(
        "HF_VIDEO_SPACE_KWARGS_JSON",
        json.dumps({"duration": "{{DURATION_SECONDS}}"}),
    )
    provider = HuggingFaceSpaceProvider()
    assert provider.inputs_template == ["{{PROMPT}}", "{{SEED}}"]
    assert provider.kwargs_template == {"duration": "{{DURATION_SECONDS}}"}
