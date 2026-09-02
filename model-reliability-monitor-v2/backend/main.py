import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pymysql
from dotenv import load_dotenv
from fastapi import FastAPI, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
UNIT = os.getenv("DB_TIMESTAMP_UNIT", "s").lower()
if UNIT not in {"ms", "s"}:
    raise RuntimeError("DB_TIMESTAMP_UNIT must be 'ms' or 's'")
UNIT_FACTOR = 1000 if UNIT == "ms" else 1
BUCKET = 60 * UNIT_FACTOR
INCREMENT_BATCH_SIZE = int(os.getenv("DB_INCREMENT_BATCH_SIZE", "10000"))
MAX_BATCHES_PER_TICK = int(os.getenv("DB_MAX_BATCHES_PER_TICK", "1"))
INITIAL_SEED_ROWS = int(os.getenv("DB_INITIAL_SEED_ROWS", "0"))
RETENTION_SECONDS = 3600
CHECKPOINT_NAME = "model_group_minute_stats"
GROUP_MULTIPLIERS = [
    {"name": "bailian", "multiplier": 1},
    {"name": "claude code", "multiplier": 0.205},
    {"name": "codex", "multiplier": 0.035},
    {"name": "codex2", "multiplier": 0.029},
    {"name": "default", "multiplier": 1},
]


def db_connection():
    return pymysql.connect(
        host=os.environ["DB_HOST"],
        port=int(os.getenv("DB_PORT", "3306")),
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        database=os.getenv("DB_NAME", "oneapi"),
        charset="utf8mb4",
        connect_timeout=10,
        read_timeout=30,
        write_timeout=30,
        cursorclass=pymysql.cursors.DictCursor,
    )


def to_datetime(bucket: int) -> datetime:
    return datetime.fromtimestamp(bucket / UNIT_FACTOR, tz=timezone.utc)


def ensure_tables() -> None:
    with db_connection() as conn, conn.cursor() as cur:
        cur.execute("""
        CREATE TABLE IF NOT EXISTS model_group_minute_stats (
          stat_minute BIGINT NOT NULL,
          group_name VARCHAR(191) NOT NULL,
          model_name VARCHAR(191) NOT NULL,
          success_count BIGINT NOT NULL DEFAULT 0,
          failure_count BIGINT NOT NULL DEFAULT 0,
          total_count BIGINT NOT NULL DEFAULT 0,
          success_rate DECIMAL(8,5) DEFAULT NULL,
          updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          PRIMARY KEY (stat_minute, group_name, model_name),
          KEY idx_group_model_minute (group_name, model_name, stat_minute),
          KEY idx_model_minute (model_name, stat_minute)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS aggregation_checkpoints (
          job_name VARCHAR(100) NOT NULL,
          last_id BIGINT NOT NULL DEFAULT 0,
          updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          PRIMARY KEY (job_name)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        cur.execute("""
          SELECT id FROM logs FORCE INDEX (PRIMARY)
          ORDER BY id DESC LIMIT 1
        """)
        latest_log = cur.fetchone()
        latest_id = int(latest_log["id"]) if latest_log else 0
        cutoff_timestamp = (int(time.time()) - RETENTION_SECONDS) * UNIT_FACTOR
        cutoff_minute = (cutoff_timestamp // BUCKET) * BUCKET
        cur.execute("SELECT MAX(stat_minute) AS latest_stat FROM model_group_minute_stats")
        latest_stat = cur.fetchone()["latest_stat"]
        has_recent_stats = latest_stat is not None and int(latest_stat) >= cutoff_minute
        if not has_recent_stats:
            cur.execute(
                "DELETE FROM model_group_minute_stats WHERE stat_minute < %s OR stat_minute IS NULL",
                (cutoff_minute,),
            )
            cur.execute("SELECT 1 FROM model_group_minute_stats LIMIT 1")
            has_recent_stats = cur.fetchone() is not None
        cur.execute(
            "SELECT last_id FROM aggregation_checkpoints WHERE job_name=%s",
            (CHECKPOINT_NAME,),
        )
        checkpoint = cur.fetchone()
        if checkpoint is None:
            initial_id = max(0, latest_id - INITIAL_SEED_ROWS)
            cur.execute("""
              INSERT INTO aggregation_checkpoints (job_name, last_id)
              VALUES (%s, %s)
            """, (CHECKPOINT_NAME, initial_id))
        elif not has_recent_stats:
            cur.execute("""
              UPDATE aggregation_checkpoints SET last_id=%s WHERE job_name=%s
            """, (max(0, latest_id - INITIAL_SEED_ROWS), CHECKPOINT_NAME))
        conn.commit()


def aggregate_minute() -> bool:
    with db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT last_id FROM aggregation_checkpoints WHERE job_name=%s FOR UPDATE",
            (CHECKPOINT_NAME,),
        )
        checkpoint = cur.fetchone()
        last_id = int(checkpoint["last_id"] if checkpoint else 0)
        cutoff = (int(time.time()) - RETENTION_SECONDS) * UNIT_FACTOR
        cur.execute(
            "DELETE FROM model_group_minute_stats WHERE stat_minute < %s",
            ((cutoff // BUCKET) * BUCKET,),
        )
        cur.execute("""
          SELECT id, created_at, type, model_name, `group`
          FROM logs FORCE INDEX (PRIMARY)
          WHERE id > %s AND created_at >= %s
          ORDER BY id
          LIMIT %s
        """, (last_id, cutoff, INCREMENT_BATCH_SIZE))
        rows = cur.fetchall()
        if not rows:
            conn.commit()
            return False

        grouped: dict[tuple[int, str, str], list[int]] = {}
        new_last_id = last_id
        for row in rows:
            new_last_id = max(new_last_id, int(row["id"]))
            if row["type"] not in (2, 5) or row["created_at"] is None:
                continue
            stat_minute = (int(row["created_at"]) // BUCKET) * BUCKET
            group_name = row["group"] or "default"
            key = (stat_minute, group_name, row["model_name"] or "(unknown)")
            counts = grouped.setdefault(key, [0, 0])
            counts[0 if row["type"] == 2 else 1] += 1

        for (stat_minute, group_name, model_name), (success, failure) in grouped.items():
            total = success + failure
            cur.execute("""
              INSERT INTO model_group_minute_stats
                (stat_minute, group_name, model_name, success_count, failure_count, total_count, success_rate)
              VALUES (%s, %s, %s, %s, %s, %s, %s)
              ON DUPLICATE KEY UPDATE
                success_count=success_count + VALUES(success_count),
                failure_count=failure_count + VALUES(failure_count),
                total_count=total_count + VALUES(total_count),
                success_rate=ROUND((success_count + VALUES(success_count)) * 100 /
                  NULLIF(total_count + VALUES(total_count), 0), 5)
            """, (stat_minute, group_name, model_name, success, failure,
                  total, round(success * 100 / total, 5)))

        cur.execute("""
          UPDATE aggregation_checkpoints SET last_id=%s WHERE job_name=%s
        """, (new_last_id, CHECKPOINT_NAME))
        conn.commit()
        return True


def refresh() -> None:
    for _ in range(MAX_BATCHES_PER_TICK):
        if not aggregate_minute():
            break
        time.sleep(0.05)


def worker() -> None:
    while True:
        try:
            refresh()
        except Exception as exc:
            print(f"aggregation failed: {exc}", flush=True)
        time.sleep(max(5, 60 - (int(time.time()) % 60)))


app = FastAPI(title="Model Reliability Monitor")
app.mount("/static", StaticFiles(directory=ROOT / "frontend"), name="static")


@app.on_event("startup")
def startup() -> None:
    ensure_tables()
    threading.Thread(target=worker, daemon=True).start()


@app.get("/api/models")
def models(group: str | None = Query(default=None)) -> list[dict[str, Any]]:
    with db_connection() as conn, conn.cursor() as cur:
        sql = """
          SELECT group_name, model_name, SUM(success_count) success_count, SUM(failure_count) failure_count,
                 SUM(total_count) total_count,
                 ROUND(SUM(success_count) * 100 / NULLIF(SUM(total_count), 0), 2) success_rate
          FROM model_group_minute_stats
        """
        params: list[Any] = []
        if group:
            sql += " WHERE group_name=%s"
            params.append(group)
        sql += " GROUP BY group_name, model_name ORDER BY group_name, model_name"
        cur.execute(sql, params)
        return cur.fetchall()


@app.get("/api/models/{model_name}/minutes")
def minutes(model_name: str, group: str = Query("default"), count: int = Query(60, ge=1, le=1440)) -> list[dict[str, Any]]:
    start = int(time.time()) * UNIT_FACTOR // BUCKET * BUCKET - (count - 1) * BUCKET
    with db_connection() as conn, conn.cursor() as cur:
        cur.execute("""
          SELECT stat_minute, success_count, failure_count, total_count, success_rate
          FROM model_group_minute_stats
          WHERE group_name=%s AND model_name=%s AND stat_minute >= %s
          ORDER BY stat_minute
        """, (group, model_name, start))
        rows = cur.fetchall()
    by_minute = {int(row["stat_minute"]): row for row in rows}
    return [{"minute": to_datetime(start + i * BUCKET).isoformat(), **by_minute.get(start + i * BUCKET, {"success_count": 0, "failure_count": 0, "total_count": 0, "success_rate": None})} for i in range(count)]


@app.get("/")
def index() -> FileResponse:
    return FileResponse(ROOT / "frontend" / "index.html")


@app.get("/api/groups")
def groups() -> list[dict[str, Any]]:
    return GROUP_MULTIPLIERS


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("APP_PORT", "8080")))
