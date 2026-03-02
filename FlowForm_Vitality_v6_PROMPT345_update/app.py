import os
import sqlite3
from pathlib import Path

from flask import Flask, jsonify


def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS app_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO app_metadata (key, value)
            VALUES ('initialized', 'true')
            """
        )


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__)

    app.config.update(
        DB_PATH=os.getenv("DATABASE_PATH", "instance/flowform.db"),
        OPENAI_API_KEY=os.getenv("OPENAI_API_KEY", ""),
    )

    if test_config:
        app.config.update(test_config)

    db_path = Path(app.config["DB_PATH"])
    init_db(db_path)

    @app.get("/api/health")
    def api_health():
        api_key = app.config.get("OPENAI_API_KEY", "")
        ai_enabled = bool(api_key)
        return jsonify(
            {
                "status": "healthy",
                "service": "flowform-app",
                "ai": {
                    "enabled": ai_enabled,
                    "reason": None if ai_enabled else "OPENAI_API_KEY missing; AI features disabled",
                },
            }
        )

    @app.get("/ready")
    def ready():
        return jsonify(
            {
                "status": "ready",
                "db": {
                    "path": str(db_path),
                    "initialized": db_path.exists(),
                },
                "module": {
                    "flask": "ok",
                    "sqlite3": "ok",
                },
                "log": {
                    "level": os.getenv("LOG_LEVEL", "INFO"),
                },
            }
        )

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "5000")))
