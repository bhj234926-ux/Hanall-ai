from __future__ import annotations

import hashlib
import json
import math
import re
import statistics
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from PIL import Image, ImageStat

from .config import JPEG_QUALITY, RENDER_DPI

try:
    import fitz  # PyMuPDF
except ImportError as exc:  # pragma: no cover - handled by the web API at runtime
    fitz = None
    FITZ_IMPORT_ERROR = exc
else:
    FITZ_IMPORT_ERROR = None


DASHES = "‐‑‒–—−"
CODE_PATTERN = re.compile(
    r"(?<![A-Z0-9])(?:[A-Z]{1,6}[ -]?)?\d{4,8}(?:\s*[-/]\s*\d{1,4})?(?![A-Z0-9])",
    re.IGNORECASE,
)

KNOWN_BRANDS: list[tuple[tuple[str, ...], str]] = [
    (("개나리", "GAENARI"), "개나리벽지"),
    (("LX하우시스", "LX HAUSYS", "LX Z:IN", "LX지인"), "LX하우시스"),
    (("KCC글라스", "KCC GLASS", "KCC"), "KCC"),
    (("신한벽지", "SHINHAN WALLCOVERINGS"), "신한벽지"),
    (("현대L&C", "HYUNDAI L&C"), "현대L&C"),
    (("디앤메종", "DID WALLPAPER", "DID"), "디앤메종"),
]


@dataclass(slots=True)
class CodeAnchor:
    code: str
    bbox: tuple[float, float, float, float]
    text: str
    confidence: float

    @property
    def cx(self) -> float:
        return (self.bbox[0] + self.bbox[2]) / 2

    @property
    def cy(self) -> float:
        return (self.bbox[1] + self.bbox[3]) / 2

    @property
    def height(self) -> float:
        return self.bbox[3] - self.bbox[1]


@dataclass(slots=True)
class CropCandidate:
    bbox: tuple[float, float, float, float]
    method: str
    layout_score: float


@dataclass(slots=True)
class ExtractedProduct:
    product_code: str
    normalized_code: str
    brand: str
    catalog: str
    collection: str
    page_number: int
    source_pdf: str
    catalog_id: str
    texture_relpath: str
    page_image_relpath: str
    bbox: list[float]
    confidence: float
    method: str
    dominant_color: str
    width: int
    height: int
    image_hash: str


def normalize_code(value: str) -> str:
    value = value.upper().strip()
    for dash in DASHES:
        value = value.replace(dash, "-")
    value = re.sub(r"\s*([- /])\s*", r"\1", value)
    value = re.sub(r"\s+", "", value)
    return value


def valid_product_code(value: str, context: str = "") -> bool:
    code = normalize_code(value)
    if not re.fullmatch(r"(?:[A-Z]{1,6}-?)?\d{4,8}(?:[-/]\d{1,4})?", code):
        return False
    number_match = re.search(r"\d{4,8}", code)
    if not number_match:
        return False
    base = number_match.group(0)
    suffix = code[number_match.end():]
    # Dates are common in catalog headers and must not become products.
    if len(base) == 4 and 1900 <= int(base) <= 2100:
        return False
    # Bare four-digit numbers are normally years, dimensions, or page decorations.
    if len(base) == 4 and not suffix and not re.match(r"[A-Z]", code):
        return False
    upper_context = context.upper()
    if re.search(re.escape(code) + r"\s*(MM|CM|M²|M2|ML|KG)", normalize_code(upper_context)):
        return False
    return True


def slug(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z가-힣]+", "-", value).strip("-").lower()
    return cleaned or "catalog"


def infer_brand(text: str, filename: str, hint: str = "") -> str:
    if hint.strip():
        return hint.strip()
    haystack = f"{filename}\n{text}".upper()
    for tokens, brand in KNOWN_BRANDS:
        if any(token.upper() in haystack for token in tokens):
            return brand
    stem = Path(filename).stem
    return stem.split("_")[0].split("-")[0].strip() or "미지정"


def infer_collection(filename: str, hint: str = "") -> str:
    if hint.strip():
        return hint.strip()
    stem = Path(filename).stem
    stem = re.sub(r"^\d{8,}[_ -]*", "", stem).strip(" _-")
    return stem or "기본 컬렉션"


def extract_code_anchors(page: Any) -> list[CodeAnchor]:
    text_dict = page.get_text("dict")
    anchors: list[CodeAnchor] = []
    for block in text_dict.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = span.get("text", "")
                if not text:
                    continue
                normalized_text = text
                for dash in DASHES:
                    normalized_text = normalized_text.replace(dash, "-")
                x0, y0, x1, y1 = map(float, span["bbox"])
                char_width = max((x1 - x0) / max(len(normalized_text), 1), 0.1)
                for match in CODE_PATTERN.finditer(normalized_text):
                    raw = match.group(0)
                    if not valid_product_code(raw, normalized_text):
                        continue
                    code = normalize_code(raw)
                    bbox = (
                        x0 + match.start() * char_width,
                        y0,
                        min(x1, x0 + match.end() * char_width),
                        y1,
                    )
                    anchors.append(CodeAnchor(code, bbox, normalized_text, 0.96))

    # Remove duplicated text layers and tiny overlaps while preserving separate swatches.
    deduplicated: list[CodeAnchor] = []
    for anchor in sorted(anchors, key=lambda item: (item.cy, item.cx)):
        duplicate = any(
            existing.code == anchor.code
            and abs(existing.cx - anchor.cx) < 4
            and abs(existing.cy - anchor.cy) < 4
            for existing in deduplicated
        )
        if not duplicate:
            deduplicated.append(anchor)
    return deduplicated


def cluster_rows(anchors: list[CodeAnchor]) -> list[list[CodeAnchor]]:
    if not anchors:
        return []
    median_height = statistics.median(max(anchor.height, 2) for anchor in anchors)
    tolerance = max(8.0, median_height * 2.2)
    rows: list[list[CodeAnchor]] = []
    for anchor in sorted(anchors, key=lambda item: (item.cy, item.cx)):
        target = None
        for row in rows:
            row_y = statistics.mean(item.cy for item in row)
            if abs(row_y - anchor.cy) <= tolerance:
                target = row
                break
        if target is None:
            rows.append([anchor])
        else:
            target.append(anchor)
    for row in rows:
        row.sort(key=lambda item: item.cx)
    rows.sort(key=lambda row: statistics.mean(item.cy for item in row))
    return rows


def rect_tuple(rect: Any) -> tuple[float, float, float, float]:
    return (float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1))


def rect_area(bbox: tuple[float, float, float, float]) -> float:
    return max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])


def horizontal_overlap(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    overlap = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    return overlap / max(1.0, min(a[2] - a[0], b[2] - b[0]))


def candidate_rectangles(page: Any) -> tuple[list[tuple[float, float, float, float]], list[tuple[float, float, float, float]]]:
    page_box = rect_tuple(page.rect)
    page_area = rect_area(page_box)
    images: list[tuple[float, float, float, float]] = []
    vectors: list[tuple[float, float, float, float]] = []
    try:
        for info in page.get_image_info(xrefs=True):
            bbox = tuple(map(float, info.get("bbox", (0, 0, 0, 0))))
            width, height = bbox[2] - bbox[0], bbox[3] - bbox[1]
            if width >= 25 and height >= 25 and rect_area(bbox) <= page_area * 0.82:
                images.append(bbox)
    except Exception:
        pass
    try:
        for drawing in page.get_drawings():
            # A path made from a long diagonal line also has a rectangular bounding
            # box. Treating that bound as a swatch cell shifts the crop. Only actual
            # PDF rectangle operators ("re") are accepted as vector cells.
            for item in drawing.get("items", []):
                if not item or item[0] != "re":
                    continue
                bbox = rect_tuple(item[1])
                width, height = bbox[2] - bbox[0], bbox[3] - bbox[1]
                if width >= 25 and height >= 25 and rect_area(bbox) <= page_area * 0.45:
                    vectors.append(bbox)
    except Exception:
        pass
    return _unique_rects(images), _unique_rects(vectors)


def _unique_rects(rectangles: Iterable[tuple[float, float, float, float]]) -> list[tuple[float, float, float, float]]:
    result: list[tuple[float, float, float, float]] = []
    for bbox in rectangles:
        if any(sum(abs(a - b) for a, b in zip(bbox, old)) < 8 for old in result):
            continue
        result.append(bbox)
    return result


def relation_candidate(
    anchor: CodeAnchor,
    bbox: tuple[float, float, float, float],
    method: str,
    page_width: float,
    page_height: float,
) -> CropCandidate | None:
    width, height = bbox[2] - bbox[0], bbox[3] - bbox[1]
    overlap = horizontal_overlap(anchor.bbox, bbox)
    gap_above = anchor.bbox[1] - bbox[3]
    gap_below = bbox[1] - anchor.bbox[3]
    contains_x = bbox[0] <= anchor.cx <= bbox[2]
    layout_score = 0.0
    if bbox[3] <= anchor.bbox[1] + max(10.0, anchor.height) and (overlap > 0.25 or contains_x):
        if gap_above <= max(height * 0.65, 90):
            layout_score = 0.85 - min(max(gap_above, 0) / max(height, 1), 0.4)
    elif bbox[1] >= anchor.bbox[3] - 3 and (overlap > 0.4 or contains_x):
        if gap_below <= max(height * 0.25, 35):
            layout_score = 0.55 - min(max(gap_below, 0) / max(height, 1), 0.25)
    if layout_score <= 0:
        return None
    if width > page_width * 0.65 or height > page_height * 0.55:
        layout_score -= 0.22
    if method == "embedded-image":
        layout_score += 0.13
    return CropCandidate(bbox, method, min(1.0, max(0.0, layout_score)))


def geometry_candidate(
    anchor: CodeAnchor,
    rows: list[list[CodeAnchor]],
    page_width: float,
    page_height: float,
) -> CropCandidate:
    row_index = next(index for index, row in enumerate(rows) if anchor in row)
    row = rows[row_index]
    index = row.index(anchor)
    margin_x = page_width * 0.025
    if len(row) > 1:
        if index == 0:
            gap = row[1].cx - anchor.cx
            left = max(margin_x, anchor.cx - gap / 2)
        else:
            left = (row[index - 1].cx + anchor.cx) / 2
        if index == len(row) - 1:
            gap = anchor.cx - row[index - 1].cx
            right = min(page_width - margin_x, anchor.cx + gap / 2)
        else:
            right = (anchor.cx + row[index + 1].cx) / 2
    else:
        estimated = page_width * 0.28
        left = max(margin_x, anchor.cx - estimated / 2)
        right = min(page_width - margin_x, anchor.cx + estimated / 2)

    cell_width = max(45.0, right - left)
    bottom = anchor.bbox[1] - max(2.5, anchor.height * 0.2)
    if row_index > 0:
        previous_bottom = max(item.bbox[3] for item in rows[row_index - 1])
        top = previous_bottom + max(4.0, anchor.height * 0.35)
        if bottom - top < 35:
            top = bottom - min(cell_width * 0.9, page_height * 0.27)
    else:
        top = bottom - min(cell_width * 0.9, page_height * 0.29)
    top = max(page_height * 0.02, top)
    if bottom - top < 25:
        top = max(0, bottom - 60)
    return CropCandidate((left + 2, top + 2, right - 2, bottom), "grid-geometry", 0.52)


def render_page(page: Any) -> Image.Image:
    scale = RENDER_DPI / 72.0
    pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False, colorspace=fitz.csRGB)
    return Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)


def normalized_bbox(
    bbox: tuple[float, float, float, float], page_width: float, page_height: float
) -> list[float]:
    return [
        round(max(0.0, min(1.0, bbox[0] / page_width)), 6),
        round(max(0.0, min(1.0, bbox[1] / page_height)), 6),
        round(max(0.0, min(1.0, bbox[2] / page_width)), 6),
        round(max(0.0, min(1.0, bbox[3] / page_height)), 6),
    ]


def crop_from_pdf_bbox(
    page_image: Image.Image,
    bbox: tuple[float, float, float, float],
    page_width: float,
    page_height: float,
) -> Image.Image:
    x_scale = page_image.width / page_width
    y_scale = page_image.height / page_height
    x0 = max(0, int(round(bbox[0] * x_scale)))
    y0 = max(0, int(round(bbox[1] * y_scale)))
    x1 = min(page_image.width, int(round(bbox[2] * x_scale)))
    y1 = min(page_image.height, int(round(bbox[3] * y_scale)))
    if x1 - x0 < 8 or y1 - y0 < 8:
        raise ValueError("추출 영역이 너무 작습니다.")
    cropped = page_image.crop((x0, y0, x1, y1)).convert("RGB")
    # Inset a tiny amount to remove cell borders without discarding the material texture.
    inset_x = max(1, int(cropped.width * 0.012))
    inset_y = max(1, int(cropped.height * 0.012))
    if cropped.width > inset_x * 2 + 16 and cropped.height > inset_y * 2 + 16:
        cropped = cropped.crop((inset_x, inset_y, cropped.width - inset_x, cropped.height - inset_y))
    return cropped


def crop_quality(image: Image.Image) -> float:
    thumb = image.copy()
    thumb.thumbnail((160, 160))
    rgb = thumb.convert("RGB")
    pixels = list(rgb.getdata())
    if not pixels:
        return 0.0
    stats = ImageStat.Stat(rgb)
    channel_std = sum(stats.stddev) / max(len(stats.stddev), 1) / 70.0
    brightness = [(red + green + blue) / 3 for red, green, blue in pixels]
    white_fraction = sum(value > 248 for value in brightness) / len(brightness)
    dark_fraction = sum(value < 8 for value in brightness) / len(brightness)
    size_score = min(1.0, math.sqrt(image.width * image.height) / 240.0)
    content_score = max(0.0, 1.0 - white_fraction * 0.75 - dark_fraction * 0.6)
    return max(0.0, min(1.0, 0.30 * size_score + 0.35 * min(channel_std, 1.0) + 0.35 * content_score))


def dominant_color(image: Image.Image) -> str:
    thumb = image.copy().convert("RGB")
    thumb.thumbnail((80, 80))
    pixels = list(thumb.getdata())
    usable = [
        pixel for pixel in pixels
        if 12 < sum(pixel) / 3 < 248
    ]
    if len(usable) < 20:
        usable = pixels
    channels = [sorted(pixel[index] for pixel in usable) for index in range(3)]
    middle = len(usable) // 2
    rgb = tuple(channel[middle] for channel in channels)
    return "#{:02X}{:02X}{:02X}".format(*rgb)


def difference_hash(image: Image.Image) -> str:
    reduced = image.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
    pixels = list(reduced.getdata())
    bits = "".join(
        "1" if pixels[row * 9 + column + 1] > pixels[row * 9 + column] else "0"
        for row in range(8)
        for column in range(8)
    )
    return f"{int(bits, 2):016x}"


def choose_crop(
    anchor: CodeAnchor,
    rows: list[list[CodeAnchor]],
    page: Any,
    page_image: Image.Image,
    image_rects: list[tuple[float, float, float, float]],
    vector_rects: list[tuple[float, float, float, float]],
) -> tuple[CropCandidate, Image.Image, float]:
    page_width, page_height = float(page.rect.width), float(page.rect.height)
    candidates: list[CropCandidate] = []
    for bbox in image_rects:
        candidate = relation_candidate(anchor, bbox, "embedded-image", page_width, page_height)
        if candidate:
            candidates.append(candidate)
    for bbox in vector_rects:
        candidate = relation_candidate(anchor, bbox, "vector-cell", page_width, page_height)
        if candidate:
            candidates.append(candidate)
    candidates.append(geometry_candidate(anchor, rows, page_width, page_height))

    scored: list[tuple[float, CropCandidate, Image.Image, float]] = []
    for candidate in candidates:
        try:
            crop = crop_from_pdf_bbox(page_image, candidate.bbox, page_width, page_height)
        except ValueError:
            continue
        quality = crop_quality(crop)
        method_bonus = {"embedded-image": 0.10, "vector-cell": 0.05, "grid-geometry": 0.0}[candidate.method]
        total = candidate.layout_score * 0.68 + quality * 0.32 + method_bonus
        scored.append((total, candidate, crop, quality))
    if not scored:
        fallback = geometry_candidate(anchor, rows, page_width, page_height)
        crop = crop_from_pdf_bbox(page_image, fallback.bbox, page_width, page_height)
        return fallback, crop, 0.35
    scored.sort(key=lambda item: item[0], reverse=True)
    total, candidate, crop, quality = scored[0]
    confidence = max(0.25, min(0.99, anchor.confidence * 0.25 + total * 0.75))
    return candidate, crop, confidence


def _catalog_text(document: Any, pages: int = 5) -> str:
    chunks: list[str] = []
    for index in range(min(document.page_count, pages)):
        try:
            chunks.append(document[index].get_text("text"))
        except Exception:
            pass
    return "\n".join(chunks)


def catalog_page_count(pdf_path: Path) -> int:
    if fitz is None:
        raise RuntimeError(f"PyMuPDF를 불러올 수 없습니다: {FITZ_IMPORT_ERROR}")
    with fitz.open(pdf_path) as document:
        return document.page_count


def extract_catalog(
    *,
    pdf_path: Path,
    job_id: str,
    catalog_id: str,
    filename: str,
    brand_hint: str,
    collection_hint: str,
    output_root: Path,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> tuple[list[ExtractedProduct], dict[str, Any]]:
    if fitz is None:
        raise RuntimeError(f"PyMuPDF를 불러올 수 없습니다: {FITZ_IMPORT_ERROR}")

    textures_dir = output_root / "textures"
    pages_dir = output_root / "pages" / catalog_id
    textures_dir.mkdir(parents=True, exist_ok=True)
    pages_dir.mkdir(parents=True, exist_ok=True)

    products: list[ExtractedProduct] = []
    with fitz.open(pdf_path) as document:
        first_text = _catalog_text(document)
        brand = infer_brand(first_text, filename, brand_hint)
        collection = infer_collection(filename, collection_hint)
        catalog_name = collection
        total_pages = document.page_count

        for page_index in range(total_pages):
            page = document[page_index]
            anchors = extract_code_anchors(page)
            if progress_callback:
                progress_callback(page_index + 1, total_pages, f"{filename} {page_index + 1}/{total_pages}페이지")
            if not anchors:
                continue

            rows = cluster_rows(anchors)
            image_rects, vector_rects = candidate_rectangles(page)
            page_image = render_page(page)
            page_relpath = f"pages/{catalog_id}/page-{page_index + 1:04d}.jpg"
            page_path = output_root / page_relpath
            page_image.save(page_path, "JPEG", quality=JPEG_QUALITY, optimize=True)

            page_seen: set[tuple[str, int, int]] = set()
            for anchor in anchors:
                identity = (anchor.code, round(anchor.cx), round(anchor.cy))
                if identity in page_seen:
                    continue
                page_seen.add(identity)
                try:
                    candidate, crop, confidence = choose_crop(
                        anchor, rows, page, page_image, image_rects, vector_rects
                    )
                except Exception:
                    continue

                normalized = normalize_code(anchor.code)
                texture_name = (
                    f"{slug(brand)}__{slug(normalized)}__p{page_index + 1:04d}__"
                    f"{hashlib.sha1(f'{catalog_id}:{anchor.cx}:{anchor.cy}'.encode()).hexdigest()[:7]}.jpg"
                )
                texture_relpath = f"textures/{texture_name}"
                texture_path = output_root / texture_relpath
                crop.save(texture_path, "JPEG", quality=JPEG_QUALITY, optimize=True)
                bbox_norm = normalized_bbox(
                    candidate.bbox, float(page.rect.width), float(page.rect.height)
                )
                products.append(
                    ExtractedProduct(
                        product_code=anchor.code,
                        normalized_code=normalized,
                        brand=brand,
                        catalog=catalog_name,
                        collection=collection,
                        page_number=page_index + 1,
                        source_pdf=filename,
                        catalog_id=catalog_id,
                        texture_relpath=texture_relpath,
                        page_image_relpath=page_relpath,
                        bbox=bbox_norm,
                        confidence=round(confidence, 4),
                        method=candidate.method,
                        dominant_color=dominant_color(crop),
                        width=crop.width,
                        height=crop.height,
                        image_hash=difference_hash(crop),
                    )
                )

        return products, {
            "brand": brand,
            "collection": collection,
            "page_count": document.page_count,
            "detected_products": len(products),
        }


def product_to_db(product: ExtractedProduct, job_id: str) -> dict[str, Any]:
    brand_key = normalize_code(product.brand) if product.brand else "UNKNOWN"
    dedupe_key = f"{brand_key}|{product.normalized_code}"
    source = {
        "source_pdf": product.source_pdf,
        "page_number": product.page_number,
        "product_code": product.product_code,
        "catalog": product.catalog,
        "collection": product.collection,
    }
    return {
        "id": uuid.uuid4().hex,
        "job_id": job_id,
        "dedupe_key": dedupe_key,
        "product_code": product.product_code,
        "normalized_code": product.normalized_code,
        "brand": product.brand,
        "catalog": product.catalog,
        "collection": product.collection,
        "page_number": product.page_number,
        "source_pdf": product.source_pdf,
        "catalog_id": product.catalog_id,
        "texture_relpath": product.texture_relpath,
        "page_image_relpath": product.page_image_relpath,
        "bbox_json": json.dumps(product.bbox),
        "confidence": product.confidence,
        "status": "review" if product.confidence < 0.70 else "auto",
        "method": product.method,
        "dominant_color": product.dominant_color,
        "width": product.width,
        "height": product.height,
        "image_hash": product.image_hash,
        "sources_json": json.dumps([source], ensure_ascii=False),
    }


def recrop_page_image(
    page_image_path: Path,
    output_path: Path,
    bbox: list[float],
) -> dict[str, Any]:
    if len(bbox) != 4 or not (0 <= bbox[0] < bbox[2] <= 1 and 0 <= bbox[1] < bbox[3] <= 1):
        raise ValueError("크롭 영역은 0~1 범위의 [x0, y0, x1, y1]이어야 합니다.")
    with Image.open(page_image_path) as source:
        source = source.convert("RGB")
        x0 = round(bbox[0] * source.width)
        y0 = round(bbox[1] * source.height)
        x1 = round(bbox[2] * source.width)
        y1 = round(bbox[3] * source.height)
        if x1 - x0 < 12 or y1 - y0 < 12:
            raise ValueError("크롭 영역이 너무 작습니다.")
        crop = source.crop((x0, y0, x1, y1))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        crop.save(output_path, "JPEG", quality=JPEG_QUALITY, optimize=True)
        return {
            "bbox_json": json.dumps([round(float(item), 6) for item in bbox]),
            "dominant_color": dominant_color(crop),
            "width": crop.width,
            "height": crop.height,
            "image_hash": difference_hash(crop),
            "confidence": 1.0,
            "method": "manual-crop",
            "status": "reviewed",
        }
