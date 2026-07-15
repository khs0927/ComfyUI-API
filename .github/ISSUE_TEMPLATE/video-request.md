---
name: Remote AI video request
description: Trigger the free Beam → Kaggle → Hugging Face video worker
title: "[video] "
labels: []
assignees: []
---

Replace the example values below. Keep the body as valid JSON.

```json
{
  "prompt": "A coherent cinematic documentary about restoration and hope",
  "script": "Optional narration or full source text",
  "target_duration_minutes": 1,
  "scene_seconds": 90,
  "provider": "auto",
  "aspect_ratio": "16:9",
  "width": 960,
  "height": 544,
  "fps": 24,
  "style": "cinematic, coherent characters, natural motion"
}
```

Allowed providers: `auto`, `beam`, `kaggle`, `hf`.

Remote issue jobs are limited to 20 minutes per request. The issue worker comments with the Actions run link. After completion, download the MP4 from the run's **Artifacts** section. Artifacts are retained for 7 days.
