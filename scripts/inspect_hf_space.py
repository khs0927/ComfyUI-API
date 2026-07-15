from __future__ import annotations

import argparse
import json
import os

from gradio_client import Client


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Inspect a public Hugging Face Gradio Space before configuring it as a fallback."
    )
    parser.add_argument("space_id", help="For example: BestWishYsh/Helios-14B-RealTime-AOTI")
    args = parser.parse_args()

    client = Client(args.space_id, token=os.getenv("HF_TOKEN") or None)
    api = client.view_api(return_format="dict")
    print(json.dumps(api, ensure_ascii=False, indent=2, default=str))
    print("\nConfigure HF_VIDEO_SPACE_API_NAME and the JSON input template from this schema.")


if __name__ == "__main__":
    main()
