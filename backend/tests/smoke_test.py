from __future__ import annotations

import json
import tempfile
import uuid
import zipfile
from pathlib import Path

from app import db
from app.db import create_job, get_all_products, init_db, insert_or_merge_product, update_job
from app.exporter import build_export
from app.extractor import extract_catalog, product_to_db
from tests.create_fixture_catalogs import create_fixtures


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="hanall-smoke-") as temp:
        root = Path(temp)
        db.DB_PATH = root / "test.sqlite3"
        init_db()
        pdfs = create_fixtures(root / "pdfs")
        job_id = uuid.uuid4().hex
        catalogs = []
        for path in pdfs:
            catalogs.append(
                {
                    "id": uuid.uuid4().hex,
                    "filename": path.name,
                    "stored_path": str(path),
                    "brand": "개나리벽지",
                    "collection": path.stem,
                }
            )
        create_job(job_id, catalogs)
        duplicates = 0
        for catalog in catalogs:
            extracted, _ = extract_catalog(
                pdf_path=Path(catalog["stored_path"]),
                job_id=job_id,
                catalog_id=catalog["id"],
                filename=catalog["filename"],
                brand_hint=catalog["brand"],
                collection_hint=catalog["collection"],
                output_root=root,
            )
            for product in extracted:
                duplicates += int(insert_or_merge_product(product_to_db(product, job_id)))
        products = get_all_products(job_id)
        codes = {product["normalized_code"] for product in products}
        expected = {"57231-1", "57231-2", "57231-3", "57232-1", "57232-2", "25120-1", "25120-2"}
        assert expected.issubset(codes), (expected - codes, codes)
        assert duplicates >= 1, duplicates
        assert all((root / product["texture_relpath"]).is_file() for product in products)
        update_job(job_id, status="complete", progress=1, products_total=len(products), duplicates_removed=duplicates)
        archive_path = build_export(job_id, root)
        assert archive_path.is_file()
        with zipfile.ZipFile(archive_path) as archive:
            names = set(archive.namelist())
            assert {"products.json", "products.csv", "manifest.json"}.issubset(names)
            assert any(name.startswith("textures/") for name in names)
            payload = json.loads(archive.read("products.json"))
            assert len(payload["products"]) == len(products)
        print(f"SMOKE_OK products={len(products)} duplicates={duplicates} zip={archive_path.name}")


if __name__ == "__main__":
    main()

