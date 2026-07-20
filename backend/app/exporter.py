from __future__ import annotations

import csv
import io
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .db import get_all_products, get_job, summary_values


CSV_FIELDS = [
    "product_code",
    "normalized_code",
    "brand",
    "catalog",
    "collection",
    "page_number",
    "source_pdf",
    "texture",
    "dominant_color",
    "confidence",
    "status",
    "method",
    "width",
    "height",
    "sources_count",
]


def public_product(product: dict[str, Any], texture_name: str) -> dict[str, Any]:
    return {
        "id": product["id"],
        "product_code": product["product_code"],
        "normalized_code": product["normalized_code"],
        "brand": product["brand"],
        "catalog": product["catalog"],
        "collection": product["collection"],
        "page_number": product["page_number"],
        "source_pdf": product["source_pdf"],
        "texture": f"textures/{texture_name}",
        "dominant_color": product["dominant_color"],
        "confidence": product["confidence"],
        "status": product["status"],
        "extraction_method": product["method"],
        "width": product["width"],
        "height": product["height"],
        "image_hash": product["image_hash"],
        "sources": product["sources"],
    }


def safe_texture_name(product: dict[str, Any], source_path: Path) -> str:
    def clean(value: str) -> str:
        value = "".join(char if char.isalnum() or char in "-_" else "-" for char in value)
        return value.strip("-") or "unknown"

    return f"{clean(product['brand'])}__{clean(product['normalized_code'])}__{product['id'][:7]}{source_path.suffix.lower()}"


def build_export(job_id: str, job_root: Path) -> Path:
    products = get_all_products(job_id, include_excluded=False)
    job = get_job(job_id)
    if not job:
        raise ValueError("작업을 찾을 수 없습니다.")
    export_dir = job_root / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    zip_path = export_dir / f"hanall-catalog-{job_id[:8]}.zip"

    json_products: list[dict[str, Any]] = []
    csv_rows: list[dict[str, Any]] = []
    texture_sources: list[tuple[Path, str]] = []
    for product in products:
        source_path = job_root / product["texture_relpath"]
        if not source_path.is_file():
            continue
        texture_name = safe_texture_name(product, source_path)
        record = public_product(product, texture_name)
        json_products.append(record)
        texture_sources.append((source_path, texture_name))
        csv_rows.append(
            {
                key: (
                    len(record["sources"])
                    if key == "sources_count"
                    else record.get(key, "")
                )
                for key in CSV_FIELDS
            }
        )

    json_payload = {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "job_id": job_id,
        "summary": summary_values(job_id),
        "catalogs": job["catalogs"],
        "products": json_products,
    }
    csv_stream = io.StringIO(newline="")
    writer = csv.DictWriter(csv_stream, fieldnames=CSV_FIELDS)
    writer.writeheader()
    writer.writerows(csv_rows)

    manifest = {
        "name": "HANALL AI Catalog Database",
        "schema_version": "1.0",
        "products_file": "products.json",
        "csv_file": "products.csv",
        "textures_directory": "textures/",
        "product_count": len(json_products),
        "excluded_count": summary_values(job_id)["excluded"],
        "deduplication": "brand + normalized product code",
    }

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        archive.writestr(
            "products.json",
            json.dumps(json_payload, ensure_ascii=False, indent=2).encode("utf-8"),
        )
        archive.writestr("products.csv", csv_stream.getvalue().encode("utf-8-sig"))
        archive.writestr(
            "manifest.json",
            json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
        )
        for source_path, texture_name in texture_sources:
            archive.write(source_path, f"textures/{texture_name}")
    return zip_path

