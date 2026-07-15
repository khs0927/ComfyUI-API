from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from video_engine import VideoRequest


FENCED_JSON = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)


def extract_payload(body: str) -> dict:
    body = body.strip()
    match = FENCED_JSON.search(body)
    candidate = match.group(1) if match else body
    payload = json.loads(candidate)
    if not isinstance(payload, dict):
        raise ValueError("Video issue payload must be a JSON object")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--body-file", required=True)
    parser.add_argument("--output", default="video-request.json")
    args = parser.parse_args()

    body = Path(args.body_file).read_text(encoding="utf-8")
    payload = extract_payload(body)
    request = VideoRequest.model_validate(payload)
    output = Path(args.output)
    output.write_text(
        json.dumps(request.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
