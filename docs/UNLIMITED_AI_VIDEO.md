# Unlimited-length AI video from mobile ChatGPT

## Decision

Use this repository as a **controller and orchestration layer**, not as a monolithic GPU application.

- **Mobile interface:** ChatGPT calls four MCP tools or the equivalent REST endpoints.
- **Free-first GPU worker:** a local/remote ComfyUI installation running LTX-2.3.
- **Free controller hosting:** a CPU Docker Space on Hugging Face, or any small Docker host.
- **Demo fallback:** an existing Hugging Face Gradio/ZeroGPU Space.
- **Paid fallback:** fal.ai LTX-2.3 when the local worker is unavailable.
- **Final renderer:** FFmpeg normalizes and concatenates generated scenes.

There is no fixed application-level final-duration limit. A 10-minute, 60-minute, or longer request becomes a larger scene plan. Physical limits still exist: GPU time, storage, provider quotas, network transfer, and platform request timeouts.

## Why not generate one huge clip?

Current video models and hosted APIs still operate best with bounded clips. LTX APIs commonly expose 6/8/10-second clips, while open LTX workflows can produce or extend longer clips. The reliable production pattern is:

1. Parse the user prompt or full script.
2. Produce a scene plan with stable character/style instructions.
3. Generate each scene independently or in small parallel batches.
4. Normalize every clip to the requested size, FPS, codecs, and audio layout.
5. Concatenate clips with FFmpeg.
6. Return one MP4 download URL to ChatGPT.

This makes final duration a workflow concern rather than a model limit.

## Components added

### `video_engine/core.py`

- Pydantic request validation
- Korean-friendly duration estimation
- unlimited scene planning
- persistent JSON job state
- ComfyUI, Hugging Face Space, fal.ai, and mock providers
- scene-level retries can be added without re-rendering completed scenes
- FFmpeg normalization and concatenation

### `video_engine/fal_ltx.py`

Adapts the current fal LTX-2.3 API schema. Long scenes are divided into supported 6/8/10-second generations and reassembled.

### `video_api.py`

REST endpoints suitable for Custom GPT Actions:

- `POST /v1/videos`
- `GET /v1/videos/{job_id}`
- `POST /v1/videos/{job_id}/cancel`
- `GET /v1/videos/{job_id}/download`
- `GET /openapi.json`

### `video_mcp_server.py`

Remote MCP tools suitable for a ChatGPT app/connector:

- `create_long_video`
- `get_video_status`
- `cancel_video`
- `estimate_video_plan`

## Provider selection

`provider=auto` uses the first configured backend:

1. ComfyUI when `COMFYUI_URL` and `COMFYUI_VIDEO_WORKFLOW` are set.
2. fal.ai when `FAL_KEY` is set.
3. Hugging Face Space when `HF_VIDEO_SPACE_ID` is set.
4. mock clips for safe testing.

For a no-subscription workflow, keep fal unset and connect a local ComfyUI worker.

## Local LTX-2.3 worker

Run:

```bash
bash scripts/bootstrap_ltx23_worker.sh
```

Then follow the printed steps to download LTX-2.3 weights, load an official ComfyUI workflow, and export it in API format.

The API workflow must contain these exact token strings in editable values:

```text
{{PROMPT}}
{{NEGATIVE_PROMPT}}
{{WIDTH}}
{{HEIGHT}}
{{FPS}}
{{DURATION_SECONDS}}
{{FRAME_COUNT}}
{{SEED}}
```

Save the exported workflow as `workflows/ltx23_video_api.json` or set `COMFYUI_VIDEO_WORKFLOW` to another path.

## Hugging Face deployment

Create a Docker Space and copy this branch into it. Use `Dockerfile.video` as the Dockerfile (or rename it to `Dockerfile`). Set secrets and variables from `.env.video.example`.

A CPU Space is enough for the controller because it does not run the diffusion model. Generated MP4 files need persistent or external object storage for production. Free ephemeral Space storage is acceptable only for testing.

ZeroGPU/public Spaces are useful as a fallback, but they have daily quotas and queues; they cannot guarantee unlimited free generation.

## ChatGPT mobile connection

### MCP route

Deploy `video_mcp_server.py` on a public HTTPS endpoint that supports Streamable HTTP. Add the remote MCP server as a ChatGPT app/connector, then ask:

```text
이 설교문으로 30분짜리 16:9 영상을 만들어줘. 장면은 8초 단위, 영화적 다큐멘터리 스타일, 한국어 흐름을 유지해줘.
```

### Custom GPT Action route

Deploy `video_api.py`, open `/openapi.json`, and use that schema for the Action. Configure bearer authentication with `VIDEO_API_TOKEN`.

## Security

- Never expose raw ComfyUI directly without authentication.
- Put the controller behind HTTPS and a long bearer token.
- Use a private tunnel or VPN between the controller and local ComfyUI.
- Restrict download retention and delete old outputs.
- Add per-user concurrency and storage quotas before sharing publicly.

## CircleCI role

CircleCI validates Python, unit tests, and the controller Docker build. It deliberately does not generate GPU video. GPU generation belongs to the worker backend, not CI.

## Remaining production work

1. Export and commit a tested LTX-2.3 ComfyUI API workflow.
2. Add S3/R2/Supabase Storage for durable final MP4 delivery.
3. Add Redis/Postgres queue state when multiple controller replicas are needed.
4. Add Korean TTS and SRT generation for models that do not create native speech.
5. Add last-frame/image conditioning to improve character continuity between scenes.
6. Add retry policies and a scene regeneration tool.
