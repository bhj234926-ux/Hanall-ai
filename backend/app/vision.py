from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from typing import Any

from .config import GPU_VISION_TIMEOUT, GPU_VISION_TOKEN, GPU_VISION_URL


TARGETS = ["wall", "floor", "ceiling", "door", "window", "furniture", "molding"]


class VisionUnavailable(RuntimeError):
    pass


def gpu_configured() -> bool:
    return bool(GPU_VISION_URL)


def segment_room(image_bytes: bytes, content_type: str) -> dict[str, Any]:
    if not GPU_VISION_URL:
        raise VisionUnavailable("GPU vision service is not configured")
    payload = json.dumps(
        {
            "image_base64": base64.b64encode(image_bytes).decode("ascii"),
            "content_type": content_type,
            "targets": TARGETS,
        }
    ).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if GPU_VISION_TOKEN:
        headers["Authorization"] = f"Bearer {GPU_VISION_TOKEN}"
    request = urllib.request.Request(GPU_VISION_URL, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=GPU_VISION_TIMEOUT) as response:
            result = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise VisionUnavailable(f"GPU vision request failed: {exc}") from exc
    masks = result.get("masks")
    if not isinstance(masks, dict) or not masks.get("wall"):
        raise VisionUnavailable("GPU vision response did not include a wall mask")
    return {
        "model": result.get("model", "gpu-room-segmentation"),
        "width": int(result.get("width", 0)),
        "height": int(result.get("height", 0)),
        "masks": {key: value for key, value in masks.items() if key in TARGETS and isinstance(value, str)},
        "scores": result.get("scores", {}),
    }
