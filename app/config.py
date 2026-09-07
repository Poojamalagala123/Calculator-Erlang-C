from __future__ import annotations

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"
STATIC_DIR.mkdir(exist_ok=True)

# Upload and safety constraints
MAX_UPLOAD_BYTES = 100 * 1024 * 1024  # 100 MB limit
MAX_UPLOAD_FILES = 20
MAX_FORECAST_ROWS = 40_000
MAX_SCHEDULE_ROWS = 50_000
MAX_AGENT_COUNT = 10_000
MAX_FORECAST_DAYS = 3_650

# Worker thread settings
MAX_WORKER_THREADS = 8
