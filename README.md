# Unlimited AI Video MCP

An Model Context Protocol (MCP) server and HTTP API service designed for high-performance long-form AI video generation. It automatically orchestrates dynamic scene-by-scene storyboard planning and distributes the rendering load across a pool of free and remote compute providers:

* **Beam**: High-speed task queue execution on H100, RTX4090, or A10G GPUs.
* **Kaggle**: Fallback worker running on Tesla T4 GPUs.
* **Hugging Face Spaces**: Instant ZeroGPU inference using preset endpoints (e.g., Helios-14B, LTX-Video, Wan2.1).

---

## Features

* **Storyboard Scene Planning**: Intelligently segments long video scripts into natural 5-to-10 second scenes with individual narration-aware visual prompts and seeds.
* **Provider Failover**: Automatically chains execution across configured cloud/remote backends based on resource availability and quota limits.
* **Daily Quota Guards**: Built-in limits to protect remote execution budgets (e.g., Beam: 20 min/day, Kaggle: 10 min/day, HF: 2 min/day).
* **MCP Integration**: Fully exposes tools for starting jobs, querying status, canceling runs, and estimating video generation workloads directly to LLMs.
* **Concurrent Scene Rendering**: Speeds up creation by processing separate scenes in parallel when permitted by compute slots.

---

## Installation & Setup

1. **Clone the Repository**:
   ```bash
   git clone -b agent/unlimited-ai-video-mcp https://github.com/khs0927/ComfyUI-API.git
   cd ComfyUI-API
   ```

2. **Install Dependencies**:
   ```bash
   pip install -r requirements-video.txt
   ```

3. **Install FFmpeg**:
   Ensure `ffmpeg` is installed and available in your system path (required for video trimming and scene concatenation).

---

## Configuration

Set the credentials for your remote providers in your environment variables:

```ini
# Beam Credentials
BEAM_TOKEN=your_beam_token
BEAM_TASK_QUEUE_URL=https://your-beam-task-queue.app.beam.cloud

# Kaggle Credentials
KAGGLE_API_TOKEN=your_kaggle_api_token
KAGGLE_KERNEL_ID=your_username/helios-video-worker

# Hugging Face Credentials
HF_TOKEN=your_hugging_face_token
HF_VIDEO_SPACE_PRESET=helios_realtime
```

---

## Running the Services

### 1. HTTP API Server
Runs a FastAPI service exposing video endpoints on port 7860:
```bash
uvicorn video_api:app --host 127.0.0.1 --port 7860
```

### 2. MCP Server
Runs the FastMCP interface for integration with tools like Claude Desktop or Cursor:
```bash
python video_mcp_server.py
```

---

## MCP Tools

* `create_long_video(prompt, script, target_duration_minutes, scene_seconds, provider, aspect_ratio, width, height, fps, style)`: Starts a video generation job.
* `get_video_status(job_id)`: Retrieves status, scene list, and download URLs.
* `cancel_video(job_id)`: Cancels a queued or running job.
* `estimate_video_plan(prompt, script, target_duration_minutes, scene_seconds)`: Generates a scene blueprint without invoking GPUs.
* `get_video_provider_status()`: Lists active/configured backends.