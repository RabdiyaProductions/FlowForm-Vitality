import hashlib
import io
import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

import app_server


@pytest.fixture()
def content_pack_client(tmp_path, monkeypatch):
    db_path = tmp_path / "flowform.db"
    media_dir = tmp_path / "media"
    media_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setenv("ENABLE_AUTH", "false")
    monkeypatch.setattr(app_server, "MEDIA_DIR", media_dir)

    app = app_server.create_app(port=0)
    app.config["TESTING"] = True
    return app.test_client(), db_path, media_dir


def _insert_media(db_path: Path, media_dir: Path, original_name: str, content: bytes, mime: str) -> tuple[int, str]:
    sha256 = hashlib.sha256(content).hexdigest()
    stored_name = f"{sha256[:12]}{Path(original_name).suffix}"
    (media_dir / stored_name).write_bytes(content)

    with sqlite3.connect(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO media_item(user_id, original_name, stored_name, mime_type, size_bytes, sha256, tags, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (1, original_name, stored_name, mime, len(content), sha256, "test", "now", "now"),
        )
        conn.commit()
        return int(cursor.lastrowid), sha256


def _insert_template(db_path: Path, name: str, blocks: list[dict]) -> int:
    with sqlite3.connect(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO session_template(name, discipline, duration_minutes, level, json_blocks, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (name, "strength", 30, "all_levels", json.dumps({"blocks": blocks}), "now", "now"),
        )
        conn.commit()
        return int(cursor.lastrowid)


def test_content_pack_export_returns_valid_zip(content_pack_client):
    client, db_path, media_dir = content_pack_client
    media_id, sha256 = _insert_media(db_path, media_dir, "clip.mp4", b"video-bytes", "video/mp4")
    template_id = _insert_template(
        db_path,
        "Exportable",
        [{"title": "Block 1", "seconds": 30, "media_id": media_id}],
    )

    response = client.post("/content-packs/export", data={"template_id": str(template_id)})

    assert response.status_code == 200
    assert response.mimetype == "application/zip"

    zf = zipfile.ZipFile(io.BytesIO(response.data))
    names = set(zf.namelist())
    assert "content_pack.json" in names
    assert f"media/{sha256}.mp4" in names

    manifest = json.loads(zf.read("content_pack.json").decode("utf-8"))
    assert len(manifest["templates"]) == 1
    assert len(manifest["media"]) == 1
    block = manifest["templates"][0]["json_blocks"]["blocks"][0]
    assert block["media_sha256"] == sha256


def test_content_pack_import_adds_template_and_dedupes_media(content_pack_client):
    client, db_path, media_dir = content_pack_client

    media_bytes = b"shared-media"
    media_id, sha256 = _insert_media(db_path, media_dir, "shared.mp4", media_bytes, "video/mp4")

    with sqlite3.connect(db_path) as conn:
        templates_before = conn.execute("SELECT COUNT(*) FROM session_template").fetchone()[0]
        media_before = conn.execute("SELECT COUNT(*) FROM media_item").fetchone()[0]

    payload = {
        "templates": [
            {
                "name": "Imported Template",
                "discipline": "cardio",
                "duration_minutes": 20,
                "level": "all_levels",
                "json_blocks": {
                    "blocks": [
                        {"title": "Imported", "seconds": 20, "media_sha256": sha256}
                    ]
                },
            }
        ],
        "media": [
            {
                "source_media_id": 123,
                "sha256": sha256,
                "original_name": "shared.mp4",
                "stored_filename": "shared.mp4",
                "mime_type": "video/mp4",
                "size_bytes": len(media_bytes),
                "tags": "import",
            }
        ],
        "metadata": {"source": "test"},
    }

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("content_pack.json", json.dumps(payload))
        zf.writestr(f"media/{sha256}.mp4", media_bytes)
    zip_buffer.seek(0)

    response = client.post(
        "/content-packs/import",
        data={"pack_file": (zip_buffer, "content-pack.zip")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 302
    assert "message=Content+pack+imported." in response.headers["Location"]

    with sqlite3.connect(db_path) as conn:
        templates_after = conn.execute("SELECT COUNT(*) FROM session_template").fetchone()[0]
        media_after = conn.execute("SELECT COUNT(*) FROM media_item").fetchone()[0]
        imported_template = conn.execute(
            "SELECT json_blocks FROM session_template ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]

    assert templates_after == templates_before + 1
    assert media_after == media_before

    blocks = json.loads(imported_template)["blocks"]
    assert blocks[0]["media_id"] == media_id


def test_content_pack_import_rejects_invalid_zip(content_pack_client):
    client, _, _ = content_pack_client

    response = client.post(
        "/content-packs/import",
        data={"pack_file": (io.BytesIO(b"not-a-zip"), "bad.zip")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 302
    assert "error=Invalid+ZIP+file." in response.headers["Location"]
