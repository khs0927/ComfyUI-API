from __future__ import annotations

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import video_api


def test_fails_closed_without_token(monkeypatch):
    monkeypatch.delenv("VIDEO_API_TOKEN", raising=False)
    monkeypatch.delenv("VIDEO_API_ALLOW_UNAUTHENTICATED", raising=False)
    with pytest.raises(HTTPException) as exc:
        video_api.authorize(None)
    assert exc.value.status_code == 503


def test_explicit_opt_out(monkeypatch):
    monkeypatch.delenv("VIDEO_API_TOKEN", raising=False)
    monkeypatch.setenv("VIDEO_API_ALLOW_UNAUTHENTICATED", "1")
    video_api.authorize(None)


@pytest.mark.parametrize("header", [None, "", "Bearer ", "Bearer wrong", "secret"[:-1]])
def test_rejects_bad_token(monkeypatch, header):
    monkeypatch.setenv("VIDEO_API_TOKEN", "secret")
    with pytest.raises(HTTPException) as exc:
        video_api.authorize(header)
    assert exc.value.status_code == 401


def test_accepts_good_token(monkeypatch):
    monkeypatch.setenv("VIDEO_API_TOKEN", "secret")
    video_api.authorize("Bearer secret")


def test_endpoint_without_token_is_503(monkeypatch):
    monkeypatch.delenv("VIDEO_API_TOKEN", raising=False)
    monkeypatch.delenv("VIDEO_API_ALLOW_UNAUTHENTICATED", raising=False)
    client = TestClient(video_api.app)
    assert client.get("/v1/videos").status_code == 503
    monkeypatch.setenv("VIDEO_API_TOKEN", "secret")
    assert client.get("/v1/videos").status_code == 401
    assert client.get("/v1/videos", headers={"Authorization": "Bearer secret"}).status_code == 200


def test_cors_default_is_localhost_only():
    client = TestClient(video_api.app)
    evil = client.get("/health", headers={"Origin": "https://evil.example"})
    assert evil.headers.get("access-control-allow-origin") is None
    ok = client.get("/health", headers={"Origin": "http://localhost:8000"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:8000"
