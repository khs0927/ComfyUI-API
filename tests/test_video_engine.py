from video_engine.core import ScenePlanner, VideoRequest
from video_engine.fal_ltx import _chunk_durations


def test_planner_has_no_fixed_final_duration_cap():
    request = VideoRequest(
        prompt="A coherent documentary about restoration",
        script="회복의 이야기입니다. " * 200,
        target_duration_minutes=30,
        scene_seconds=10,
        provider="mock",
    )
    scenes = ScenePlanner().plan(request)
    assert len(scenes) == 180
    assert sum(scene.duration_seconds for scene in scenes) == 1800
    assert all(scene.visual_prompt for scene in scenes)


def test_korean_script_duration_is_estimated_when_unspecified():
    request = VideoRequest(prompt="테스트", script="가" * 580, scene_seconds=10)
    scenes = ScenePlanner().plan(request)
    assert sum(scene.duration_seconds for scene in scenes) == 120


def test_fal_duration_is_split_into_supported_segments():
    assert _chunk_durations(3) == [(6, 3)]
    assert _chunk_durations(8) == [(8, 8)]
    assert _chunk_durations(9) == [(10, 9)]
    assert _chunk_durations(27) == [(10, 10), (10, 10), (8, 7)]


def test_aspect_ratio_normalizes_dimensions():
    vertical = VideoRequest(prompt="vertical video", aspect_ratio="9:16", width=1920, height=1080)
    assert vertical.height > vertical.width
    square = VideoRequest(prompt="square video", aspect_ratio="1:1", width=1280, height=720)
    assert square.width == square.height == 720
