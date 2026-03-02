import json
import sqlite3
from pathlib import Path

import pytest

import app_server


@pytest.fixture()
def app_client(tmp_path, monkeypatch):
    db_path = tmp_path / "flowform.db"
    media_dir = tmp_path / "media"
    media_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setenv("ENABLE_AUTH", "false")
    monkeypatch.setattr(app_server, "MEDIA_DIR", media_dir)

    app = app_server.create_app(port=0)
    app.config["TESTING"] = True
    return app.test_client(), db_path


def _insert_media(db_path: Path, original_name: str = "demo.mp4") -> int:
    with sqlite3.connect(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO media_item(user_id, original_name, stored_name, mime_type, size_bytes, sha256, tags, created_at, updated_at)
            VALUES (1, ?, ?, 'video/mp4', 12, ?, 'test', 'now', 'now')
            """,
            (original_name, "stored.mp4", "a" * 64),
        )
        conn.commit()
        return int(cursor.lastrowid)


def _insert_template(db_path: Path) -> int:
    with sqlite3.connect(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO session_template(name, discipline, duration_minutes, level, json_blocks, created_at, updated_at)
            VALUES ('Original', 'strength', 30, 'all_levels', ?, 'now', 'now')
            """,
            (json.dumps({"blocks": [{"name": "Warmup", "minutes": 5, "media_id": None}]}),),
        )
        conn.commit()
        return int(cursor.lastrowid)


def test_template_edit_updates_json_blocks_with_media_id(app_client):
    client, db_path = app_client
    media_id = _insert_media(db_path)
    template_id = _insert_template(db_path)

    response = client.post(
        f"/templates/{template_id}/edit",
        data={
            "name": "Edited Template",
            "discipline": "cardio",
            "duration_minutes": "40",
            "level": "intermediate",
            "block_name": ["Main Set", "Cooldown"],
            "block_minutes": ["20", "10"],
            "block_media_id": [str(media_id), ""],
        },
    )

    assert response.status_code == 302
    assert f"/templates/{template_id}/edit?message=Template+saved." in response.headers["Location"]

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT name, discipline, duration_minutes, level, json_blocks FROM session_template WHERE id = ?",
            (template_id,),
        ).fetchone()

    assert row[0] == "Edited Template"
    assert row[1] == "cardio"
    assert row[2] == 40
    assert row[3] == "intermediate"

    payload = json.loads(row[4])
    assert payload["blocks"][0]["media_id"] == media_id
    assert payload["blocks"][1]["media_id"] is None


def test_session_start_includes_open_media_for_block_attachment(app_client):
    client, db_path = app_client
    media_id = _insert_media(db_path)

    with sqlite3.connect(db_path) as conn:
        template_id = conn.execute(
            """
            INSERT INTO session_template(name, discipline, duration_minutes, level, json_blocks, created_at, updated_at)
            VALUES ('Template with Media', 'strength', 30, 'all_levels', ?, 'now', 'now')
            """,
            (json.dumps({"blocks": [{"name": "Block A", "minutes": 5, "media_id": media_id}]}),),
        ).lastrowid
        plan_id = conn.execute(
            "INSERT INTO plan(user_id, name, start_date, weeks, status, created_at, updated_at) VALUES (1, 'P', '2026-01-01', 4, 'active', 'now', 'now')"
        ).lastrowid
        plan_day_id = conn.execute(
            "INSERT INTO plan_day(plan_id, week, day_index, template_id, title, created_at, updated_at) VALUES (?, 1, 1, ?, 'Day 1', 'now', 'now')",
            (plan_id, template_id),
        ).lastrowid
        conn.commit()

    response = client.get(f"/session/start/{plan_day_id}")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Open media" in html
    assert f'\"media_id\": {media_id}' in html
