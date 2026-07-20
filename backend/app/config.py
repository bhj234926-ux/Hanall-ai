from __future__ import annotations

import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("HANALL_DATA_DIR", BASE_DIR / "data")).resolve()
DB_PATH = DATA_DIR / "hanall_catalogs.sqlite3"
BACKUP_DIR = DATA_DIR / "backups"

MAX_FILES = int(os.getenv("HANALL_MAX_FILES", "20"))
MAX_FILE_MB = int(os.getenv("HANALL_MAX_FILE_MB", "300"))
MAX_FILE_BYTES = MAX_FILE_MB * 1024 * 1024
RENDER_DPI = int(os.getenv("HANALL_RENDER_DPI", "170"))
JPEG_QUALITY = int(os.getenv("HANALL_JPEG_QUALITY", "91"))
MAX_IMAGE_MB = int(os.getenv("HANALL_MAX_IMAGE_MB", "20"))
MAX_IMAGE_BYTES = MAX_IMAGE_MB * 1024 * 1024
GPU_VISION_URL = os.getenv("HANALL_GPU_VISION_URL", "").strip()
GPU_VISION_TOKEN = os.getenv("HANALL_GPU_VISION_TOKEN", "").strip()
GPU_VISION_TIMEOUT = int(os.getenv("HANALL_GPU_VISION_TIMEOUT", "120"))
ADMIN_TOKEN = os.getenv("HANALL_ADMIN_TOKEN", "").strip()


def ensure_data_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "jobs").mkdir(parents=True, exist_ok=True)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)


def job_dir(job_id: str) -> Path:
    return DATA_DIR / "jobs" / job_id
