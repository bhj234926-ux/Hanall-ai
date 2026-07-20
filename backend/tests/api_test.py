from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="hanall-api-") as temp:
        root = Path(temp)
        os.environ["HANALL_DATA_DIR"] = str(root / "data")

        from fastapi.testclient import TestClient

        from app.main import app
        from tests.create_fixture_catalogs import create_fixtures

        fixtures = create_fixtures(root / "pdfs")
        with TestClient(app) as client:
            home = client.get("/")
            assert home.status_code == 200
            assert "HANALL AI" in home.text
            admin = client.get("/admin/catalogs")
            assert admin.status_code == 200
            assert "Catalog Extractor" in admin.text

            streams = [path.open("rb") for path in fixtures]
            try:
                files = [
                    ("files", (path.name, stream, "application/pdf"))
                    for path, stream in zip(fixtures, streams)
                ]
                metadata = [
                    {"brand": "개나리벽지", "collection": "ARTBOOK"},
                    {"brand": "개나리벽지", "collection": "STORY"},
                ]
                response = client.post("/api/jobs", files=files, data={"metadata": json.dumps(metadata, ensure_ascii=False)})
            finally:
                for stream in streams:
                    stream.close()
            assert response.status_code == 202, response.text
            job_id = response.json()["job_id"]
            job = client.get(f"/api/jobs/{job_id}").json()
            assert job["status"] == "complete", job
            assert job["duplicates_removed"] >= 1

            listing = client.get(f"/api/jobs/{job_id}/products?limit=100").json()
            assert len(listing["items"]) == 7, len(listing["items"])
            activation = client.post(f"/api/jobs/{job_id}/activate")
            assert activation.status_code == 200, activation.text
            active_library = client.get("/api/catalog/products").json()
            assert active_library["active_job_id"] == job_id
            assert len(active_library["items"]) == 7
            product = listing["items"][0]
            assert client.get(product["texture_url"]).status_code == 200
            assert client.get(product["page_url"]).status_code == 200

            update = client.patch(
                f"/api/jobs/{job_id}/products/{product['id']}",
                json={"product_code": product["product_code"], "brand": "개나리벽지", "status": "reviewed"},
            )
            assert update.status_code == 200, update.text
            crop = client.post(
                f"/api/jobs/{job_id}/products/{product['id']}/crop",
                json={"bbox": product["bbox"]},
            )
            assert crop.status_code == 200, crop.text
            assert crop.json()["method"] == "manual-crop"

            export = client.get(f"/api/jobs/{job_id}/export")
            assert export.status_code == 200, export.text
            assert export.headers["content-type"].startswith("application/zip")
            assert export.content.startswith(b"PK")
            print(
                f"API_OK job={job_id[:8]} products={len(listing['items'])} "
                f"duplicates={job['duplicates_removed']} zip_bytes={len(export.content)}"
            )


if __name__ == "__main__":
    main()
