from __future__ import annotations

import json
import os
import tempfile
from io import BytesIO
from pathlib import Path

from PIL import Image


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="hanall-api-") as temp:
        root = Path(temp)
        os.environ["HANALL_DATA_DIR"] = str(root / "data")
        os.environ["HANALL_ADMIN_TOKEN"] = "test-admin-token"

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
            status = client.get("/api/system/status")
            assert status.status_code == 200, status.text
            assert status.json()["gpu_vision_configured"] is False
            assert status.json()["catalog_versions"] == 1
            versions = client.get("/api/catalog/versions")
            assert versions.status_code == 200, versions.text
            assert versions.json()["items"][0]["job_id"] == job_id
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

            room = BytesIO()
            Image.new("RGB", (32, 24), "white").save(room, format="JPEG")
            vision = client.post(
                "/api/vision/segment",
                files={"image": ("room.jpg", room.getvalue(), "image/jpeg")},
            )
            assert vision.status_code == 503, vision.text

            admin_headers = {"X-Hanall-Admin-Token": "test-admin-token"}
            assert client.post("/api/admin/backup").status_code == 401
            backup = client.post("/api/admin/backup", headers=admin_headers)
            assert backup.status_code == 200, backup.text
            assert backup.headers["content-type"].startswith("application/zip")
            assert backup.content.startswith(b"PK")
            backup_listing = client.get("/api/admin/backups", headers=admin_headers)
            assert backup_listing.status_code == 200, backup_listing.text
            assert len(backup_listing.json()["items"]) == 1

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
