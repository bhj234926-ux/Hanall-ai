from __future__ import annotations

import json
import re
import shutil
import uuid
from io import BytesIO
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .backup import create_catalog_backup, list_catalog_backups
from .config import ADMIN_TOKEN, BASE_DIR, DATA_DIR, MAX_FILE_BYTES, MAX_FILES, MAX_IMAGE_BYTES, ensure_data_dirs, job_dir
from .db import (
    create_job,
    get_active_job_id,
    get_all_products,
    get_catalogs,
    get_catalog_versions,
    get_job,
    get_product,
    init_db,
    insert_or_merge_product,
    list_products,
    summary_values,
    set_active_job,
    update_catalog,
    update_job,
    update_product,
)
from .exporter import build_export
from .extractor import (
    catalog_page_count,
    extract_catalog,
    normalize_code,
    product_to_db,
    recrop_page_image,
)
from .vision import VisionUnavailable, gpu_configured, segment_room


STATIC_DIR = BASE_DIR / "app" / "static"
app = FastAPI(
    title="HANALL AI Catalog Extractor",
    version="1.0.0",
    description="Multi-PDF interior material catalog extraction and review API",
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class ProductUpdate(BaseModel):
    product_code: str | None = Field(default=None, max_length=80)
    brand: str | None = Field(default=None, max_length=120)
    catalog: str | None = Field(default=None, max_length=180)
    collection: str | None = Field(default=None, max_length=180)
    status: str | None = None


class CropUpdate(BaseModel):
    bbox: list[float] = Field(min_length=4, max_length=4)


def require_admin(x_hanall_admin_token: str | None = Header(default=None)) -> None:
    if not ADMIN_TOKEN:
        raise HTTPException(503, "Admin backup access is not configured")
    if x_hanall_admin_token != ADMIN_TOKEN:
        raise HTTPException(401, "Invalid admin token")


def sanitize_filename(filename: str) -> str:
    name = Path(filename).name
    name = re.sub(r"[^0-9A-Za-z가-힣._ -]", "_", name).strip(" .")
    return name or "catalog.pdf"


def public_product(product: dict[str, Any], job_id: str) -> dict[str, Any]:
    product = dict(product)
    product["texture_url"] = f"/api/jobs/{job_id}/products/{product['id']}/texture"
    product["page_url"] = f"/api/jobs/{job_id}/products/{product['id']}/page"
    return product


@app.on_event("startup")
def startup() -> None:
    ensure_data_dirs()
    init_db()


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "simulator.html")


@app.get("/admin/catalogs", include_in_schema=False)
def catalog_admin() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "hanall-catalog-extractor"}


@app.get("/api/system/status")
def system_status() -> dict[str, Any]:
    return {
        "status": "ok",
        "gpu_vision_configured": gpu_configured(),
        "storage_mode": "persistent" if str(DATA_DIR).replace("\\", "/").startswith("/var/data") else "local-or-ephemeral",
        "catalog_versions": len(get_catalog_versions()),
    }


@app.get("/api/catalog/versions")
def catalog_versions() -> dict[str, Any]:
    return {"items": get_catalog_versions()}


@app.get("/api/admin/backups", dependencies=[Depends(require_admin)])
def backups() -> dict[str, Any]:
    return {"items": list_catalog_backups()}


@app.post("/api/admin/backup", dependencies=[Depends(require_admin)])
def create_backup() -> FileResponse:
    archive = create_catalog_backup()
    return FileResponse(archive, media_type="application/zip", filename=archive.name)


@app.post("/api/vision/segment")
async def segment_space(image: UploadFile = File(...)) -> dict[str, Any]:
    content_type = image.content_type or "application/octet-stream"
    if not content_type.startswith("image/"):
        raise HTTPException(400, "이미지 파일을 선택해 주세요.")
    payload = await image.read(MAX_IMAGE_BYTES + 1)
    if len(payload) > MAX_IMAGE_BYTES:
        raise HTTPException(413, "이미지 파일이 너무 큽니다.")
    try:
        with Image.open(BytesIO(payload)) as source:
            source.verify()
    except (UnidentifiedImageError, OSError) as exc:
        raise HTTPException(400, "올바른 이미지 파일이 아닙니다.") from exc
    try:
        return await run_in_threadpool(segment_room, payload, content_type)
    except VisionUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc


@app.post("/api/jobs", status_code=202)
async def create_extraction_job(
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
    metadata: str = Form("[]"),
) -> dict[str, Any]:
    if not files:
        raise HTTPException(400, "PDF 파일을 한 개 이상 선택해 주세요.")
    if len(files) > MAX_FILES:
        raise HTTPException(400, f"한 번에 최대 {MAX_FILES}개까지 업로드할 수 있습니다.")
    try:
        metadata_items = json.loads(metadata)
    except json.JSONDecodeError as exc:
        raise HTTPException(400, "카탈로그 메타데이터 형식이 올바르지 않습니다.") from exc
    if not isinstance(metadata_items, list):
        raise HTTPException(400, "카탈로그 메타데이터는 배열이어야 합니다.")

    job_id = uuid.uuid4().hex
    root = job_dir(job_id)
    source_dir = root / "source"
    source_dir.mkdir(parents=True, exist_ok=False)
    catalog_rows: list[dict[str, Any]] = []
    try:
        for index, upload in enumerate(files):
            original_name = upload.filename or f"catalog-{index + 1}.pdf"
            safe_name = sanitize_filename(original_name)
            if Path(safe_name).suffix.lower() != ".pdf":
                raise HTTPException(400, f"{original_name}: PDF 파일만 지원합니다.")
            catalog_id = uuid.uuid4().hex
            stored_name = f"{catalog_id}__{safe_name}"
            destination = source_dir / stored_name
            total = 0
            first_chunk = True
            with destination.open("wb") as target:
                while chunk := await upload.read(1024 * 1024):
                    if first_chunk:
                        first_chunk = False
                        if not chunk.startswith(b"%PDF-"):
                            raise HTTPException(400, f"{original_name}: 올바른 PDF 파일이 아닙니다.")
                    total += len(chunk)
                    if total > MAX_FILE_BYTES:
                        raise HTTPException(413, f"{original_name}: 파일 크기 제한을 초과했습니다.")
                    target.write(chunk)
            item_meta = metadata_items[index] if index < len(metadata_items) else {}
            catalog_rows.append(
                {
                    "id": catalog_id,
                    "filename": safe_name,
                    "stored_path": str(destination.resolve()),
                    "brand": str(item_meta.get("brand", "")).strip(),
                    "collection": str(item_meta.get("collection", "")).strip(),
                }
            )
    except Exception:
        shutil.rmtree(root, ignore_errors=True)
        raise

    create_job(job_id, catalog_rows)
    background_tasks.add_task(process_job, job_id)
    return {"job_id": job_id, "status": "queued"}


def process_job(job_id: str) -> None:
    root = job_dir(job_id)
    catalogs = get_catalogs(job_id)
    duplicates = 0
    product_count = 0
    processed_global = 0
    try:
        update_job(job_id, status="processing", progress=0.01, current_step="PDF 페이지 수 확인 중")
        total_pages = 0
        for catalog in catalogs:
            count = catalog_page_count(Path(catalog["stored_path"]))
            update_catalog(catalog["id"], page_count=count)
            total_pages += count
        update_job(job_id, total_pages=total_pages)

        for catalog_index, catalog in enumerate(catalogs):
            update_catalog(catalog["id"], status="processing")
            catalog_start = processed_global

            def progress(page: int, pages: int, step: str) -> None:
                current = catalog_start + page
                update_job(
                    job_id,
                    processed_pages=current,
                    progress=min(0.94, current / max(total_pages, 1) * 0.94),
                    current_step=step,
                )

            try:
                extracted, info = extract_catalog(
                    pdf_path=Path(catalog["stored_path"]),
                    job_id=job_id,
                    catalog_id=catalog["id"],
                    filename=catalog["filename"],
                    brand_hint=catalog["brand"],
                    collection_hint=catalog["collection"],
                    output_root=root,
                    progress_callback=progress,
                )
                update_catalog(
                    catalog["id"],
                    brand=info["brand"],
                    collection=info["collection"],
                    page_count=info["page_count"],
                    status="complete",
                )
                for product in extracted:
                    merged = insert_or_merge_product(product_to_db(product, job_id))
                    if merged:
                        duplicates += 1
                    else:
                        product_count += 1
                processed_global += info["page_count"]
            except Exception as exc:
                update_catalog(catalog["id"], status="failed", error=str(exc))
                processed_global += int(catalog.get("page_count") or 0)

        failed = [catalog for catalog in get_catalogs(job_id) if catalog["status"] == "failed"]
        if failed and len(failed) == len(catalogs):
            raise RuntimeError("모든 카탈로그 분석이 실패했습니다. 각 파일의 오류 내용을 확인해 주세요.")
        update_job(
            job_id,
            status="complete",
            progress=1.0,
            processed_pages=total_pages,
            products_total=product_count,
            duplicates_removed=duplicates,
            current_step=f"완료: 제품 {product_count}개, 중복 {duplicates}개 병합",
        )
    except Exception as exc:
        update_job(job_id, status="failed", error=str(exc), current_step="분석 실패")


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str) -> dict[str, Any]:
    job = get_job(job_id)
    if not job:
        raise HTTPException(404, "작업을 찾을 수 없습니다.")
    job["summary"] = summary_values(job_id)
    return job


@app.get("/api/jobs/{job_id}/products")
def products(
    job_id: str,
    search: str = "",
    brand: str = "",
    catalog: str = "",
    status: str = "",
    limit: int = 1000,
    offset: int = 0,
) -> dict[str, Any]:
    if not get_job(job_id):
        raise HTTPException(404, "작업을 찾을 수 없습니다.")
    rows = list_products(
        job_id,
        search=search,
        brand=brand,
        catalog=catalog,
        status=status,
        limit=min(max(limit, 1), 2000),
        offset=max(offset, 0),
    )
    return {"items": [public_product(row, job_id) for row in rows], "summary": summary_values(job_id)}


@app.post("/api/jobs/{job_id}/activate")
def activate_catalog(job_id: str) -> dict[str, Any]:
    if not get_job(job_id):
        raise HTTPException(404, "작업을 찾을 수 없습니다.")
    try:
        set_active_job(job_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    products = get_all_products(job_id)
    return {
        "active_job_id": job_id,
        "product_count": len(products),
        "message": f"제품 {len(products)}개를 HANALL AI 앱에 적용했습니다.",
    }


@app.get("/api/catalog/products")
def active_catalog_products() -> dict[str, Any]:
    active_job_id = get_active_job_id()
    if not active_job_id:
        return {"active_job_id": None, "items": [], "summary": {"total": 0}}
    products = get_all_products(active_job_id)
    return {
        "active_job_id": active_job_id,
        "items": [public_product(row, active_job_id) for row in products],
        "summary": summary_values(active_job_id),
    }


@app.patch("/api/jobs/{job_id}/products/{product_id}")
def edit_product(job_id: str, product_id: str, payload: ProductUpdate) -> dict[str, Any]:
    product = get_product(job_id, product_id)
    if not product:
        raise HTTPException(404, "제품을 찾을 수 없습니다.")
    changes = payload.model_dump(exclude_none=True)
    if "status" in changes and changes["status"] not in {"auto", "review", "reviewed", "excluded"}:
        raise HTTPException(400, "지원하지 않는 검수 상태입니다.")
    code = changes.get("product_code", product["product_code"]).strip()
    brand = changes.get("brand", product["brand"]).strip()
    normalized = normalize_code(code)
    changes["product_code"] = code
    changes["normalized_code"] = normalized
    changes["brand"] = brand
    changes["dedupe_key"] = f"{normalize_code(brand) if brand else 'UNKNOWN'}|{normalized}"
    if any(key in changes for key in ("product_code", "brand", "catalog", "collection")) and "status" not in changes:
        changes["status"] = "reviewed"
    update_product(job_id, product_id, changes)
    updated = get_product(job_id, product_id)
    return public_product(updated, job_id)  # type: ignore[arg-type]


@app.post("/api/jobs/{job_id}/products/{product_id}/crop")
def edit_crop(job_id: str, product_id: str, payload: CropUpdate) -> dict[str, Any]:
    product = get_product(job_id, product_id)
    if not product:
        raise HTTPException(404, "제품을 찾을 수 없습니다.")
    root = job_dir(job_id)
    page_path = (root / product["page_image_relpath"]).resolve()
    texture_path = (root / product["texture_relpath"]).resolve()
    if root.resolve() not in page_path.parents or not page_path.is_file():
        raise HTTPException(404, "원본 페이지 이미지를 찾을 수 없습니다.")
    try:
        changes = recrop_page_image(page_path, texture_path, payload.bbox)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    update_product(job_id, product_id, changes)
    updated = get_product(job_id, product_id)
    return public_product(updated, job_id)  # type: ignore[arg-type]


def product_file(job_id: str, product_id: str, field: str) -> Path:
    product = get_product(job_id, product_id)
    if not product:
        raise HTTPException(404, "제품을 찾을 수 없습니다.")
    root = job_dir(job_id).resolve()
    path = (root / product[field]).resolve()
    if root not in path.parents or not path.is_file():
        raise HTTPException(404, "이미지 파일을 찾을 수 없습니다.")
    return path


@app.get("/api/jobs/{job_id}/products/{product_id}/texture")
def texture(job_id: str, product_id: str) -> FileResponse:
    return FileResponse(product_file(job_id, product_id, "texture_relpath"), media_type="image/jpeg")


@app.get("/api/jobs/{job_id}/products/{product_id}/page")
def page_image(job_id: str, product_id: str) -> FileResponse:
    return FileResponse(product_file(job_id, product_id, "page_image_relpath"), media_type="image/jpeg")


@app.get("/api/jobs/{job_id}/export")
def export(job_id: str) -> FileResponse:
    job = get_job(job_id)
    if not job:
        raise HTTPException(404, "작업을 찾을 수 없습니다.")
    if job["status"] != "complete":
        raise HTTPException(409, "분석이 완료된 후 내보낼 수 있습니다.")
    products = get_all_products(job_id)
    if not products:
        raise HTTPException(409, "내보낼 제품이 없습니다.")
    zip_path = build_export(job_id, job_dir(job_id))
    return FileResponse(
        zip_path,
        media_type="application/zip",
        filename=zip_path.name,
    )
