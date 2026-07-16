from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

# Executing this file directly sets sys.path[0] to scripts/, which otherwise
# makes the repository's video_engine package unavailable on clean runners.
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from video_engine import LongVideoOrchestrator, VideoRequest


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run a remote long-video job and wait for completion"
    )
    parser.add_argument("--request-json")
    parser.add_argument("--prompt")
    parser.add_argument("--script-file")
    parser.add_argument("--minutes", type=float)
    parser.add_argument("--scene-seconds", type=int, default=90)
    parser.add_argument("--provider", default="auto")
    parser.add_argument("--aspect-ratio", default="16:9")
    parser.add_argument("--width", type=int, default=960)
    parser.add_argument("--height", type=int, default=544)
    parser.add_argument("--fps", type=int, default=24)
    parser.add_argument("--poll-seconds", type=float, default=10)
    parser.add_argument("--timeout-seconds", type=int, default=21600)
    args = parser.parse_args()

    if args.request_json:
        payload = json.loads(Path(args.request_json).read_text(encoding="utf-8"))
        request = VideoRequest.model_validate(payload)
    else:
        if not args.prompt or args.minutes is None:
            parser.error("--prompt and --minutes are required without --request-json")
        script = None
        if args.script_file:
            script = Path(args.script_file).read_text(encoding="utf-8")
        request = VideoRequest(
            prompt=args.prompt,
            script=script,
            target_duration_minutes=args.minutes,
            scene_seconds=args.scene_seconds,
            provider=args.provider,
            aspect_ratio=args.aspect_ratio,
            width=args.width,
            height=args.height,
            fps=args.fps,
        )

    max_minutes = float(os.getenv("REMOTE_JOB_MAX_MINUTES", "0"))
    requested_minutes = request.target_duration_minutes
    if requested_minutes is None:
        requested_minutes = LongVideoOrchestrator().planner.estimate_minutes(
            request.script or request.prompt
        )
    if max_minutes > 0 and requested_minutes > max_minutes:
        print(
            f"Requested duration {requested_minutes:.2f} minutes exceeds "
            f"REMOTE_JOB_MAX_MINUTES={max_minutes:.2f}",
            file=sys.stderr,
        )
        return 6

    engine = LongVideoOrchestrator(os.getenv("VIDEO_DATA_ROOT", "./video-data"))
    job = await engine.create(request)
    print(f"JOB_ID={job.id}", flush=True)

    deadline = time.monotonic() + args.timeout_seconds
    last_message = ""
    while time.monotonic() < deadline:
        current = await engine.get(job.id)
        if current is None:
            print("Job disappeared from store", file=sys.stderr)
            return 2
        if current.message != last_message:
            print(
                f"state={current.state} progress={current.progress:.3f} "
                f"message={current.message}",
                flush=True,
            )
            last_message = current.message
        if current.state == "completed":
            if not current.output_path:
                print("Completed job has no output path", file=sys.stderr)
                return 3
            output = Path(current.output_path).resolve()
            print(f"OUTPUT_PATH={output}", flush=True)
            github_output = os.getenv("GITHUB_OUTPUT")
            if github_output:
                with open(github_output, "a", encoding="utf-8") as handle:
                    handle.write(f"job_id={job.id}\n")
                    handle.write(f"output_path={output}\n")
            return 0
        if current.state in {"failed", "cancelled"}:
            print(current.error or current.message, file=sys.stderr)
            return 4
        await asyncio.sleep(args.poll_seconds)

    await engine.cancel(job.id)
    print("Remote video job timed out", file=sys.stderr)
    return 5


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
