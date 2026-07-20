from __future__ import annotations

import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("HANALL_DATA_DIR", BASE_DIR / "data")).resolve()
DB_PATH = DATA_DIR / "hanall_catalogs.sqlite3"

MAX_FILES = int(os.getenv("HANALL_MAX_FILES", "20"))
MAX_FILE_MB = int(os.getenv("HANALL_MAX_FILE_MB", "300"))
MAX_FILE_BYTES = MAX_FILE_MB * 1024 * 1024
RENDER_DPI = int(os.getenv("HANALL_RENDER_DPI", "170"))
JPEG_QUALITY = int(os.getenv("HANALL_JPEG_QUALITY", "91"))


def ensure_data_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "jobs").mkdir(parents=True, exist_ok=True)


def job_dir(job_id: str) -> Path:
    return DATA_DIR / "jobs" / job_id

