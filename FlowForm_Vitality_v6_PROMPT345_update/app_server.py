from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import subprocess
import io
import zipfile
import tempfile
import shutil
import urllib.request
import urllib.error
import uuid
import mimetypes
from datetime import date, datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from functools import wraps

from flask import Flask, jsonify, make_response, redirect, render_template, request, send_file, url_for, session, abort
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

APP_NAME = "FlowForm Vitality Master Suite"
APP_VERSION = "0.1.5"
BUILD_DATE = "2026-03-02"

ROOT_DIR = Path(__file__).resolve().parent
DATA_DIR = ROOT_DIR / "data"
LOG_DIR = ROOT_DIR / "logs"
DEFAULT_DB_PATH = DATA_DIR / "flowform.db"
INSTANCE_DIR = ROOT_DIR / "instance"
MEDIA_DIR = INSTANCE_DIR / "media"

ALLOWED_MEDIA_EXTS = {
    ".mp4", ".mov", ".m4v", ".webm",
    ".mp3", ".wav", ".m4a", ".aac", ".ogg",
    ".jpg", ".jpeg", ".png", ".gif", ".webp",
    ".pdf",
}
MAX_UPLOAD_BYTES = 250 * 1024 * 1024


def _guess_mime(filename: str) -> str:
    mime, _ = mimetypes.guess_type(filename)
    return mime or "application/octet-stream"


def _allowed_ext(filename: str) -> bool:
    ext = Path(filename).suffix.lower()
    return ext in ALLOWED_MEDIA_EXTS


def _media_category(mime_type: str) -> str:
    mt = (mime_type or "").lower()
    if mt.startswith("image/"):
        return "image"
    if mt.startswith("video/"):
        return "video"
    if mt.startswith("audio/"):
        return "audio"
    if mt == "application/pdf":
        return "pdf"
    return "file"

DISCIPLINES = ["strength", "cardio", "mobility", "recovery", "conditioning", "endurance"]
GOAL_DEFAULTS = {
    "strength": ["strength", "mobility", "recovery", "conditioning", "cardio"],
    "fat_loss": ["conditioning", "cardio", "strength", "mobility", "recovery"],
    "mobility": ["mobility", "recovery", "strength", "cardio", "conditioning"],
    "stress": ["recovery", "mobility", "cardio", "strength", "conditioning"],
    "hybrid": ["strength", "cardio", "mobility", "conditioning", "recovery"],
}

DISCIPLINES = ["strength", "cardio", "mobility", "recovery", "conditioning", "endurance"]
GOAL_DEFAULTS = {
    "strength": ["strength", "mobility", "recovery", "conditioning", "cardio"],
    "fat_loss": ["conditioning", "cardio", "strength", "mobility", "recovery"],
    "mobility": ["mobility", "recovery", "strength", "cardio", "conditioning"],
    "stress": ["recovery", "mobility", "cardio", "strength", "conditioning"],
    "hybrid": ["strength", "cardio", "mobility", "conditioning", "recovery"],
}


def load_env_file(env_path: Path) -> None:
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def configure_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    root_logger = logging.getLogger()
    if root_logger.handlers:
        return

    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    root_logger.setLevel(level)

    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")

    console = logging.StreamHandler()
    console.setFormatter(formatter)

    file_handler = RotatingFileHandler(LOG_DIR / "flowform.log", maxBytes=1_000_000, backupCount=3)
    file_handler.setFormatter(formatter)

    root_logger.addHandler(console)
    root_logger.addHandler(file_handler)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def git_hash() -> str:
    """Return git short hash if repo has .git, else a stable sentinel."""
    try:
        if not (ROOT_DIR / '.git').exists():
            return 'not_a_git_repo'
        return (
            subprocess.check_output(
                ['git', 'rev-parse', '--short', 'HEAD'],
                cwd=str(ROOT_DIR),
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
        )
    except Exception:
        return 'unknown'


def provider_status() -> str:
    return 'configured' if (os.getenv('OPENAI_API_KEY') or os.getenv('PROVIDER_API_KEY')) else 'not_configured'



def first_check_state(app: Flask) -> dict:
    state = app.config.setdefault("FIRST_CHECK", {"ok": False, "message": "FIRST_CHECK missing"})
    if not isinstance(state, dict):
        state = {"ok": False, "message": "FIRST_CHECK malformed"}
        app.config["FIRST_CHECK"] = state
    state.setdefault("ok", False)
    state.setdefault("message", "")
    return state


def is_api_request() -> bool:
    return request.path.startswith('/api/')

def column_exists(connection: sqlite3.Connection, table: str, column: str) -> bool:
    rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row[1] == column for row in rows)


def ensure_column(connection: sqlite3.Connection, table: str, column: str, definition_sql: str) -> None:
    if not column_exists(connection, table, column):
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {definition_sql}")


def apply_schema_migrations(connection: sqlite3.Connection) -> None:
    now = utc_now_iso()

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            email TEXT,
            display_name TEXT
        )
        """
    )
    ensure_column(connection, "users", "created_at", "created_at TEXT NOT NULL DEFAULT ''")
    ensure_column(connection, "users", "updated_at", "updated_at TEXT NOT NULL DEFAULT ''")
    ensure_column(connection, "users", "password_hash", "password_hash TEXT")
    ensure_column(connection, "users", "role", "role TEXT NOT NULL DEFAULT 'member'")
    ensure_column(connection, "users", "enabled", "enabled INTEGER NOT NULL DEFAULT 1")

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS profile (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            goal TEXT,
            days_per_week INTEGER,
            minutes INTEGER,
            equipment TEXT,
            constraints TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """
    )

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS plan (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            name TEXT NOT NULL,
            start_date TEXT,
            weeks INTEGER NOT NULL DEFAULT 4,
            status TEXT NOT NULL DEFAULT 'draft',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """
    )

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS session_template (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            discipline TEXT NOT NULL,
            duration_minutes INTEGER NOT NULL,
            level TEXT NOT NULL,
            json_blocks TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS plan_day (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_id INTEGER NOT NULL,
            week INTEGER NOT NULL,
            day_index INTEGER NOT NULL,
            template_id INTEGER,
            title TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(plan_id) REFERENCES plan(id),
            FOREIGN KEY(template_id) REFERENCES session_template(id)
        )
        """
    )

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS session_completion (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_day_id INTEGER NOT NULL,
            completed_at TEXT NOT NULL,
            rpe INTEGER,
            notes TEXT,
            minutes_done INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(plan_day_id) REFERENCES plan_day(id)
        )
        """
    )

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS session_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            title TEXT NOT NULL,
            category TEXT NOT NULL,
            intensity TEXT NOT NULL,
            duration_minutes INTEGER NOT NULL,
            training_load REAL NOT NULL,
            notes TEXT,
            media_id INTEGER,
            created_at TEXT NOT NULL,
            completed_at TEXT,
            updated_at TEXT NOT NULL
        )
        """
    )

    # Backfill schema for older DBs that predate media support.
    ensure_column(connection, "session_log", "media_id", "media_id INTEGER")

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS media_item (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            original_name TEXT NOT NULL,
            stored_name TEXT NOT NULL,
            mime_type TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            tags TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """
    )

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS session_metric (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            heart_rate_avg INTEGER,
            calories INTEGER,
            perceived_exertion INTEGER,
            created_at TEXT NOT NULL,
            FOREIGN KEY(session_id) REFERENCES session_log(id)
        )
        """
    )

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS recovery_checkin (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            date TEXT NOT NULL,
            sleep_hours REAL,
            stress_1_10 INTEGER,
            soreness_1_10 INTEGER,
            mood_1_10 INTEGER,
            notes TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """
    )

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            plan TEXT NOT NULL DEFAULT 'free',
            status TEXT NOT NULL DEFAULT 'active',
            start_date TEXT,
            end_date TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """
    )

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS assistant_message (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """
    )

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS _healthcheck (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            checked_at TEXT NOT NULL
        )
        """
    )
    connection.execute("INSERT INTO _healthcheck(checked_at) VALUES (?)", (now,))


def seed_templates(connection: sqlite3.Connection) -> None:
    existing_count = connection.execute("SELECT COUNT(*) FROM session_template").fetchone()[0]
    if existing_count > 0:
        return

    now = utc_now_iso()
    templates = [
        ("Strength Foundation A", "strength", 45, "beginner", '{"blocks":[{"name":"warmup","minutes":8},{"name":"compound_lifts","minutes":28},{"name":"cooldown","minutes":9}]}'),
        ("Strength Progression B", "strength", 60, "intermediate", '{"blocks":[{"name":"warmup","minutes":10},{"name":"main_lifts","minutes":38},{"name":"accessory","minutes":8},{"name":"cooldown","minutes":4}]}'),
        ("Zone 2 Base Ride", "cardio", 50, "beginner", '{"blocks":[{"name":"warmup","minutes":10},{"name":"steady_state","minutes":35},{"name":"cooldown","minutes":5}]}'),
        ("Tempo Intervals Run", "cardio", 40, "intermediate", '{"blocks":[{"name":"warmup","minutes":8},{"name":"tempo_intervals","minutes":26},{"name":"cooldown","minutes":6}]}'),
        ("Mobility Restore", "mobility", 30, "all_levels", '{"blocks":[{"name":"breath","minutes":5},{"name":"hips_spine","minutes":20},{"name":"reset","minutes":5}]}'),
        ("Yoga Recovery Flow", "recovery", 35, "all_levels", '{"blocks":[{"name":"flow","minutes":25},{"name":"downregulate","minutes":10}]}'),
        ("HIIT Power Ladder", "conditioning", 32, "advanced", '{"blocks":[{"name":"warmup","minutes":6},{"name":"ladder","minutes":20},{"name":"cooldown","minutes":6}]}'),
        ("Endurance Long Session", "endurance", 75, "intermediate", '{"blocks":[{"name":"warmup","minutes":10},{"name":"steady_endurance","minutes":55},{"name":"cooldown","minutes":10}]}'),
    ]

    connection.executemany(
        """
        INSERT INTO session_template (name, discipline, duration_minutes, level, json_blocks, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [(name, discipline, duration, level, blocks, now, now) for name, discipline, duration, level, blocks in templates],
    )


def get_or_create_founder_user(connection: sqlite3.Connection) -> int:
    row = connection.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()
    if row:
        return int(row[0])

    now = utc_now_iso()
    cursor = connection.execute(
        """
        INSERT INTO users (email, display_name, created_at, updated_at)
        VALUES (?, ?, ?, ?)
        """,
        ("founder@flowform.local", "Founder", now, now),
    )
    return int(cursor.lastrowid)


def ensure_subscription_row(connection: sqlite3.Connection, user_id: int) -> None:
    row = connection.execute("SELECT id FROM subscriptions WHERE user_id = ? ORDER BY id DESC LIMIT 1", (user_id,)).fetchone()
    if row:
        return
    now = utc_now_iso()
    connection.execute(
        """
        INSERT INTO subscriptions (user_id, plan, status, start_date, end_date, created_at, updated_at)
        VALUES (?, 'free', 'active', ?, NULL, ?, ?)
        """,
        (user_id, date.today().isoformat(), now, now),
    )


def user_subscription(connection: sqlite3.Connection, user_id: int) -> dict:
    connection.row_factory = sqlite3.Row
    row = connection.execute(
        """
        SELECT plan, status, start_date, end_date
        FROM subscriptions
        WHERE user_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (user_id,),
    ).fetchone()
    if row is None:
        ensure_subscription_row(connection, user_id)
        row = connection.execute(
            "SELECT plan, status, start_date, end_date FROM subscriptions WHERE user_id = ? ORDER BY id DESC LIMIT 1",
            (user_id,),
        ).fetchone()
    return dict(row) if row else {"plan": "free", "status": "active"}


def has_paid_access(connection: sqlite3.Connection, user_id: int) -> bool:
    sub = user_subscription(connection, user_id)
    return sub.get("plan") == "paid" and sub.get("status") == "active"


def db_integrity_snapshot(db_path: Path) -> dict:
    required_tables = {
        "users",
        "profile",
        "plan",
        "plan_day",
        "session_template",
        "session_completion",
        "recovery_checkin",
        "audit_log",
    }

    try:
        connection = sqlite3.connect(db_path)
        existing_tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        missing_tables = sorted(required_tables - existing_tables)
        template_count = connection.execute("SELECT COUNT(*) FROM session_template").fetchone()[0]
        connection.execute("SELECT 1")
        connection.close()

        db_ok = not missing_tables and template_count > 0
        return {"db_ok": db_ok, "template_count": int(template_count), "missing_tables": missing_tables}
    except sqlite3.Error as exc:
        return {
            "db_ok": False,
            "template_count": 0,
            "missing_tables": sorted(required_tables),
            "error": str(exc),
        }


def clamp_int(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def preferred_disciplines(payload: dict) -> list[str]:
    ranked = []
    if payload.get("disciplines") and isinstance(payload.get("disciplines"), list):
        ranked = [str(item).strip().lower() for item in payload["disciplines"] if str(item).strip()]
    else:
        ranked = [
            str(payload.get(f"discipline_rank_{idx}", "")).strip().lower()
            for idx in range(1, 6)
            if str(payload.get(f"discipline_rank_{idx}", "")).strip()
        ]

    goal = str(payload.get("goal", "hybrid")).strip().lower().replace(" ", "_")
    defaults = GOAL_DEFAULTS.get(goal, GOAL_DEFAULTS["hybrid"])

    deduped = []
    for item in ranked + defaults:
        if item in DISCIPLINES and item not in deduped:
            deduped.append(item)
    return deduped or GOAL_DEFAULTS["hybrid"]


def fetch_template_pool(connection: sqlite3.Connection, ordered_disciplines: list[str], target_minutes: int, limit_templates: int | None = None) -> list[dict]:
    rows = connection.execute(
        """
        SELECT id, name, discipline, duration_minutes, level
        FROM session_template
        ORDER BY id ASC
        """
    ).fetchall()
    if not rows:
        return []

    priority = {discipline: idx for idx, discipline in enumerate(ordered_disciplines)}

    mapped = [
        {
            "id": int(row[0]),
            "name": row[1],
            "discipline": row[2],
            "duration": int(row[3]),
            "level": row[4],
        }
        for row in rows
    ]

    mapped.sort(
        key=lambda item: (
            priority.get(item["discipline"], 999),
            abs(item["duration"] - target_minutes),
            item["id"],
        )
    )
    if limit_templates is not None:
        return mapped[: max(1, int(limit_templates))]
    return mapped


def choose_template_for_day(pool: list[dict], discipline: str, target_minutes: int, offset: int) -> dict:
    discipline_pool = [item for item in pool if item["discipline"] == discipline]
    candidate_pool = discipline_pool if discipline_pool else pool
    ranked = sorted(candidate_pool, key=lambda item: (abs(item["duration"] - target_minutes), item["id"]))
    return ranked[offset % len(ranked)]


def build_plan_structure(
    pool: list[dict],
    ordered_disciplines: list[str],
    days_per_week: int,
    minutes_per_session: int,
    weeks: int,
) -> list[dict]:
    items: list[dict] = []
    for week in range(1, weeks + 1):
        # Progressive structure: weeks 1-3 build, week 4 deload.
        if week <= 2:
            target = minutes_per_session
        elif week == 3:
            target = clamp_int(minutes_per_session + 5, 30, 75)
        else:
            target = clamp_int(minutes_per_session - 5, 30, 75)

        for day_index in range(1, days_per_week + 1):
            discipline = ordered_disciplines[(day_index - 1) % len(ordered_disciplines)]
            choice = choose_template_for_day(pool, discipline, target, offset=week + day_index)
            items.append(
                {
                    "week": week,
                    "day_index": day_index,
                    "template_id": choice["id"],
                    "title": f"Week {week} Day {day_index}: {choice['name']}",
                }
            )
    return items


def current_plan_record(connection: sqlite3.Connection, user_id: int) -> sqlite3.Row | None:
    connection.row_factory = sqlite3.Row
    row = connection.execute(
        """
        SELECT id, user_id, name, start_date, weeks, status
        FROM plan
        WHERE user_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (user_id,),
    ).fetchone()
    return row


def write_audit(connection: sqlite3.Connection, event: str, payload: dict) -> None:
    now = utc_now_iso()
    connection.execute(
        """
        INSERT INTO audit_log (event, payload_json, created_at, updated_at)
        VALUES (?, ?, ?, ?)
        """,
        (event, json.dumps(payload), now, now),
    )



def blocks_from_json(raw: str) -> list[dict]:
    """
    Parse a session template's JSON payload into a list of normalized block dictionaries.

    Each block in the returned list will always contain the following keys:
      - name (str): the display title for the block
      - minutes (int): duration in minutes (>= 0)
      - seconds (int): duration in seconds (minutes * 60)
      - media_id (int | None): optional ID of a media_item attached to this block

    If parsing fails or the JSON structure doesn't match expectations, an empty list is returned.
    """
    try:
        payload = json.loads(raw)
    except Exception:
        return []
    # We expect a top-level dict with a "blocks" list.
    blocks = payload.get("blocks") if isinstance(payload, dict) else None
    if not isinstance(blocks, list):
        return []

    normalized: list[dict] = []
    for idx, block in enumerate(blocks, start=1):
        # Skip any non-dict block definitions.
        if not isinstance(block, dict):
            continue
        name = str(block.get("name", f"Block {idx}")).strip() or f"Block {idx}"
        minutes = block.get("minutes", 0)
        try:
            minutes_int = max(0, int(minutes))
        except (TypeError, ValueError):
            minutes_int = 0
        # Carry forward media_id if present and numeric; else None.
        media_id_val: int | None = None
        raw_media = block.get("media_id")
        if isinstance(raw_media, int):
            media_id_val = raw_media
        elif isinstance(raw_media, str) and raw_media.isdigit():
            media_id_val = int(raw_media)
        normalized.append({
            "name": name,
            "minutes": minutes_int,
            "seconds": minutes_int * 60,
            "media_id": media_id_val,
        })
    return normalized

def compute_readiness_score(sleep_hours: float, stress: int, soreness: int, mood: int) -> tuple[int, str]:
    # Explainable weighted score out of 100.
    sleep_component = max(0.0, min(1.0, sleep_hours / 8.0)) * 40.0
    stress_component = ((11 - stress) / 10.0) * 20.0
    soreness_component = ((11 - soreness) / 10.0) * 20.0
    mood_component = (mood / 10.0) * 20.0
    score = int(round(max(0.0, min(100.0, sleep_component + stress_component + soreness_component + mood_component))))

    explanation = (
        f"sleep({sleep_hours}h)->{sleep_component:.1f}/40, "
        f"stress({stress})->{stress_component:.1f}/20, "
        f"soreness({soreness})->{soreness_component:.1f}/20, "
        f"mood({mood})->{mood_component:.1f}/20"
    )
    return score, explanation


def readiness_label(score: int) -> str:
    if score >= 75:
        return "high"
    if score >= 55:
        return "moderate"
    return "low"


def suggestion_for_low_readiness(connection: sqlite3.Connection, minutes: int | None = None) -> dict | None:
    target = int(minutes or 35)
    row = connection.execute(
        """
        SELECT id, name, discipline, duration_minutes
        FROM session_template
        WHERE discipline IN ('mobility', 'recovery')
        ORDER BY ABS(duration_minutes - ?), duration_minutes ASC
        LIMIT 1
        """,
        (target,),
    ).fetchone()
    if row is None:
        return None
    return {"id": int(row[0]), "name": row[1], "discipline": row[2], "duration_minutes": int(row[3])}



def analytics_snapshot(connection: sqlite3.Connection, user_id: int) -> dict:
    # Distinct completion dates for streak math.
    completion_dates = [
        row[0]
        for row in connection.execute(
            """
            SELECT DISTINCT DATE(completed_at) AS day
            FROM session_completion
            ORDER BY day DESC
            """
        ).fetchall()
        if row[0]
    ]

    streak = 0
    if completion_dates:
        today = date.today()
        date_set = {date.fromisoformat(item) for item in completion_dates}
        cursor = today
        while cursor in date_set:
            streak += 1
            cursor -= timedelta(days=1)

    # Weekly completion rate for current week of active plan.
    plan = current_plan_record(connection, user_id)
    weekly_completion_rate = 0
    if plan is not None and plan["start_date"]:
        start = date.fromisoformat(plan["start_date"])
        elapsed = max(0, (date.today() - start).days)
        current_week = min(int(plan["weeks"]), (elapsed // 7) + 1)

        totals = connection.execute(
            "SELECT COUNT(*) FROM plan_day WHERE plan_id = ? AND week = ?",
            (int(plan["id"]), current_week),
        ).fetchone()[0]
        completed = connection.execute(
            """
            SELECT COUNT(DISTINCT pd.id)
            FROM plan_day pd
            JOIN session_completion sc ON sc.plan_day_id = pd.id
            WHERE pd.plan_id = ? AND pd.week = ?
            """,
            (int(plan["id"]), current_week),
        ).fetchone()[0]
        if totals > 0:
            weekly_completion_rate = int(round((completed / totals) * 100))

    avg_rpe = {}
    for days in (7, 14, 30):
        row = connection.execute(
            """
            SELECT AVG(rpe)
            FROM session_completion
            WHERE completed_at >= datetime('now', ?)
            """,
            (f"-{days} days",),
        ).fetchone()
        avg_rpe[str(days)] = round(float(row[0]), 2) if row and row[0] is not None else None

    readiness_rows = connection.execute(
        """
        SELECT date, sleep_hours, stress_1_10, soreness_1_10, mood_1_10
        FROM recovery_checkin
        WHERE user_id = ?
        ORDER BY date DESC
        LIMIT 14
        """,
        (user_id,),
    ).fetchall()

    readiness_trend = []
    for row in reversed(readiness_rows):
        score, _ = compute_readiness_score(
            float(row[1] or 0),
            int(row[2] or 5),
            int(row[3] or 5),
            int(row[4] or 5),
        )
        readiness_trend.append({"date": row[0], "score": score})

    # Card takeaways
    streak_takeaway = "Excellent momentum — keep the chain alive today." if streak >= 3 else "Start or restart the streak with one focused session today."
    weekly_takeaway = (
        "On track this week — maintain your rhythm." if weekly_completion_rate >= 70
        else "Below target this week — schedule one catch-up session."
    )

    rpe_7 = avg_rpe.get("7")
    if rpe_7 is None:
        rpe_takeaway = "No recent RPE data yet — finish a session to start insights."
    elif rpe_7 >= 8:
        rpe_takeaway = "Recent effort is high — consider extra recovery emphasis."
    elif rpe_7 <= 5:
        rpe_takeaway = "Recent effort is moderate/low — safe room to push when ready."
    else:
        rpe_takeaway = "Recent effort is balanced — keep progressive overload steady."

    if readiness_trend:
        latest = readiness_trend[-1]["score"]
        readiness_takeaway = "Recovery trend is low — choose a lighter session suggestion today." if latest < 55 else "Recovery trend is supportive — proceed with planned intensity."
    else:
        readiness_takeaway = "No readiness trend yet — add daily recovery check-ins."

    return {
        "streak": streak,
        "weekly_completion_rate": weekly_completion_rate,
        "avg_rpe": avg_rpe,
        "readiness_trend": readiness_trend,
        "takeaways": {
            "streak": streak_takeaway,
            "weekly": weekly_takeaway,
            "rpe": rpe_takeaway,
            "readiness": readiness_takeaway,
        },
    }




def assistant_history(connection: sqlite3.Connection, user_id: int, limit: int = 20) -> list[dict]:
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        """
        SELECT role, content, created_at
        FROM assistant_message
        WHERE user_id = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (user_id, int(limit)),
    ).fetchall()
    history = []
    for row in reversed(rows):
        history.append({"role": row["role"], "content": row["content"], "created_at": row["created_at"]})
    return history


def save_assistant_message(connection: sqlite3.Connection, user_id: int, role: str, content: str) -> None:
    now = utc_now_iso()
    connection.execute(
        """
        INSERT INTO assistant_message (user_id, role, content, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (user_id, role, content, now),
    )


def build_coach_context(connection: sqlite3.Connection, user_id: int) -> dict:
    # Lightweight, explainable context. No medical claims.
    ctx: dict = {}

    # Recovery: latest check-in.
    connection.row_factory = sqlite3.Row
    latest = connection.execute(
        """
        SELECT date, sleep_hours, stress_1_10, soreness_1_10, mood_1_10
        FROM recovery_checkin
        WHERE user_id = ?
        ORDER BY date DESC
        LIMIT 1
        """,
        (user_id,),
    ).fetchone()
    if latest:
        score, explanation = compute_readiness_score(
            float(latest["sleep_hours"] or 0),
            int(latest["stress_1_10"] or 5),
            int(latest["soreness_1_10"] or 5),
            int(latest["mood_1_10"] or 5),
        )
        ctx["readiness"] = {
            "date": latest["date"],
            "score": score,
            "label": readiness_label(score),
            "explanation": explanation,
        }

    # Plan: current plan high-level.
    plan = current_plan_record(connection, user_id)
    if plan is not None:
        ctx["plan"] = {
            "id": int(plan["id"]),
            "name": plan["name"],
            "start_date": plan["start_date"],
            "weeks": int(plan["weeks"]),
            "status": plan["status"],
        }

    # Analytics snapshot for trends.
    try:
        ctx["analytics"] = analytics_snapshot(connection, user_id)
    except Exception:
        ctx["analytics"] = {}

    return ctx


def coach_fallback_response(message: str, context: dict) -> str:
    # Simple rules-engine coach. Keep it practical and safe.
    msg = (message or "").strip().lower()
    readiness = (context.get("readiness") or {})
    score = int(readiness.get("score", 60))
    label = readiness.get("label", "moderate")

    disclaimer = (
        "I'm a digital coach, not a healthcare professional. "
        "If you have severe symptoms or injury concerns, consider seeking medical advice.\n\n"
    )

    # Injury/symptom escalation keywords.
    red_flags = ["chest pain", "faint", "shortness of breath", "severe", "bleeding", "numb", "dizzy", "fracture"]
    if any(k in msg for k in red_flags):
        return disclaimer + (
            "That sounds potentially serious. Please stop and consider getting professional medical advice. "
            "If symptoms are severe or sudden, seek urgent care."
        )

    # Recovery guidance.
    if "recovery" in msg or "sore" in msg or "sleep" in msg or "stress" in msg:
        if label == "low" or score < 55:
            return disclaimer + (
                f"Your readiness looks **low** (score {score}). Today, prioritise recovery:\n"
                "- 20–35 min mobility + easy breathwork\n"
                "- Light walk (Zone 1/2) if you want movement\n"
                "- Hydration + earlier bedtime\n\n"
                "If you still want training, keep it low impact and stop if pain increases."
            )
        return disclaimer + (
            f"Your readiness is **{label}** (score {score}). A solid recovery protocol today:\n"
            "- 10 min breath downregulation\n"
            "- 20–30 min mobility (hips/spine/ankles)\n"
            "- 5 min easy stretch + warm shower\n"
        )

    # Plan tweaks.
    if "plan" in msg or "tweak" in msg or "adjust" in msg:
        if label == "low" or score < 55:
            return disclaimer + (
                f"Plan tweak suggestion (readiness {score}): swap today's session to Mobility Restore or Yoga Recovery Flow. "
                "Keep the next strength session, but reduce volume by ~20% this week."
            )
        return disclaimer + (
            "Plan tweak suggestion: keep your structure, but rotate disciplines to avoid overload. "
            "If two high-intensity days are back-to-back, insert a mobility/recovery day between them."
        )

    # Substitution.
    if "substitute" in msg or "instead" in msg or "swap" in msg:
        if label == "low" or score < 55:
            return disclaimer + (
                "Substitution: swap to a 30–40 min recovery session (mobility + breathwork). "
                "Avoid HIIT or heavy compound lifts today."
            )
        return disclaimer + (
            "Substitution: keep the same discipline but reduce impact. Example: intervals -> Zone 2, heavy strength -> technique + lighter sets."
        )

    # Motivation / default.
    if "motivate" in msg or "motivation" in msg or "struggling" in msg:
        return disclaimer + (
            "Two-step approach:\n"
            "1) Do a 5-minute warmup only. If you still feel flat, stop and recover.\n"
            "2) If you feel okay after warmup, complete the first block only. Momentum usually follows."
        )

    return disclaimer + (
        "Tell me what you want today: plan tweak, substitution, recovery, or motivation — and how you feel (sleep/stress/soreness/mood)."
    )


def try_openai_response(prompt: str, context: dict, history: list[dict]) -> str | None:
    api_key = (os.getenv('OPENAI_API_KEY') or '').strip()
    if not api_key:
        return None

    model = (os.getenv('OPENAI_MODEL') or 'gpt-4o-mini').strip()
    base = (os.getenv('OPENAI_API_BASE') or 'https://api.openai.com').rstrip('/')
    url = f"{base}/v1/chat/completions"

    system = (
        "You are FlowForm Vitality Coach. You provide practical fitness/wellbeing guidance, "
        "but you do NOT provide medical advice or diagnosis. "
        "Be concise, actionable, and explain reasoning briefly. "
        "If severe symptoms or injury red-flags are mentioned, advise seeking professional care."
    )

    messages = [{"role": "system", "content": system}]
    # Provide context as a short JSON blob.
    messages.append({"role": "system", "content": "Context JSON:\n" + json.dumps(context, indent=2)})

    # Add last few turns.
    for m in history[-8:]:
        role = 'assistant' if m.get('role') != 'user' else 'user'
        messages.append({"role": role, "content": m.get('content','')})

    messages.append({"role": "user", "content": prompt})

    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.4,
        "max_tokens": 400,
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode('utf-8'),
        headers={
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {api_key}',
        },
        method='POST',
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode('utf-8')
        data = json.loads(raw)
        choice = (data.get('choices') or [{}])[0]
        msg = (choice.get('message') or {}).get('content')
        if msg and isinstance(msg, str):
            return msg.strip()
    except Exception:
        return None

    return None
def export_snapshot(connection: sqlite3.Connection, user_id: int) -> dict:
    connection.row_factory = sqlite3.Row
    plan = current_plan_record(connection, user_id)

    profile = connection.execute(
        """
        SELECT goal, days_per_week, minutes, equipment, constraints, created_at, updated_at
        FROM profile
        WHERE user_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (user_id,),
    ).fetchone()

    plan_days = []
    completions = []
    if plan is not None:
        plan_days = [
            dict(row)
            for row in connection.execute(
                """
                SELECT pd.id, pd.plan_id, pd.week, pd.day_index, pd.title, pd.created_at, pd.updated_at,
                       st.id AS template_id, st.name AS template_name, st.discipline, st.duration_minutes
                FROM plan_day pd
                LEFT JOIN session_template st ON st.id = pd.template_id
                WHERE pd.plan_id = ?
                ORDER BY pd.week ASC, pd.day_index ASC
                """,
                (int(plan["id"]),),
            ).fetchall()
        ]

        completions = [
            dict(row)
            for row in connection.execute(
                """
                SELECT sc.id, sc.plan_day_id, sc.completed_at, sc.rpe, sc.notes, sc.minutes_done, sc.created_at, sc.updated_at
                FROM session_completion sc
                JOIN plan_day pd ON pd.id = sc.plan_day_id
                WHERE pd.plan_id = ?
                ORDER BY sc.completed_at DESC
                """,
                (int(plan["id"]),),
            ).fetchall()
        ]

    templates = [
        dict(row)
        for row in connection.execute(
            """
            SELECT id, name, discipline, duration_minutes, level, json_blocks, created_at, updated_at
            FROM session_template
            ORDER BY id ASC
            """
        ).fetchall()
    ]

    recovery = [
        dict(row)
        for row in connection.execute(
            """
            SELECT id, date, sleep_hours, stress_1_10, soreness_1_10, mood_1_10, notes, created_at, updated_at
            FROM recovery_checkin
            WHERE user_id = ?
            ORDER BY date DESC
            """,
            (user_id,),
        ).fetchall()
    ]

    return {
        "exported_at": utc_now_iso(),
        "app": {"name": APP_NAME, "version": APP_VERSION},
        "user": {"id": user_id},
        "profile": dict(profile) if profile else None,
        "plan": dict(plan) if plan else None,
        "plan_days": plan_days,
        "templates": templates,
        "completions": completions,
        "recovery": recovery,
    }


def render_plan_export_html(payload: dict) -> str:
    plan = payload.get("plan") or {}
    profile = payload.get("profile") or {}
    days = payload.get("plan_days") or []
    completions = {item["plan_day_id"]: item for item in (payload.get("completions") or [])}

    rows = []
    for item in days:
        status = "Completed" if item["id"] in completions else "Pending"
        rows.append(
            f"<tr><td>{item['week']}</td><td>{item['day_index']}</td><td>{item.get('title','')}</td><td>{item.get('template_name','')}</td><td>{item.get('discipline','')}</td><td>{item.get('duration_minutes','')}</td><td>{status}</td></tr>"
        )

    rows_html = "".join(rows) if rows else '<tr><td colspan="7">No plan days available.</td></tr>'

    return f"""<!doctype html>
<html><head><meta charset='utf-8'><title>FlowForm Plan Export</title>
<style>body{{font-family:Arial,sans-serif;margin:24px}}table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #ccc;padding:8px;text-align:left}}</style>
</head><body>
<h1>FlowForm Plan Export</h1>
<p><strong>Generated:</strong> {payload.get('exported_at','')}</p>
<p><strong>Plan:</strong> {plan.get('name','N/A')} | Start: {plan.get('start_date','N/A')} | Weeks: {plan.get('weeks','N/A')}</p>
<p><strong>Profile:</strong> Goal {profile.get('goal','N/A')} · Days/week {profile.get('days_per_week','N/A')} · Minutes {profile.get('minutes','N/A')}</p>
<p><strong>Safety disclaimer:</strong> Training guidance is informational and not medical advice.</p>
<h2>Plan days</h2>
<table><thead><tr><th>Week</th><th>Day</th><th>Title</th><th>Template</th><th>Discipline</th><th>Minutes</th><th>Status</th></tr></thead><tbody>{rows_html}</tbody></table>
</body></html>
"""


def _pdf_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def build_simple_pdf(lines: list[str], title: str = "FlowForm Export") -> bytes:
    safe_lines = [title, ""] + [str(line)[:180] for line in lines]
    y = 780
    content_lines = ["BT", "/F1 12 Tf"]
    for idx, line in enumerate(safe_lines):
        if idx == 0:
            content_lines.append(f"72 {y} Td ({_pdf_escape(line)}) Tj")
        else:
            content_lines.append("0 -16 Td")
            content_lines.append(f"({_pdf_escape(line)}) Tj")
    content_lines.append("ET")
    content = "\n".join(content_lines).encode("latin-1", errors="replace")

    objects: list[bytes] = []
    objects.append(b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n")
    objects.append(b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n")
    objects.append(b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >> endobj\n")
    objects.append(b"4 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n")
    objects.append(f"5 0 obj << /Length {len(content)} >> stream\n".encode("latin-1") + content + b"\nendstream endobj\n")

    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for obj in objects:
        offsets.append(len(out))
        out.extend(obj)
    xref_pos = len(out)
    out.extend(f"xref\n0 {len(objects) + 1}\n".encode("latin-1"))
    out.extend(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        out.extend(f"{off:010d} 00000 n \n".encode("latin-1"))
    out.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_pos}\n%%EOF\n"
        ).encode("latin-1")
    )
    return bytes(out)


def backup_manifest(connection: sqlite3.Connection) -> dict:
    counts = {
        "plans": connection.execute("SELECT COUNT(*) FROM plan").fetchone()[0],
        "plan_days": connection.execute("SELECT COUNT(*) FROM plan_day").fetchone()[0],
        "templates": connection.execute("SELECT COUNT(*) FROM session_template").fetchone()[0],
        "completions": connection.execute("SELECT COUNT(*) FROM session_completion").fetchone()[0],
        "recovery": connection.execute("SELECT COUNT(*) FROM recovery_checkin").fetchone()[0],
    }
    media_count = 0
    if MEDIA_DIR.exists():
        media_count = sum(1 for p in MEDIA_DIR.iterdir() if p.is_file())
    return {
        "created_at": utc_now_iso(),
        "counts": counts,
        "media_files": media_count,
        "warning": "Restoring this backup overwrites current database and media files.",
    }

def create_app(port: int | None = None) -> Flask:
    load_env_file(ROOT_DIR / ".env")
    configure_logging()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES

    resolved_port = int(port if port is not None else os.getenv("PORT", "5410"))
    db_path = Path(os.getenv("DB_PATH", str(DEFAULT_DB_PATH))).resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    app.config.update(
        APP_NAME=APP_NAME,
        VERSION=APP_VERSION,
        PORT=resolved_port,
        DB_PATH=str(db_path),
        BUILD_DATE=BUILD_DATE,
        GIT_HASH=git_hash(),
        FIRST_CHECK={"ok": True, "message": ""},
        ENABLE_AUTH=str(os.getenv("ENABLE_AUTH", "false")).lower() == "true",
    )
    app.secret_key = os.getenv("SECRET_KEY", "flowform-dev-secret")

    app.logger.info("FlowForm boot config: port=%s db=%s", resolved_port, db_path)

    def auth_enabled() -> bool:
        return bool(app.config.get("ENABLE_AUTH", False))

    def current_user_id(connection: sqlite3.Connection) -> int:
        if not auth_enabled():
            return get_or_create_founder_user(connection)
        user_id = session.get("user_id")
        if not user_id:
            return 0
        row = connection.execute("SELECT id, enabled FROM users WHERE id = ?", (int(user_id),)).fetchone()
        if not row or int(row[1]) == 0:
            session.clear()
            return 0
        return int(row[0])

    def require_login(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not auth_enabled():
                return view(*args, **kwargs)
            connection = sqlite3.connect(db_path)
            uid = current_user_id(connection)
            connection.close()
            if uid <= 0:
                if is_api_request():
                    return jsonify({"error": "auth_required"}), 401
                return redirect(url_for("login_page"))
            return view(*args, **kwargs)

        return wrapped

    @app.context_processor
    def inject_auth_flags():
        return {
            "auth_enabled": auth_enabled(),
            "current_session_user_id": session.get("user_id"),
        }

    def init_db_safely() -> dict:
        try:
            connection = sqlite3.connect(db_path)
            apply_schema_migrations(connection)
            get_or_create_founder_user(connection)
            seed_templates(connection)
            connection.commit()
            connection.close()
            return {"ok": True, "message": "db_ready"}
        except sqlite3.Error as exc:
            app.logger.warning("SQLite init degraded: %s", exc)
            return {"ok": False, "message": f"SQLite init degraded: {exc}"}

    def init_db_safely() -> dict:
        try:
            connection = sqlite3.connect(db_path)
            apply_schema_migrations(connection)
            get_or_create_founder_user(connection)
            seed_templates(connection)
            connection.commit()
            connection.close()
            return {"ok": True, "message": "db_ready"}
        except sqlite3.Error as exc:
            app.logger.warning("SQLite init degraded: %s", exc)
            return {"ok": False, "message": f"SQLite init degraded: {exc}"}

    app.config["FIRST_CHECK"] = init_db_safely()

    @app.errorhandler(404)
    def handle_not_found(_: Exception):
        if is_api_request():
            return jsonify({"error": "not_found"}), 404
        return render_template("friendly_error.html", code=404, title="Page not found", message="The page you requested does not exist."), 404

    @app.errorhandler(500)
    def handle_server_error(error: Exception):
        app.logger.exception("Unhandled server error: %s", error)
        if is_api_request():
            return jsonify({"error": "internal_server_error"}), 500
        return render_template(
            "friendly_error.html",
            code=500,
            title="Something went wrong",
            message="An unexpected error occurred. Please retry or return to Ready.",
        ), 500

    @app.get("/")
    def root():
        check = first_check_state(app)
        if not check.get("ok", False):
            return render_template("first_run_error.html", error_message=check.get("message", "Unknown startup check failure")), 500
        if auth_enabled() and not session.get("user_id"):
            return redirect(url_for("login_page"))
        return render_template("ready.html")

    @app.get("/signup")
    def signup_page():
        if not auth_enabled():
            return redirect(url_for("ready"))
        return render_template("signup.html")

    @app.post("/signup")
    def signup_submit():
        if not auth_enabled():
            return redirect(url_for("ready"))
        email = str(request.form.get("email", "")).strip().lower()
        password = str(request.form.get("password", "")).strip()
        display_name = str(request.form.get("display_name", "")).strip() or "User"
        if not email or not password:
            return render_template("signup.html", error="Email and password are required."), 400

        connection = sqlite3.connect(db_path)
        exists = connection.execute("SELECT id FROM users WHERE lower(email) = ?", (email,)).fetchone()
        if exists:
            connection.close()
            return render_template("signup.html", error="Email already registered."), 400
        now = utc_now_iso()
        cursor = connection.execute(
            """
            INSERT INTO users (email, display_name, password_hash, role, enabled, created_at, updated_at)
            VALUES (?, ?, ?, 'member', 1, ?, ?)
            """,
            (email, display_name, generate_password_hash(password), now, now),
        )
        user_id = int(cursor.lastrowid)
        ensure_subscription_row(connection, user_id)
        connection.commit()
        connection.close()
        session["user_id"] = user_id
        return redirect(url_for("ready"))

    @app.get("/login")
    def login_page():
        if not auth_enabled():
            return redirect(url_for("ready"))
        return render_template("login.html")

    @app.post("/login")
    def login_submit():
        if not auth_enabled():
            return redirect(url_for("ready"))
        email = str(request.form.get("email", "")).strip().lower()
        password = str(request.form.get("password", "")).strip()
        connection = sqlite3.connect(db_path)
        row = connection.execute(
            "SELECT id, password_hash, enabled FROM users WHERE lower(email) = ?",
            (email,),
        ).fetchone()
        connection.close()
        if (not row) or int(row[2]) == 0 or not check_password_hash(str(row[1] or ""), password):
            return render_template("login.html", error="Invalid credentials."), 401
        session["user_id"] = int(row[0])
        return redirect(url_for("ready"))

    @app.get("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login_page") if auth_enabled() else url_for("ready"))

    @app.get("/health")
    def health():
        snapshot = db_integrity_snapshot(db_path)
        return jsonify(
            {
                "status": "ok" if snapshot["db_ok"] else "degraded",
                "version": app.config["VERSION"],
                "time": utc_now_iso(),
                "db_ok": snapshot["db_ok"],
                "template_count": snapshot["template_count"],
                "provider_status": provider_status(),
            }
        )

    @app.get("/api/health")
    def api_health():
        snapshot = db_integrity_snapshot(db_path)
        is_db_ok = snapshot["db_ok"] and bool(first_check_state(app).get("ok", False))
        return jsonify(
            {
                "status": "ok" if is_db_ok else "degraded",
                "port": app.config["PORT"],
                "db_ok": is_db_ok,
                "template_count": snapshot["template_count"],
                "version": app.config["VERSION"],
            }
        )

    @app.get("/version")
    def version():
        return jsonify(
            {
                "app_name": app.config["APP_NAME"],
                "semantic_version": app.config["VERSION"],
                "build_date": app.config["BUILD_DATE"],
                "git_hash": app.config["GIT_HASH"],
            }
        )

    @app.get("/plan/wizard")
    @require_login
    def plan_wizard():
        return render_template("plan_wizard.html", disciplines=DISCIPLINES)

    @app.post("/api/plan/create")
    @require_login
    def api_plan_create():
        payload = request.get_json(silent=True) or request.form.to_dict()

        goal_raw = str(payload.get("goal", "hybrid")).strip().lower().replace(" ", "_")
        goal = goal_raw if goal_raw in GOAL_DEFAULTS else "hybrid"
        days_per_week = clamp_int(int(payload.get("days_per_week", 4)), 2, 6)
        minutes_per_session = clamp_int(int(payload.get("minutes_per_session", 50)), 30, 75)

        ordered_disciplines = preferred_disciplines(payload)
        injury_flags = str(payload.get("injury_flags", "")).strip()
        equipment = str(payload.get("equipment", "")).strip()
        extra_constraints = str(payload.get("constraints", "")).strip()
        combined_constraints = "; ".join(part for part in [injury_flags, extra_constraints] if part)

        now = utc_now_iso()
        today = date.today().isoformat()

        connection = sqlite3.connect(db_path)
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            user_id = current_user_id(connection)
            if user_id <= 0:
                raise sqlite3.IntegrityError("auth_required")
            sub = user_subscription(connection, user_id)
            is_paid = sub.get("plan") == "paid" and sub.get("status") == "active"
            if not is_paid:
                existing_plans = connection.execute(
                    "SELECT COUNT(*) FROM plan WHERE user_id = ? AND status != 'archived'",
                    (user_id,),
                ).fetchone()[0]
                if int(existing_plans) >= 1:
                    connection.close()
                    return jsonify({
                        "ok": False,
                        "error": "free_tier_limit_reached",
                        "message": "Free tier allows 1 active plan. Upgrade for unlimited plans.",
                        "benefits": ["unlimited_plans", "priority_support", "early_access_ai"],
                        "pay_now_link": None,
                    }), 403
            profile_row = connection.execute(
                "SELECT id FROM profile WHERE user_id = ? ORDER BY id DESC LIMIT 1",
                (user_id,),
            ).fetchone()
            if profile_row:
                connection.execute(
                    """
                    UPDATE profile
                    SET goal = ?, days_per_week = ?, minutes = ?, equipment = ?, constraints = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (goal, days_per_week, minutes_per_session, equipment, combined_constraints, now, profile_row[0]),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO profile (user_id, goal, days_per_week, minutes, equipment, constraints, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (user_id, goal, days_per_week, minutes_per_session, equipment, combined_constraints, now, now),
                )

            connection.execute(
                "UPDATE plan SET status = 'archived', updated_at = ? WHERE user_id = ? AND status = 'active'",
                (now, user_id),
            )

            plan_name = f"{goal.replace('_', ' ').title()} 4-Week Plan"
            cursor = connection.execute(
                """
                INSERT INTO plan (user_id, name, start_date, weeks, status, created_at, updated_at)
                VALUES (?, ?, ?, 4, 'active', ?, ?)
                """,
                (user_id, plan_name, today, now, now),
            )
            plan_id = int(cursor.lastrowid)

            pool = fetch_template_pool(connection, ordered_disciplines, minutes_per_session, None if is_paid else 3)
            if not pool:
                raise sqlite3.IntegrityError("session_template empty; cannot generate plan")
            items = build_plan_structure(pool, ordered_disciplines, days_per_week, minutes_per_session, weeks=4)

            connection.executemany(
                """
                INSERT INTO plan_day (plan_id, week, day_index, template_id, title, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (plan_id, item["week"], item["day_index"], item["template_id"], item["title"], now, now)
                    for item in items
                ],
            )

            write_audit(
                connection,
                "plan_created",
                {
                    "plan_id": plan_id,
                    "goal": goal,
                    "days_per_week": days_per_week,
                    "minutes_per_session": minutes_per_session,
                    "disciplines": ordered_disciplines,
                },
            )

            connection.commit()
        except (sqlite3.Error, ValueError) as exc:
            connection.rollback()
            connection.close()
            return jsonify({"ok": False, "error": str(exc)}), 400
        connection.close()

        if request.is_json:
            return jsonify({"ok": True, "plan_id": plan_id, "redirect": "/plan/current"})
        return redirect(url_for("plan_current"))

    @app.post("/api/plan/regenerate-next-week")
    @require_login
    def api_plan_regenerate_next_week():
        connection = sqlite3.connect(db_path)
        connection.execute("PRAGMA foreign_keys = ON")
        now = utc_now_iso()
        try:
            user_id = current_user_id(connection)
            row = current_plan_record(connection, user_id)
            if row is None:
                raise sqlite3.IntegrityError("No plan found")

            plan_id = int(row["id"])
            total_weeks = int(row["weeks"])
            start = date.fromisoformat(row["start_date"])
            elapsed = max(0, (date.today() - start).days)
            current_week = min(total_weeks, (elapsed // 7) + 1)
            next_week = min(total_weeks, current_week + 1)

            profile = connection.execute(
                "SELECT goal, days_per_week, minutes FROM profile WHERE user_id = ? ORDER BY id DESC LIMIT 1",
                (user_id,),
            ).fetchone()
            days_per_week = clamp_int(int(profile[1]) if profile and profile[1] else 4, 2, 6)
            minutes_per_session = clamp_int(int(profile[2]) if profile and profile[2] else 50, 30, 75)
            goal = (profile[0] if profile and profile[0] else "hybrid").strip().lower().replace(" ", "_")
            ordered_disciplines = GOAL_DEFAULTS.get(goal, GOAL_DEFAULTS["hybrid"])

            completed_day_ids = {
                int(item[0])
                for item in connection.execute(
                    """
                    SELECT sc.plan_day_id
                    FROM session_completion sc
                    JOIN plan_day pd ON pd.id = sc.plan_day_id
                    WHERE pd.plan_id = ? AND pd.week = ?
                    """,
                    (plan_id, next_week),
                ).fetchall()
            }

            existing_rows = connection.execute(
                "SELECT id FROM plan_day WHERE plan_id = ? AND week = ?",
                (plan_id, next_week),
            ).fetchall()
            for item in existing_rows:
                plan_day_id = int(item[0])
                if plan_day_id not in completed_day_ids:
                    connection.execute("DELETE FROM plan_day WHERE id = ?", (plan_day_id,))

            pool = fetch_template_pool(connection, ordered_disciplines, minutes_per_session)
            week_items = [
                item
                for item in build_plan_structure(pool, ordered_disciplines, days_per_week, minutes_per_session, weeks=next_week)
                if item["week"] == next_week
            ]

            existing_day_indexes = {
                int(row[0])
                for row in connection.execute(
                    "SELECT day_index FROM plan_day WHERE plan_id = ? AND week = ?",
                    (plan_id, next_week),
                ).fetchall()
            }

            for item in week_items:
                if item["day_index"] in existing_day_indexes:
                    continue
                connection.execute(
                    """
                    INSERT INTO plan_day (plan_id, week, day_index, template_id, title, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (plan_id, item["week"], item["day_index"], item["template_id"], item["title"], now, now),
                )

            write_audit(connection, "plan_week_regenerated", {"plan_id": plan_id, "week": next_week})
            connection.commit()
            payload = {"ok": True, "plan_id": plan_id, "week": next_week}
        except (sqlite3.Error, ValueError) as exc:
            connection.rollback()
            payload = {"ok": False, "error": str(exc)}
        connection.close()

        if request.is_json:
            return jsonify(payload), (200 if payload.get("ok") else 400)
        return redirect(url_for("plan_current"))

    @app.get("/dashboard")
    @require_login
    def dashboard():
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        user_id = current_user_id(connection)

        today = date.today()
        week_start = today - timedelta(days=today.weekday())
        month_start = today.replace(day=1)

        view = (request.args.get("view") or "week").strip().lower()
        if view not in {"week", "month"}:
            view = "week"
        period_start = week_start if view == "week" else month_start
        start_iso = period_start.isoformat()

        # Manual (standalone) completed sessions
        manual_stats = connection.execute(
            """
            SELECT
                COALESCE(COUNT(*), 0) AS sessions,
                COALESCE(SUM(duration_minutes), 0) AS minutes,
                COALESCE(SUM(training_load), 0) AS load
            FROM session_log
            WHERE user_id = ?
              AND completed_at IS NOT NULL
              AND DATE(completed_at) >= ?
            """,
            (user_id, start_iso),
        ).fetchone()
        manual_sessions = int(manual_stats["sessions"] or 0)
        manual_minutes = int(manual_stats["minutes"] or 0)
        manual_load = float(manual_stats["load"] or 0.0)

        manual_rpe_row = connection.execute(
            """
            SELECT COALESCE(SUM(m.perceived_exertion), 0) AS sum_rpe,
                   COALESCE(COUNT(m.perceived_exertion), 0) AS cnt
            FROM session_metric m
            JOIN session_log s ON s.id = m.session_id
            WHERE s.user_id = ?
              AND s.completed_at IS NOT NULL
              AND DATE(s.completed_at) >= ?
              AND m.perceived_exertion IS NOT NULL
            """,
            (user_id, start_iso),
        ).fetchone()
        manual_rpe_sum = int(manual_rpe_row["sum_rpe"] or 0)
        manual_rpe_cnt = int(manual_rpe_row["cnt"] or 0)

        # Plan completions
        plan_stats = connection.execute(
            """
            SELECT
                COALESCE(COUNT(*), 0) AS sessions,
                COALESCE(SUM(COALESCE(sc.minutes_done, st.duration_minutes)), 0) AS minutes,
                COALESCE(SUM(COALESCE(sc.minutes_done, st.duration_minutes)), 0) AS load,
                COALESCE(SUM(sc.rpe), 0) AS sum_rpe,
                COALESCE(COUNT(sc.rpe), 0) AS cnt_rpe
            FROM session_completion sc
            JOIN plan_day pd ON pd.id = sc.plan_day_id
            JOIN plan p ON p.id = pd.plan_id
            LEFT JOIN session_template st ON st.id = pd.template_id
            WHERE p.user_id = ?
              AND DATE(sc.completed_at) >= ?
            """,
            (user_id, start_iso),
        ).fetchone()
        plan_sessions = int(plan_stats["sessions"] or 0)
        plan_minutes = int(plan_stats["minutes"] or 0)
        plan_load = float(plan_stats["load"] or 0.0)
        plan_rpe_sum = int(plan_stats["sum_rpe"] or 0)
        plan_rpe_cnt = int(plan_stats["cnt_rpe"] or 0)

        # Combined period metrics
        period_sessions = manual_sessions + plan_sessions
        period_minutes = manual_minutes + plan_minutes
        period_load = manual_load + plan_load

        total_rpe_sum = manual_rpe_sum + plan_rpe_sum
        total_rpe_cnt = manual_rpe_cnt + plan_rpe_cnt
        avg_rpe = round(total_rpe_sum / total_rpe_cnt, 1) if total_rpe_cnt > 0 else None

        # Weekly + monthly totals for quick comparison (to-date)
        def minutes_since(start_date: date) -> int:
            start_str = start_date.isoformat()
            m1 = connection.execute(
                """
                SELECT COALESCE(SUM(duration_minutes), 0) AS v
                FROM session_log
                WHERE user_id = ?
                  AND completed_at IS NOT NULL
                  AND DATE(completed_at) >= ?
                """,
                (user_id, start_str),
            ).fetchone()[0]
            m2 = connection.execute(
                """
                SELECT COALESCE(SUM(COALESCE(sc.minutes_done, st.duration_minutes)), 0) AS v
                FROM session_completion sc
                JOIN plan_day pd ON pd.id = sc.plan_day_id
                JOIN plan p ON p.id = pd.plan_id
                LEFT JOIN session_template st ON st.id = pd.template_id
                WHERE p.user_id = ?
                  AND DATE(sc.completed_at) >= ?
                """,
                (user_id, start_str),
            ).fetchone()[0]
            return int(m1 or 0) + int(m2 or 0)

        weekly_minutes = minutes_since(week_start)
        monthly_minutes = minutes_since(month_start)

        # All-time totals
        total_sessions = connection.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM session_log WHERE user_id = ? AND completed_at IS NOT NULL)
              + (SELECT COUNT(*) FROM session_completion sc
                 JOIN plan_day pd ON pd.id = sc.plan_day_id
                 JOIN plan p ON p.id = pd.plan_id
                 WHERE p.user_id = ?) AS total
            """,
            (user_id, user_id),
        ).fetchone()[0]

        # Readiness average for period
        readiness_rows = connection.execute(
            """
            SELECT sleep_hours, stress_1_10, soreness_1_10, mood_1_10
            FROM recovery_checkin
            WHERE user_id = ?
              AND date >= ?
            ORDER BY date DESC
            """,
            (user_id, start_iso),
        ).fetchall()
        readiness_avg = None
        if readiness_rows:
            scores = []
            for r in readiness_rows:
                score, _ex = compute_readiness_score(
                    float(r["sleep_hours"] or 0),
                    int(r["stress_1_10"] or 5),
                    int(r["soreness_1_10"] or 5),
                    int(r["mood_1_10"] or 5),
                )
                scores.append(score)
            if scores:
                readiness_avg = round(sum(scores) / len(scores), 1)

        # Streak: consecutive days with any completion (manual or plan).
        days = connection.execute(
            """
            SELECT DISTINCT day FROM (
                SELECT DATE(completed_at) AS day
                FROM session_log
                WHERE user_id = ? AND completed_at IS NOT NULL
                UNION
                SELECT DATE(sc.completed_at) AS day
                FROM session_completion sc
                JOIN plan_day pd ON pd.id = sc.plan_day_id
                JOIN plan p ON p.id = pd.plan_id
                WHERE p.user_id = ?
            )
            ORDER BY day DESC
            """,
            (user_id, user_id),
        ).fetchall()
        streak = 0
        day_set = {row[0] for row in days if row and row[0]}
        cursor = today
        while cursor.isoformat() in day_set:
            streak += 1
            cursor -= timedelta(days=1)

        # Recent activity (merged)
        recent_activity = []
        manual_recent = connection.execute(
            """
            SELECT id, title, duration_minutes, completed_at, created_at
            FROM session_log
            WHERE user_id = ?
              AND completed_at IS NOT NULL
            ORDER BY completed_at DESC
            LIMIT 10
            """,
            (user_id,),
        ).fetchall()
        for r in manual_recent:
            recent_activity.append(
                {
                    "type": "manual",
                    "title": r["title"],
                    "minutes": int(r["duration_minutes"] or 0),
                    "when": r["completed_at"] or r["created_at"],
                    "url": f"/sessions/{int(r['id'])}",
                }
            )

        plan_recent = connection.execute(
            """
            SELECT sc.id AS completion_id,
                   COALESCE(st.name, 'Planned Session') AS title,
                   COALESCE(sc.minutes_done, st.duration_minutes, 0) AS minutes,
                   sc.completed_at AS completed_at
            FROM session_completion sc
            JOIN plan_day pd ON pd.id = sc.plan_day_id
            JOIN plan p ON p.id = pd.plan_id
            LEFT JOIN session_template st ON st.id = pd.template_id
            WHERE p.user_id = ?
            ORDER BY sc.completed_at DESC
            LIMIT 10
            """,
            (user_id,),
        ).fetchall()
        for r in plan_recent:
            recent_activity.append(
                {
                    "type": "plan",
                    "title": r["title"],
                    "minutes": int(r["minutes"] or 0),
                    "when": r["completed_at"],
                    "url": f"/session/summary/{int(r['completion_id'])}",
                }
            )

        recent_activity.sort(key=lambda item: item.get("when") or "", reverse=True)
        recent_activity = recent_activity[:10]

        # Last 5 manual sessions (including pending) for quick access.
        last_sessions = connection.execute(
            """
            SELECT id, title, category, intensity, duration_minutes, training_load, created_at, completed_at
            FROM session_log
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT 5
            """,
            (user_id,),
        ).fetchall()

        connection.close()

        return render_template(
            "dashboard.html",
            view=view,
            week_start=week_start.isoformat(),
            month_start=month_start.isoformat(),
            total_sessions=int(total_sessions or 0),
            period_sessions=int(period_sessions),
            period_minutes=int(period_minutes),
            period_load=float(period_load),
            weekly_minutes=int(weekly_minutes),
            monthly_minutes=int(monthly_minutes),
            avg_rpe=avg_rpe,
            readiness_avg=readiness_avg,
            streak=int(streak),
            recent_activity=recent_activity,
            last_sessions=[dict(r) for r in last_sessions],
        )

    @app.get("/sessions")
    @require_login
    def sessions():
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        user_id = current_user_id(connection)
        rows = connection.execute(
            """
            SELECT id, title, category, intensity, duration_minutes, training_load, notes, created_at, completed_at
            FROM session_log
            WHERE user_id = ?
            ORDER BY created_at DESC
            """,
            (user_id,),
        ).fetchall()
        connection.close()
        return render_template("sessions.html", sessions=[dict(r) for r in rows])

    @app.get("/sessions/new")
    @require_login
    def sessions_new():
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        user_id = current_user_id(connection)
        media_rows = connection.execute(
            """
            SELECT id, original_name, mime_type
            FROM media_item
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT 200
            """,
            (user_id,),
        ).fetchall()
        connection.close()
        media_items = [
            {
                "id": int(r[0]),
                "original_name": str(r[1]),
                "mime_type": str(r[2]),
                "category": _media_category(str(r[2])),
            }
            for r in media_rows
        ]
        return render_template("session_new.html", error=None, form_data={}, media_items=media_items)

    @app.post("/sessions/create")
    @require_login
    def sessions_create():
        title = (request.form.get("title") or "").strip()
        category = (request.form.get("category") or "").strip()
        intensity = (request.form.get("intensity") or "").strip()
        duration_minutes = int(request.form.get("duration_minutes") or 0)
        notes = (request.form.get("notes") or "").strip()
        media_id_raw = (request.form.get("media_id") or "").strip()
        media_id = int(media_id_raw) if media_id_raw.isdigit() else None
        if not title or not category or not intensity or duration_minutes <= 0:
            return render_template(
                "session_new.html",
                error="Please complete all fields (duration must be > 0).",
                form_data=request.form,
                media_items=[],
            ), 400

        intensity_factor = 1.0
        intensity_norm = intensity.lower()
        if intensity_norm.isdigit():
            rpe = max(1, min(10, int(intensity_norm)))
            # Simple, explainable scaling: 1 -> 0.66, 10 -> 1.20
            intensity_factor = round(0.6 + (rpe * 0.06), 2)
        else:
            intensity_factor = {"low": 0.8, "medium": 1.0, "high": 1.2}.get(intensity_norm, 1.0)
        training_load = round(duration_minutes * float(intensity_factor), 2)

        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        user_id = current_user_id(connection)
        if media_id is not None:
            owned = connection.execute(
                "SELECT 1 FROM media_item WHERE id = ? AND user_id = ?",
                (int(media_id), user_id),
            ).fetchone()
            if not owned:
                connection.close()
                return render_template(
                    "session_new.html",
                    error="Selected media item not found.",
                    form_data=request.form,
                    media_items=[],
                ), 400
        now = utc_now_iso()
        connection.execute(
            """
            INSERT INTO session_log(user_id, title, category, intensity, duration_minutes, training_load, notes, media_id, created_at, completed_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
            """,
            (user_id, title, category, intensity, duration_minutes, training_load, notes, media_id, now, now),
        )
        connection.commit()
        session_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
        connection.close()
        return redirect(f"/sessions/{int(session_id)}")

    @app.get("/sessions/<int:session_id>")
    @require_login
    def session_detail(session_id: int):
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        user_id = current_user_id(connection)
        row = connection.execute(
            "SELECT * FROM session_log WHERE id = ? AND user_id = ?",
            (int(session_id), user_id),
        ).fetchone()
        if row is None:
            connection.close()
            return render_template("friendly_error.html", code=404, message="Session not found."), 404

        metric = connection.execute(
            """
            SELECT heart_rate_avg, calories, perceived_exertion, created_at
            FROM session_metric
            WHERE session_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (int(session_id),),
        ).fetchone()

        media = None
        try:
            media_id = row["media_id"] if isinstance(row, sqlite3.Row) else row.get("media_id")
        except Exception:
            media_id = None
        if media_id:
            mrow = connection.execute(
                """
                SELECT id, original_name, stored_name, mime_type, size_bytes
                FROM media_item
                WHERE id = ? AND user_id = ?
                """,
                (int(media_id), user_id),
            ).fetchone()
            if mrow:
                media = {
                    "id": int(mrow[0]),
                    "original_name": str(mrow[1]),
                    "stored_name": str(mrow[2]),
                    "mime_type": str(mrow[3]),
                    "size_bytes": int(mrow[4]),
                    "category": _media_category(str(mrow[3])),
                }
        connection.close()
        return render_template(
            "session_detail.html",
            session=dict(row),
            metric=dict(metric) if metric else None,
            media=media,
            error=None,
        )

    @app.post("/sessions/<int:session_id>/complete")
    @require_login
    def session_complete(session_id: int):
        heart_rate_avg = request.form.get("heart_rate_avg")
        calories = request.form.get("calories")
        perceived = request.form.get("perceived_exertion")
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        user_id = current_user_id(connection)
        row = connection.execute(
            "SELECT * FROM session_log WHERE id = ? AND user_id = ?",
            (int(session_id), user_id),
        ).fetchone()
        if row is None:
            connection.close()
            return render_template("friendly_error.html", code=404, message="Session not found."), 404

        now = utc_now_iso()
        connection.execute(
            "UPDATE session_log SET completed_at = COALESCE(completed_at, ?), updated_at = ? WHERE id = ? AND user_id = ?",
            (now, now, int(session_id), user_id),
        )
        connection.execute(
            """
            INSERT INTO session_metric(session_id, heart_rate_avg, calories, perceived_exertion, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                int(session_id),
                int(heart_rate_avg) if heart_rate_avg else None,
                int(calories) if calories else None,
                int(perceived) if perceived else None,
                now,
            ),
        )
        connection.commit()
        connection.close()
        return redirect(f"/sessions/{int(session_id)}")

    @app.get("/media")
    @require_login
    def media_library():
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        user_id = current_user_id(connection)
        rows = connection.execute(
            """
            SELECT id, original_name, mime_type, size_bytes, tags, created_at
            FROM media_item
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT 500
            """,
            (user_id,),
        ).fetchall()
        connection.close()
        items = []
        for r in rows:
            items.append(
                {
                    "id": int(r[0]),
                    "original_name": str(r[1]),
                    "mime_type": str(r[2]),
                    "size_bytes": int(r[3]),
                    "tags": str(r[4] or ""),
                    "created_at": str(r[5]),
                    "category": _media_category(str(r[2])),
                }
            )
        return render_template("media.html", items=items, error=None)

    @app.post("/media/upload")
    @require_login
    def media_upload():
        file = request.files.get("file")
        tags = (request.form.get("tags") or "").strip()
        if not file or not file.filename:
            return render_template("media.html", items=[], error="Please choose a file to upload."), 400
        if not _allowed_ext(file.filename):
            return render_template(
                "media.html",
                items=[],
                error=f"File type not allowed. Allowed: {', '.join(sorted(ALLOWED_MEDIA_EXTS))}",
            ), 400
        original = secure_filename(file.filename)
        ext = Path(original).suffix.lower()
        stored = f"{uuid.uuid4().hex}{ext}"
        target = (MEDIA_DIR / stored).resolve()
        if MEDIA_DIR not in target.parents and target.parent != MEDIA_DIR:
            abort(400)
        target.parent.mkdir(parents=True, exist_ok=True)
        file.save(str(target))
        size_bytes = target.stat().st_size
        if size_bytes > MAX_UPLOAD_BYTES:
            try:
                target.unlink(missing_ok=True)
            except Exception:
                pass
            return render_template("media.html", items=[], error="File too large."), 400
        mime_type = _guess_mime(original)

        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        user_id = current_user_id(connection)
        now = utc_now_iso()
        connection.execute(
            """
            INSERT INTO media_item(user_id, original_name, stored_name, mime_type, size_bytes, tags, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, original, stored, mime_type, int(size_bytes), tags, now, now),
        )
        connection.commit()
        connection.close()
        return redirect(url_for("media_library"))

    @app.get("/media/<int:media_id>")
    @require_login
    def media_detail(media_id: int):
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        user_id = current_user_id(connection)
        row = connection.execute(
            """
            SELECT id, original_name, stored_name, mime_type, size_bytes, tags, created_at
            FROM media_item
            WHERE id = ? AND user_id = ?
            """,
            (int(media_id), user_id),
        ).fetchone()
        connection.close()
        if not row:
            return render_template("friendly_error.html", code=404, title="Not found", message="Media item not found."), 404
        item = {
            "id": int(row[0]),
            "original_name": str(row[1]),
            "stored_name": str(row[2]),
            "mime_type": str(row[3]),
            "size_bytes": int(row[4]),
            "tags": str(row[5] or ""),
            "created_at": str(row[6]),
            "category": _media_category(str(row[3])),
        }
        return render_template("media_detail.html", item=item)

    @app.get("/media/<int:media_id>/download")
    @require_login
    def media_download(media_id: int):
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        user_id = current_user_id(connection)
        row = connection.execute(
            "SELECT original_name, stored_name, mime_type FROM media_item WHERE id = ? AND user_id = ?",
            (int(media_id), user_id),
        ).fetchone()
        connection.close()
        if not row:
            abort(404)
        original, stored, mime_type = str(row[0]), str(row[1]), str(row[2])
        path = (MEDIA_DIR / stored).resolve()
        if MEDIA_DIR not in path.parents and path.parent != MEDIA_DIR:
            abort(404)
        if not path.exists():
            abort(404)
        return send_file(path, mimetype=mime_type, as_attachment=True, download_name=original)

    @app.get("/media/<int:media_id>/raw")
    @require_login
    def media_raw(media_id: int):
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        user_id = current_user_id(connection)
        row = connection.execute(
            "SELECT stored_name, mime_type FROM media_item WHERE id = ? AND user_id = ?",
            (int(media_id), user_id),
        ).fetchone()
        connection.close()
        if not row:
            abort(404)
        stored, mime_type = str(row[0]), str(row[1])
        path = (MEDIA_DIR / stored).resolve()
        if MEDIA_DIR not in path.parents and path.parent != MEDIA_DIR:
            abort(404)
        if not path.exists():
            abort(404)
        return send_file(path, mimetype=mime_type, as_attachment=False)

    @app.post("/media/<int:media_id>/delete")
    @require_login
    def media_delete(media_id: int):
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        user_id = current_user_id(connection)
        row = connection.execute(
            "SELECT stored_name FROM media_item WHERE id = ? AND user_id = ?",
            (int(media_id), user_id),
        ).fetchone()
        if not row:
            connection.close()
            return redirect(url_for("media_library"))
        stored = str(row[0])
        connection.execute(
            "UPDATE session_log SET media_id = NULL WHERE user_id = ? AND media_id = ?",
            (user_id, int(media_id)),
        )
        connection.execute("DELETE FROM media_item WHERE id = ? AND user_id = ?", (int(media_id), user_id))
        connection.commit()
        connection.close()
        path = (MEDIA_DIR / stored).resolve()
        try:
            if path.exists() and (MEDIA_DIR in path.parents or path.parent == MEDIA_DIR):
                path.unlink(missing_ok=True)
        except Exception:
            pass
        return redirect(url_for("media_library"))

    @app.get("/recovery")
    @require_login
    def recovery():
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        user_id = current_user_id(connection)
        rows = connection.execute(
            """
            SELECT date, sleep_hours, stress_1_10, soreness_1_10, mood_1_10, notes
            FROM recovery_checkin
            WHERE user_id = ?
            ORDER BY date DESC
            LIMIT 14
            """,
            (user_id,),
        ).fetchall()
        connection.close()

        entries = [dict(row) for row in rows]
        latest = entries[0] if entries else None

        readiness = None
        if latest is not None:
            score, explanation = compute_readiness_score(
                float(latest.get("sleep_hours") or 0),
                int(latest.get("stress_1_10") or 5),
                int(latest.get("soreness_1_10") or 5),
                int(latest.get("mood_1_10") or 5),
            )
            readiness = {"score": score, "label": readiness_label(score), "explanation": explanation}

        return render_template(
            "recovery.html",
            today=date.today().isoformat(),
            entries=entries,
            readiness=readiness,
        )

    @app.post("/api/recovery/checkin")
    @require_login
    def api_recovery_checkin():
        payload = request.form if request.form else (request.get_json(silent=True) or {})
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        user_id = current_user_id(connection)

        d = str(payload.get("date") or date.today().isoformat())
        sleep_hours = payload.get("sleep_hours")
        stress = payload.get("stress_1_10")
        soreness = payload.get("soreness_1_10")
        mood = payload.get("mood_1_10")
        notes = payload.get("notes")

        sleep_val = float(sleep_hours) if sleep_hours not in (None, "") else 0.0
        stress_val = int(stress) if stress not in (None, "") else 5
        soreness_val = int(soreness) if soreness not in (None, "") else 5
        mood_val = int(mood) if mood not in (None, "") else 5

        score, explanation = compute_readiness_score(sleep_val, stress_val, soreness_val, mood_val)
        label = readiness_label(score)

        now = utc_now_iso()
        connection.execute("DELETE FROM recovery_checkin WHERE user_id = ? AND date = ?", (user_id, d))
        connection.execute(
            """
            INSERT INTO recovery_checkin(user_id, date, sleep_hours, stress_1_10, soreness_1_10, mood_1_10, notes, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                d,
                float(sleep_val) if sleep_hours not in (None, "") else None,
                int(stress_val) if stress not in (None, "") else None,
                int(soreness_val) if soreness not in (None, "") else None,
                int(mood_val) if mood not in (None, "") else None,
                str(notes) if notes not in (None, "") else None,
                now,
                now,
            ),
        )
        connection.commit()
        connection.close()

        out = {
            "ok": True,
            "date": d,
            "readiness_score": score,
            "readiness_label": label,
            "explanation": explanation,
        }

        # Browser form submits to /api/* but expects redirect; JSON clients want JSON.
        if request.is_json:
            return jsonify(out)
        return redirect(url_for("recovery"))

    @app.get("/plan/current")
    @require_login
    def plan_current():
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        user_id = current_user_id(connection)

        plan = current_plan_record(connection, user_id)
        if plan is None:
            connection.close()
            return render_template(
                "plan_current.html",
                plan=None,
                weeks=[],
                today_week=1,
                today_day=1,
                today_plan_day_id=None,
                readiness=None,
                suggestion=None,
                suggestion_text=None,
            )

        rows = connection.execute(
            """
            SELECT
                pd.id,
                pd.week,
                pd.day_index,
                pd.title,
                st.name AS template_name,
                st.discipline,
                st.duration_minutes,
                CASE WHEN sc.id IS NULL THEN 0 ELSE 1 END AS completed
            FROM plan_day pd
            LEFT JOIN session_template st ON st.id = pd.template_id
            LEFT JOIN session_completion sc ON sc.plan_day_id = pd.id
            WHERE pd.plan_id = ?
            ORDER BY pd.week ASC, pd.day_index ASC
            """,
            (int(plan["id"]),),
        ).fetchall()

        # Build structured weeks for template
        weeks_map: dict[int, list[dict]] = {}
        for r in rows:
            d = dict(r)
            wk = int(d.get("week") or 1)
            d["week"] = wk
            d["day_index"] = int(d.get("day_index") or 1)
            d["completed"] = bool(d.get("completed"))
            weeks_map.setdefault(wk, []).append(d)

        weeks = [{"week": wk, "days": weeks_map[wk]} for wk in sorted(weeks_map.keys())]

        # Determine today position within plan
        try:
            start_date = datetime.fromisoformat(plan["start_date"]).date()
        except Exception:
            start_date = date.today()
        delta_days = max(0, (date.today() - start_date).days)
        today_week = min(4, (delta_days // 7) + 1)
        today_day = min(7, (delta_days % 7) + 1)

        today_plan_day_id = None
        for w in weeks:
            for day in w["days"]:
                if int(day["week"]) == int(today_week) and int(day["day_index"]) == int(today_day):
                    today_plan_day_id = int(day["id"])
                    break
            if today_plan_day_id:
                break

        # Latest readiness influences suggestion only (does not overwrite)
        recent = connection.execute(
            """
            SELECT sleep_hours, stress_1_10, soreness_1_10, mood_1_10
            FROM recovery_checkin
            WHERE user_id = ?
            ORDER BY date DESC
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()

        readiness = None
        suggestion = None
        suggestion_text = None
        if recent is not None:
            score, explanation = compute_readiness_score(
                float(recent["sleep_hours"] or 0),
                int(recent["stress_1_10"] or 5),
                int(recent["soreness_1_10"] or 5),
                int(recent["mood_1_10"] or 5),
            )
            readiness = {"score": score, "label": readiness_label(score), "explanation": explanation}
            if score < 50:
                suggestion = suggestion_for_low_readiness(connection)
                suggestion_text = "Low readiness today — consider lighter work (recovery/mobility)."
            elif score < 70:
                suggestion_text = "Moderate readiness — keep intensity controlled and focus on quality reps."
            else:
                suggestion_text = "Good readiness — you can push intensity as planned."

        connection.close()
        return render_template(
            "plan_current.html",
            plan=dict(plan),
            weeks=weeks,
            today_week=today_week,
            today_day=today_day,
            today_plan_day_id=today_plan_day_id,
            readiness=readiness,
            suggestion=suggestion,
            suggestion_text=suggestion_text,
        )

    @app.get("/templates")

    @app.get("/templates")
    @require_login
    def templates_catalog():
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        user_id = current_user_id(connection)

        limit_clause = ""
        limited = False
        if user_id > 0 and not has_paid_access(connection, user_id):
            limit_clause = "LIMIT 3"
            limited = True

        query = f"""
            SELECT id, name, discipline, duration_minutes, level
            FROM session_template
            ORDER BY discipline ASC, duration_minutes ASC, id ASC
            {limit_clause}
        """
        rows = connection.execute(query).fetchall()
        connection.close()
        return render_template("templates_catalog.html", templates=[dict(r) for r in rows], limited=limited)


    @app.get("/analytics")
    @require_login
    def analytics():
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        user_id = current_user_id(connection)
        data = analytics_snapshot(connection, user_id)
        connection.close()
        return render_template("analytics.html", analytics=data)


    @app.get("/assistant")
    @require_login
    def assistant_page():
        connection = sqlite3.connect(db_path)
        user_id = current_user_id(connection)
        messages = assistant_history(connection, user_id, limit=20)
        connection.close()
        return render_template("assistant.html", messages=messages)

    @app.post("/assistant/send")
    @require_login
    def assistant_send():
        message = str(request.form.get("message", "")).strip()
        if not message:
            return redirect(url_for("assistant_page"))
        connection = sqlite3.connect(db_path)
        user_id = current_user_id(connection)
        ctx = build_coach_context(connection, user_id)
        hist = assistant_history(connection, user_id, limit=20)
        save_assistant_message(connection, user_id, "user", message)
        # Try LLM; fallback always works.
        reply = try_openai_response(message, ctx, hist) or coach_fallback_response(message, ctx)
        save_assistant_message(connection, user_id, "assistant", reply)
        connection.commit()
        connection.close()
        return redirect(url_for("assistant_page"))

    @app.post("/api/assistant/chat")
    @require_login
    def api_assistant_chat():
        payload = request.get_json(silent=True) or {}
        message = str(payload.get("message", "")).strip()
        if not message:
            return jsonify({"ok": False, "error": "message_required"}), 400
        connection = sqlite3.connect(db_path)
        user_id = current_user_id(connection)
        ctx = build_coach_context(connection, user_id)
        hist = assistant_history(connection, user_id, limit=20)
        save_assistant_message(connection, user_id, "user", message)
        reply = try_openai_response(message, ctx, hist) or coach_fallback_response(message, ctx)
        save_assistant_message(connection, user_id, "assistant", reply)
        connection.commit()
        connection.close()
        return jsonify({"ok": True, "reply": reply, "context": ctx})

    @app.get("/settings/profile")

    @require_login
    def settings_profile():
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        user_id = current_user_id(connection)
        user = connection.execute("SELECT id, email, display_name, role FROM users WHERE id = ?", (user_id,)).fetchone()
        profile = connection.execute(
            "SELECT goal, days_per_week, minutes, equipment, constraints FROM profile WHERE user_id = ? ORDER BY id DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        sub = user_subscription(connection, user_id)
        connection.close()
        return render_template("settings_profile.html", user=user, profile=profile, subscription=sub, auth_enabled=auth_enabled())

    @app.post("/settings/profile")
    @require_login
    def settings_profile_submit():
        payload = request.form
        display_name = str(payload.get("display_name", "")).strip() or "User"
        goal = str(payload.get("goal", "hybrid")).strip().lower().replace(" ", "_")
        days_per_week = clamp_int(int(payload.get("days_per_week", 4)), 2, 6)
        minutes = clamp_int(int(payload.get("minutes", 45)), 30, 75)
        equipment = str(payload.get("equipment", "")).strip()
        constraints = str(payload.get("constraints", "")).strip()
        now = utc_now_iso()

        connection = sqlite3.connect(db_path)
        user_id = current_user_id(connection)
        connection.execute("UPDATE users SET display_name = ?, updated_at = ? WHERE id = ?", (display_name, now, user_id))
        row = connection.execute("SELECT id FROM profile WHERE user_id = ? ORDER BY id DESC LIMIT 1", (user_id,)).fetchone()
        if row:
            connection.execute(
                "UPDATE profile SET goal = ?, days_per_week = ?, minutes = ?, equipment = ?, constraints = ?, updated_at = ? WHERE id = ?",
                (goal, days_per_week, minutes, equipment, constraints, now, row[0]),
            )
        else:
            connection.execute(
                "INSERT INTO profile (user_id, goal, days_per_week, minutes, equipment, constraints, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (user_id, goal, days_per_week, minutes, equipment, constraints, now, now),
            )
        connection.commit()
        connection.close()
        return redirect(url_for("settings_profile"))

    @app.get("/api/billing/checkout")
    @require_login
    def billing_checkout_stub():
        return jsonify(
            {
                "ok": True,
                "provider": "stripe_stub",
                "message": "Payment link intentionally unimplemented for safety.",
                "pay_now_link": None,
                "paid_benefits": ["unlimited_plans", "priority_support", "early_access_ai"],
            }
        )

    @app.get("/admin")
    @require_login
    def admin_dashboard():
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        actor_id = current_user_id(connection)
        actor = connection.execute("SELECT role FROM users WHERE id = ?", (actor_id,)).fetchone()
        if not actor or actor[0] != "admin":
            connection.close()
            return jsonify({"error": "admin_only"}), 403
        rows = connection.execute(
            """
            SELECT u.id, u.email, u.display_name, u.role, u.enabled,
                   COALESCE(s.plan, 'free') AS subscription_plan,
                   COALESCE(s.status, 'active') AS subscription_status
            FROM users u
            LEFT JOIN subscriptions s ON s.user_id = u.id
            ORDER BY u.id ASC
            """
        ).fetchall()
        connection.close()
        return render_template("admin.html", users=[dict(r) for r in rows])

    @app.post("/admin/users/<int:user_id>/toggle")
    @require_login
    def admin_toggle_user(user_id: int):
        connection = sqlite3.connect(db_path)
        actor_id = current_user_id(connection)
        role = connection.execute("SELECT role FROM users WHERE id = ?", (actor_id,)).fetchone()
        if not role or role[0] != "admin":
            connection.close()
            return jsonify({"error": "admin_only"}), 403
        row = connection.execute("SELECT enabled FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            connection.close()
            return jsonify({"error": "user_not_found"}), 404
        new_state = 0 if int(row[0]) else 1
        connection.execute("UPDATE users SET enabled = ?, updated_at = ? WHERE id = ?", (new_state, utc_now_iso(), user_id))
        connection.commit()
        connection.close()
        return redirect(url_for("admin_dashboard"))

    @app.get("/session/start/<int:plan_day_id>")
    @require_login
    def session_start(plan_day_id: int):
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """
            SELECT pd.id AS plan_day_id, pd.title, pd.week, pd.day_index, st.name AS template_name,
                   st.duration_minutes, st.json_blocks
            FROM plan_day pd
            LEFT JOIN session_template st ON st.id = pd.template_id
            WHERE pd.id = ?
            """,
            (plan_day_id,),
        ).fetchone()
        connection.close()

        if row is None:
            return jsonify({"error": "plan_day_not_found"}), 404

        blocks = blocks_from_json(row["json_blocks"] or "")
        if not blocks:
            duration = int(row["duration_minutes"] or 30)
            blocks = [{"name": row["template_name"] or "Session", "minutes": duration, "seconds": duration * 60}]

        return render_template(
            "session_start.html",
            session={
                "plan_day_id": int(row["plan_day_id"]),
                "title": row["title"] or row["template_name"] or "Session",
                "template_name": row["template_name"] or "Session",
                "week": int(row["week"]),
                "day_index": int(row["day_index"]),
                "blocks": blocks,
            },
        )

    @app.post("/api/session/finish")
    @require_login
    def api_session_finish():
        payload = request.get_json(silent=True) or request.form.to_dict()
        try:
            plan_day_id = int(payload.get("plan_day_id"))
            rpe = clamp_int(int(payload.get("rpe", 5)), 1, 10)
            notes = str(payload.get("notes", "")).strip()
            minutes_done = clamp_int(int(payload.get("minutes_done", 0)), 0, 300)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "invalid_payload"}), 400

        now = utc_now_iso()
        connection = sqlite3.connect(db_path)
        try:
            exists = connection.execute("SELECT id FROM plan_day WHERE id = ?", (plan_day_id,)).fetchone()
            if not exists:
                return jsonify({"ok": False, "error": "plan_day_not_found"}), 404

            cursor = connection.execute(
                """
                INSERT INTO session_completion (plan_day_id, completed_at, rpe, notes, minutes_done, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (plan_day_id, now, rpe, notes, minutes_done, now, now),
            )
            completion_id = int(cursor.lastrowid)
            write_audit(connection, "session_completed", {"completion_id": completion_id, "plan_day_id": plan_day_id})
            connection.commit()
        except sqlite3.Error as exc:
            connection.rollback()
            connection.close()
            return jsonify({"ok": False, "error": str(exc)}), 400
        connection.close()

        return jsonify({"ok": True, "completion_id": completion_id, "redirect": f"/session/summary/{completion_id}"})

    @app.get("/session/summary/<int:completion_id>")
    @require_login
    def session_summary(completion_id: int):
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """
            SELECT sc.id, sc.completed_at, sc.rpe, sc.notes, sc.minutes_done,
                   pd.title, pd.week, pd.day_index, st.name AS template_name
            FROM session_completion sc
            JOIN plan_day pd ON pd.id = sc.plan_day_id
            LEFT JOIN session_template st ON st.id = pd.template_id
            WHERE sc.id = ?
            """,
            (completion_id,),
        ).fetchone()
        connection.close()

        if row is None:
            return jsonify({"error": "completion_not_found"}), 404

        return render_template("session_summary.html", completion=row)


    @app.post("/api/timeline/update")
    def api_timeline_update():
        return jsonify({"ok": True, "route": "/api/timeline/update"})

    @app.post("/api/timeline/regenerate")
    def api_timeline_regenerate():
        return jsonify({"ok": True, "route": "/api/timeline/regenerate"})

    @app.post("/api/timeline/apply_global")
    def api_timeline_apply_global():
        return jsonify({"ok": True, "route": "/api/timeline/apply_global"})

    @app.post("/api/critic/run")
    def api_critic_run():
        return jsonify({"ok": True, "route": "/api/critic/run"})

    @app.post("/api/approve")
    def api_approve():
        return jsonify({"ok": True, "route": "/api/approve"})

    @app.get("/exports")
    @require_login
    def exports_page():
        return render_template("exports.html")

    @app.get("/restore")
    @require_login
    def restore_page():
        return render_template("restore.html")

    @app.get("/api/export/plan")
    @require_login
    def api_export_plan():
        connection = sqlite3.connect(db_path)
        user_id = current_user_id(connection)
        payload = export_snapshot(connection, user_id)
        connection.close()

        html = render_plan_export_html(payload)
        response = make_response(html)
        response.headers["Content-Type"] = "text/html; charset=utf-8"
        response.headers["Content-Disposition"] = "attachment; filename=flowform_plan_export.html"
        return response

    @app.get("/api/export/json")
    @require_login
    def api_export_json():
        connection = sqlite3.connect(db_path)
        user_id = current_user_id(connection)
        payload = export_snapshot(connection, user_id)
        connection.close()

        response = make_response(json.dumps(payload, indent=2))
        response.headers["Content-Type"] = "application/json"
        response.headers["Content-Disposition"] = "attachment; filename=flowform_backup.json"
        return response

    @app.get("/api/export/zip")
    @require_login
    def api_export_zip():
        connection = sqlite3.connect(db_path)
        user_id = current_user_id(connection)
        payload = export_snapshot(connection, user_id)
        connection.close()

        html = render_plan_export_html(payload)
        json_blob = json.dumps(payload, indent=2)

        memory = io.BytesIO()
        with zipfile.ZipFile(memory, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("flowform_plan_export.html", html)
            zf.writestr("flowform_backup.json", json_blob)
            db_file = Path(app.config["DB_PATH"])
            if db_file.exists():
                zf.write(db_file, arcname="flowform.db")
        memory.seek(0)

        return send_file(
            memory,
            mimetype="application/zip",
            as_attachment=True,
            download_name="flowform_export_bundle.zip",
        )

    @app.get("/api/export/backup")
    @require_login
    def api_export_backup():
        connection = sqlite3.connect(db_path)
        user_id = current_user_id(connection)
        payload = export_snapshot(connection, user_id)
        manifest = backup_manifest(connection)
        connection.close()

        settings_payload = {
            "app_name": app.config.get("APP_NAME"),
            "version": app.config.get("VERSION"),
            "port": app.config.get("PORT"),
            "build_date": app.config.get("BUILD_DATE"),
        }

        memory = io.BytesIO()
        with zipfile.ZipFile(memory, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            db_file = Path(app.config["DB_PATH"])
            if db_file.exists():
                zf.write(db_file, arcname="flowform.db")
            zf.writestr("flowform_backup.json", json.dumps(payload, indent=2))
            zf.writestr("settings.json", json.dumps(settings_payload, indent=2))
            zf.writestr("manifest.json", json.dumps(manifest, indent=2))
            if MEDIA_DIR.exists():
                for item in MEDIA_DIR.iterdir():
                    if item.is_file():
                        zf.write(item, arcname=f"media/{item.name}")
        memory.seek(0)
        return send_file(
            memory,
            mimetype="application/zip",
            as_attachment=True,
            download_name="flowform_full_backup.zip",
        )

    @app.get("/api/export/plan_pdf/<int:plan_id>")
    @require_login
    def api_export_plan_pdf(plan_id: int):
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        plan = connection.execute("SELECT id, name, start_date, weeks, status FROM plan WHERE id = ?", (plan_id,)).fetchone()
        if plan is None:
            connection.close()
            return jsonify({"error": "plan_not_found"}), 404
        days = connection.execute(
            """
            SELECT pd.week, pd.day_index, pd.title, st.name AS template_name, st.discipline, st.duration_minutes
            FROM plan_day pd
            LEFT JOIN session_template st ON st.id = pd.template_id
            WHERE pd.plan_id = ?
            ORDER BY pd.week ASC, pd.day_index ASC
            """,
            (plan_id,),
        ).fetchall()
        connection.close()

        lines = [
            f"Plan: {plan['name']} (status: {plan['status']})",
            f"Start: {plan['start_date']} | Weeks: {plan['weeks']}",
            "",
            "4-week schedule:",
        ]
        for row in days:
            lines.append(
                f"W{row['week']} D{row['day_index']} | {row['title'] or row['template_name'] or 'Session'} | {row['discipline'] or '-'} | {row['duration_minutes'] or 0} min"
            )
        pdf = build_simple_pdf(lines, title="FlowForm Plan PDF Export")
        return send_file(io.BytesIO(pdf), mimetype="application/pdf", as_attachment=True, download_name=f"flowform_plan_{plan_id}.pdf")

    @app.get("/api/export/session_summary/<int:completion_id>")
    @require_login
    def api_export_session_summary_pdf(completion_id: int):
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """
            SELECT sc.id, sc.completed_at, sc.rpe, sc.notes, sc.minutes_done,
                   pd.title, pd.week, pd.day_index, st.name AS template_name, st.json_blocks
            FROM session_completion sc
            JOIN plan_day pd ON pd.id = sc.plan_day_id
            LEFT JOIN session_template st ON st.id = pd.template_id
            WHERE sc.id = ?
            """,
            (completion_id,),
        ).fetchone()
        connection.close()
        if row is None:
            return jsonify({"error": "completion_not_found"}), 404

        blocks = blocks_from_json(row["json_blocks"] or "")
        lines = [
            f"Session title: {row['title'] or row['template_name'] or 'Session'}",
            f"Completed at: {row['completed_at']}",
            f"Week/Day: W{row['week']} D{row['day_index']}",
            f"RPE: {row['rpe']} | Minutes: {row['minutes_done']}",
            f"Notes: {row['notes'] or ''}",
            "",
            "Blocks:",
        ]
        for block in blocks:
            lines.append(f"- {block.get('name', 'Block')} ({block.get('minutes', 0)} min)")
        pdf = build_simple_pdf(lines, title="FlowForm Session Summary PDF")
        return send_file(io.BytesIO(pdf), mimetype="application/pdf", as_attachment=True, download_name=f"flowform_session_{completion_id}.pdf")

    @app.post("/api/import/backup")
    @require_login
    def api_import_backup():
        upload = request.files.get("file")
        if upload is None or not upload.filename:
            return jsonify({"ok": False, "error": "file_required"}), 400

        should_confirm = str((request.form.get("confirm_overwrite") or "false")).lower() in {"true", "1", "yes"}
        raw = upload.read()
        try:
            zf = zipfile.ZipFile(io.BytesIO(raw))
        except zipfile.BadZipFile:
            return jsonify({"ok": False, "error": "invalid_zip"}), 400

        names = set(zf.namelist())
        if "flowform.db" not in names:
            return jsonify({"ok": False, "error": "flowform.db_missing"}), 400

        manifest = {}
        if "manifest.json" in names:
            try:
                manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
            except Exception:
                manifest = {}

        summary = {
            "plans": int((manifest.get("counts") or {}).get("plans", 0)),
            "templates": int((manifest.get("counts") or {}).get("templates", 0)),
            "completions": int((manifest.get("counts") or {}).get("completions", 0)),
            "recovery": int((manifest.get("counts") or {}).get("recovery", 0)),
            "media_files": int(manifest.get("media_files", 0)),
            "warning": "Restoring this backup will overwrite current data.",
        }

        if not should_confirm:
            return jsonify({"ok": True, "requires_confirmation": True, "summary": summary})

        db_target = Path(app.config["DB_PATH"])
        db_target.parent.mkdir(parents=True, exist_ok=True)
        MEDIA_DIR.mkdir(parents=True, exist_ok=True)

        temp_dir = Path(tempfile.mkdtemp(prefix="flowform-restore-"))
        stage_db = temp_dir / "flowform.db"
        stage_media = temp_dir / "media"
        stage_media.mkdir(exist_ok=True)

        try:
            stage_db.write_bytes(zf.read("flowform.db"))
            for name in names:
                if name.startswith("media/") and not name.endswith("/"):
                    out = stage_media / Path(name).name
                    out.write_bytes(zf.read(name))

            probe = sqlite3.connect(stage_db)
            required = {"plan", "plan_day", "session_template", "session_completion", "recovery_checkin"}
            existing = {r[0] for r in probe.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            probe.close()
            if not required.issubset(existing):
                raise ValueError("backup_database_schema_invalid")

            old_db = db_target.with_suffix(".pre_restore.bak")
            if db_target.exists():
                shutil.copy2(db_target, old_db)

            tmp_live_db = db_target.with_suffix(".restore_tmp")
            shutil.copy2(stage_db, tmp_live_db)
            tmp_live_db.replace(db_target)

            old_media = MEDIA_DIR.parent / "media_pre_restore"
            if old_media.exists():
                shutil.rmtree(old_media)
            if MEDIA_DIR.exists():
                MEDIA_DIR.replace(old_media)
            shutil.copytree(stage_media, MEDIA_DIR, dirs_exist_ok=True)
            if old_media.exists():
                shutil.rmtree(old_media)
            if old_db.exists():
                old_db.unlink(missing_ok=True)

        except Exception as exc:
            shutil.rmtree(temp_dir, ignore_errors=True)
            return jsonify({"ok": False, "error": str(exc)}), 400

        shutil.rmtree(temp_dir, ignore_errors=True)
        return jsonify({"ok": True, "restored": summary})

    @app.post("/api/export")
    def api_export():
        return jsonify({"ok": True, "route": "/api/export"})

    @app.post("/api/import")
    def api_import():
        return jsonify({"ok": True, "route": "/api/import"})

    @app.get("/api/projects/<code>")
    def api_project_by_code(code: str):
        return jsonify({"ok": True, "route": "/api/projects/<code>", "code": code})

    @app.post("/api/agents/enhance")
    def api_agents_enhance():
        return jsonify({"ok": True, "route": "/api/agents/enhance"})

    def app_spec() -> dict:
        curated_routes = [
            {"path": "/", "methods": ["GET"], "description": "Ready landing page"},
            {"path": "/ready", "methods": ["GET"], "description": "Readiness page"},
            {"path": "/plan/wizard", "methods": ["GET"], "description": "Plan creation wizard"},
            {"path": "/plan/current", "methods": ["GET"], "description": "Current 4-week plan calendar"},
            {"path": "/recovery", "methods": ["GET"], "description": "Recovery check-in page"},
            {"path": "/analytics", "methods": ["GET"], "description": "Founder analytics dashboard"},
            {"path": "/assistant", "methods": ["GET"], "description": "Assistant coach page"},
            {"path": "/api/assistant/chat", "methods": ["POST"], "description": "Assistant chat (JSON)"},
            {"path": "/media", "methods": ["GET"], "description": "Media library"},
            {"path": "/media/upload", "methods": ["POST"], "description": "Upload media"},
            {"path": "/media/<media_id>", "methods": ["GET"], "description": "Media detail"},
            {"path": "/media/<media_id>/download", "methods": ["GET"], "description": "Download media"},
            {"path": "/media/<media_id>/raw", "methods": ["GET"], "description": "Stream media inline"},
            {"path": "/login", "methods": ["GET", "POST"], "description": "User login"},
            {"path": "/signup", "methods": ["GET", "POST"], "description": "User signup"},
            {"path": "/logout", "methods": ["GET"], "description": "User logout"},
            {"path": "/settings/profile", "methods": ["GET", "POST"], "description": "User profile settings"},
            {"path": "/admin", "methods": ["GET"], "description": "Admin dashboard"},
            {"path": "/api/billing/checkout", "methods": ["GET"], "description": "Payment provider stub"},
            {"path": "/exports", "methods": ["GET"], "description": "Exports page"},
            {"path": "/restore", "methods": ["GET"], "description": "Backup restore page"},
            {"path": "/templates", "methods": ["GET"], "description": "Session template catalog"},
            {"path": "/api/recovery/checkin", "methods": ["POST"], "description": "Persist daily recovery check-in"},
            {"path": "/health", "methods": ["GET"], "description": "Operational health endpoint"},
            {"path": "/version", "methods": ["GET"], "description": "Build/version metadata"},
            {"path": "/diagnostics", "methods": ["GET"], "description": "Diagnostics checks (HTML)"},
            {"path": "/api/diagnostics", "methods": ["GET"], "description": "Diagnostics checks (JSON)"},
            {"path": "/api/spec", "methods": ["GET"], "description": "API + route spec"},
            {"path": "/api/health", "methods": ["GET"], "description": "Legacy API health status"},
            {"path": "/api/plan/create", "methods": ["POST"], "description": "Create a 4-week plan"},
            {"path": "/api/plan/regenerate-next-week", "methods": ["POST"], "description": "Regenerate next week plan days"},
            {"path": "/session/start/<plan_day_id>", "methods": ["GET"], "description": "Session player"},
            {"path": "/api/session/finish", "methods": ["POST"], "description": "Persist session completion"},
            {"path": "/session/summary/<completion_id>", "methods": ["GET"], "description": "Session completion summary"},
            {"path": "/api/timeline/update", "methods": ["POST"], "description": "Update timeline"},
            {"path": "/api/timeline/regenerate", "methods": ["POST"], "description": "Regenerate timeline"},
            {"path": "/api/timeline/apply_global", "methods": ["POST"], "description": "Apply global timeline config"},
            {"path": "/api/critic/run", "methods": ["POST"], "description": "Run critic pass"},
            {"path": "/api/approve", "methods": ["POST"], "description": "Approve current draft"},
            {"path": "/api/export", "methods": ["POST"], "description": "Export project"},
            {"path": "/api/export/plan", "methods": ["GET"], "description": "Download plan HTML export"},
            {"path": "/api/export/json", "methods": ["GET"], "description": "Download full backup JSON"},
            {"path": "/api/export/zip", "methods": ["GET"], "description": "Download zip bundle"},
            {"path": "/api/export/backup", "methods": ["GET"], "description": "Download full-fidelity backup ZIP"},
            {"path": "/api/export/plan_pdf/<plan_id>", "methods": ["GET"], "description": "Download plan PDF"},
            {"path": "/api/export/session_summary/<completion_id>", "methods": ["GET"], "description": "Download session summary PDF"},
            {"path": "/api/import", "methods": ["POST"], "description": "Import project"},
            {"path": "/api/import/backup", "methods": ["POST"], "description": "Restore full-fidelity backup ZIP"},
            {"path": "/admin/users/<user_id>/toggle", "methods": ["POST"], "description": "Enable/disable user account"},
            {"path": "/api/projects/<code>", "methods": ["GET"], "description": "Fetch project by code"},
            {"path": "/api/agents/enhance", "methods": ["POST"], "description": "Enhance via agent"},
        ]

        seen_paths = {item["path"] for item in curated_routes}
        for rule in app.url_map.iter_rules():
            if rule.endpoint == "static":
                continue
            if rule.rule in seen_paths:
                continue
            methods = sorted(m for m in rule.methods if m not in {"HEAD", "OPTIONS"})
            curated_routes.append({"path": rule.rule, "methods": methods, "description": "Auto-discovered route"})
            seen_paths.add(rule.rule)

        return {"name": app.config["APP_NAME"], "version": app.config["VERSION"], "routes": curated_routes}

    @app.get("/api/spec")
    def api_spec():
        return jsonify(app_spec())

    def diagnostics_payload() -> dict:
        needed = [
            '/health',
            '/version',
            '/api/health',
            '/api/diagnostics',
            '/api/plan/create',
            '/plan/wizard',
            '/plan/current',
            '/dashboard',
            '/sessions',
            '/sessions/new',
            '/sessions/create',
            '/sessions/<int:session_id>',
            '/sessions/<int:session_id>/complete',
            '/recovery',
            '/analytics',
            '/exports',
            '/restore',
            '/templates',
            '/api/recovery/checkin',
            '/api/export/plan',
            '/api/export/json',
            '/api/export/backup',
            '/api/export/plan_pdf/<plan_id>',
            '/api/export/session_summary/<completion_id>',
            '/api/import/backup',
            '/api/spec',
            '/diagnostics',
            '/ready',
            '/assistant',
            '/api/assistant/chat',
            '/media',
            '/media/upload',
            '/media/<int:media_id>',
            '/media/<int:media_id>/download',
            '/media/<int:media_id>/raw',
        ]

        spec_routes = {route["path"] for route in app_spec()["routes"]}
        missing_from_spec = [route for route in needed if route not in spec_routes]

        snapshot = db_integrity_snapshot(db_path)
        checks = {
            "health_route": "PASS" if "/health" in spec_routes else "FAIL",
            "spec_mismatch": "FAIL" if missing_from_spec else "PASS",
            "db_integrity": "PASS" if snapshot["db_ok"] else "FAIL",
        }

        return {
            "status": "PASS" if all(v == "PASS" for v in checks.values()) else "FAIL",
            "needed": needed,
            "checks": checks,
            "missing_from_spec": missing_from_spec,
            "template_count": snapshot["template_count"],
            "missing_tables": snapshot["missing_tables"],
        }

    @app.get("/api/diagnostics")
    def api_diagnostics():
        return jsonify(diagnostics_payload())

    @app.get("/diagnostics")
    def diagnostics():
        payload = diagnostics_payload()
        return render_template("diagnostics.html", data=payload)

    @app.get("/ready")
    def ready():
        check = first_check_state(app)
        if not check.get("ok", False):
            return render_template("first_run_error.html", error_message=check.get("message", "Unknown startup check failure")), 500

        connection = sqlite3.connect(db_path)
        counts = {
            "templates": connection.execute("SELECT COUNT(*) FROM session_template").fetchone()[0],
            "plans": connection.execute("SELECT COUNT(*) FROM plan").fetchone()[0],
            "completions": connection.execute("SELECT COUNT(*) FROM session_completion").fetchone()[0],
            "recovery": connection.execute("SELECT COUNT(*) FROM recovery_checkin").fetchone()[0],
            "media": connection.execute("SELECT COUNT(*) FROM media_item").fetchone()[0],
        }
        connection.close()
        return render_template("ready.html", counts=counts)
        return render_template("ready.html")

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Run FlowForm Flask server")
    parser.add_argument("--port", type=int, default=None, help="Port to bind")
    args = parser.parse_args()

    app = create_app(port=args.port)
    host = os.getenv("HOST", "127.0.0.1")
    app.run(host=host, port=app.config["PORT"], debug=False)


if __name__ == "__main__":
    main()
