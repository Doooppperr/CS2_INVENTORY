from __future__ import annotations

import os
from pathlib import Path


class Config:
    STATE_DIR = Path(os.getenv("CS2_STATE_DIR", "./var")).resolve()
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    SQLALCHEMY_DATABASE_URI = os.getenv(
        "DATABASE_URL", f"sqlite:///{(STATE_DIR / 'cs2_inventory.db').as_posix()}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SECRET_KEY = os.getenv("CS2_SECRET_KEY", "development-only-change-me")
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.getenv("CS2_COOKIE_SECURE", "0") == "1"
    SESSION_COOKIE_PATH = os.getenv("CS2_COOKIE_PATH", "/cs2_inventory/")
    PERMANENT_SESSION_LIFETIME = 86400
    MAX_TARGETS = int(os.getenv("CS2_MAX_TARGETS", "35"))
    PAGE_SIZE = 20
    SNAPSHOT_RETENTION_DAYS = 7
    INVENTORY_MONTHLY_BUDGET = int(os.getenv("CS2_MONTHLY_BUDGET", "9000"))
    INVENTORY_DAILY_BUDGET = int(os.getenv("CS2_DAILY_BUDGET", "300"))
    INVENTORY_RESERVE = int(os.getenv("CS2_INVENTORY_RESERVE", "1000"))
    REQUESTS_PER_SCAN = int(os.getenv("CS2_REQUESTS_PER_SCAN", "7"))
    PROFILE_REFRESH_DAYS = 7
    STEAMWEBAPI_KEY = os.getenv("STEAMWEBAPI_KEY", "").strip()
    OBSERVATION_CACHE = os.getenv(
        "INVENTORY_OBSERVATION_CACHE", str(STATE_DIR / "observations.json")
    )
    TESTING = False
