#!/usr/bin/env python3
"""
Hardware Performance Telemetry Logger & SQLite Storage.
Collects and persists real-time CPU, GPU, RAM and temperature data from LibreHardwareMonitor.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import socket
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("hw_logger")

DEFAULT_DB_NAME = "hardware.db"
DEFAULT_LHM_URL = "http://localhost:8085/data.json"
DEFAULT_RETENTION_DAYS = 7
DEFAULT_SERVER_PORT = 8086


def get_db_path(custom_path: Optional[str] = None) -> Path:
    """Return resolved absolute path to the SQLite database."""
    if custom_path:
        return Path(custom_path).resolve()
    env_path = os.environ.get("HW_DB_PATH")
    if env_path:
        return Path(env_path).resolve()
    return Path(__file__).resolve().parent / DEFAULT_DB_NAME


def get_connection(db_path: Path) -> sqlite3.Connection:
    """Create a configured SQLite connection with WAL mode enabled."""
    conn = sqlite3.connect(str(db_path), timeout=10.0)
    conn.row_factory = sqlite3.Row
    with conn:
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA temp_store = MEMORY;")
    return conn


def init_db(db_path: Path) -> None:
    """Initialize database schema and indexes."""
    with get_connection(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS telemetry (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp INTEGER NOT NULL,
                cpu REAL,
                gpu REAL,
                ram REAL,
                cpu_temp REAL,
                gpu_temp REAL
            );
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_telemetry_timestamp ON telemetry(timestamp);"
        )


def extract_telemetry(lhm_data: Dict[str, Any]) -> Dict[str, Optional[float]]:
    """
    Recursively traverse LibreHardwareMonitor JSON tree to extract
    CPU %, GPU %, RAM %, CPU Temp (°C), and GPU Temp (°C).
    """
    result: Dict[str, Optional[float]] = {
        "cpu": None,
        "gpu": None,
        "ram": None,
        "cpu_temp": None,
        "gpu_temp": None,
    }

    def walk(node: Dict[str, Any]) -> None:
        text = str(node.get("Text", ""))
        val = str(node.get("Value", ""))

        if (text == "CPU Total" or text == "CPU Core") and "%" in val:
            try:
                result["cpu"] = round(float(val.replace("%", "").strip()), 1)
            except ValueError:
                pass

        if (text == "GPU Core" or text == "GPU Core 3D") and "%" in val:
            try:
                result["gpu"] = round(float(val.replace("%", "").strip()), 1)
            except ValueError:
                pass

        if text == "Memory" and "%" in val:
            try:
                result["ram"] = round(float(val.replace("%", "").strip()), 1)
            except ValueError:
                pass

        if (text in ("CPU Package", "CPU Core", "Core Average")) and "°C" in val:
            if result["cpu_temp"] is None:
                try:
                    result["cpu_temp"] = round(float(val.replace("°C", "").strip()), 1)
                except ValueError:
                    pass

        if (text in ("GPU Core", "GPU Hot Spot", "GPU")) and "°C" in val:
            if result["gpu_temp"] is None:
                try:
                    result["gpu_temp"] = round(float(val.replace("°C", "").strip()), 1)
                except ValueError:
                    pass

        children = node.get("Children")
        if isinstance(children, list):
            for child in children:
                if isinstance(child, dict):
                    walk(child)

    walk(lhm_data)
    return result


def insert_point(
    db_path: Path,
    timestamp: int,
    cpu: Optional[float],
    gpu: Optional[float],
    ram: Optional[float],
    cpu_temp: Optional[float],
    gpu_temp: Optional[float],
) -> int:
    """Insert a single telemetry record into SQLite and return its row ID."""
    with get_connection(db_path) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO telemetry (timestamp, cpu, gpu, ram, cpu_temp, gpu_temp)
            VALUES (?, ?, ?, ?, ?, ?);
            """,
            (timestamp, cpu, gpu, ram, cpu_temp, gpu_temp),
        )
        return cur.lastrowid or 0


def insert_batch(db_path: Path, records: List[Dict[str, Any]]) -> int:
    """
    Insert multiple telemetry records in a single transaction.
    Returns the count of successfully inserted records.
    """
    if not records:
        return 0

    rows_to_insert: List[Tuple[int, Optional[float], Optional[float], Optional[float], Optional[float], Optional[float]]] = []
    for r in records:
        ts = int(r.get("timestamp") or r.get("t") or int(time.time() * 1000))
        
        def to_float(val: Any) -> Optional[float]:
            if val is None or val == "" or val == "--":
                return None
            try:
                return round(float(val), 1)
            except (ValueError, TypeError):
                return None

        cpu = to_float(r.get("cpu"))
        gpu = to_float(r.get("gpu"))
        ram = to_float(r.get("ram"))
        cpu_temp = to_float(r.get("cpu_temp") or r.get("cpuTemp"))
        gpu_temp = to_float(r.get("gpu_temp") or r.get("gpuTemp"))
        rows_to_insert.append((ts, cpu, gpu, ram, cpu_temp, gpu_temp))

    with get_connection(db_path) as conn:
        conn.executemany(
            """
            INSERT INTO telemetry (timestamp, cpu, gpu, ram, cpu_temp, gpu_temp)
            VALUES (?, ?, ?, ?, ?, ?);
            """,
            rows_to_insert,
        )
    return len(rows_to_insert)


def query_telemetry(
    db_path: Path,
    range_seconds: int = 3600,
    max_points: int = 500,
) -> List[Dict[str, Any]]:
    """
    Query telemetry records for the last `range_seconds`.
    Automatically performs bucket aggregation if row count exceeds `max_points`.
    """
    now_ms = int(time.time() * 1000)
    cutoff_ms = now_ms - (range_seconds * 1000)

    with get_connection(db_path) as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM telemetry WHERE timestamp >= ?;",
            (cutoff_ms,),
        )
        total_rows = cur.fetchone()[0]

        if total_rows == 0:
            return []

        if max_points <= 0 or total_rows <= max_points:
            cur.execute(
                """
                SELECT timestamp, cpu, gpu, ram, cpu_temp, gpu_temp
                FROM telemetry
                WHERE timestamp >= ?
                ORDER BY timestamp ASC;
                """,
                (cutoff_ms,),
            )
            results: List[Dict[str, Any]] = []
            for row in cur.fetchall():
                results.append({
                    "t": row["timestamp"],
                    "cpu": f"{row['cpu']:.1f}" if row["cpu"] is not None else "--",
                    "gpu": f"{row['gpu']:.1f}" if row["gpu"] is not None else "--",
                    "ram": f"{row['ram']:.1f}" if row["ram"] is not None else "--",
                    "cpuTemp": f"{row['cpu_temp']:.1f}" if row["cpu_temp"] is not None else "--",
                    "gpuTemp": f"{row['gpu_temp']:.1f}" if row["gpu_temp"] is not None else "--",
                })
            return results

        # Downsample via SQL bucketing
        bucket_interval_ms = max(1000, (range_seconds * 1000) // max_points)
        cur.execute(
            f"""
            SELECT 
                MIN(timestamp) as timestamp,
                ROUND(AVG(cpu), 1) as avg_cpu,
                ROUND(AVG(gpu), 1) as avg_gpu,
                ROUND(AVG(ram), 1) as avg_ram,
                ROUND(AVG(cpu_temp), 1) as avg_cpu_temp,
                ROUND(AVG(gpu_temp), 1) as avg_gpu_temp
            FROM telemetry
            WHERE timestamp >= ?
            GROUP BY CAST(timestamp / {bucket_interval_ms} AS INTEGER)
            ORDER BY timestamp ASC;
            """,
            (cutoff_ms,),
        )
        results = []
        for row in cur.fetchall():
            results.append({
                "t": row["timestamp"],
                "cpu": f"{row['avg_cpu']:.1f}" if row["avg_cpu"] is not None else "--",
                "gpu": f"{row['avg_gpu']:.1f}" if row["avg_gpu"] is not None else "--",
                "ram": f"{row['avg_ram']:.1f}" if row["avg_ram"] is not None else "--",
                "cpuTemp": f"{row['avg_cpu_temp']:.1f}" if row["avg_cpu_temp"] is not None else "--",
                "gpuTemp": f"{row['avg_gpu_temp']:.1f}" if row["avg_gpu_temp"] is not None else "--",
            })
        return results


def prune_old_records(db_path: Path, retention_days: int = DEFAULT_RETENTION_DAYS) -> int:
    """Delete records older than `retention_days` and run incremental vacuum."""
    cutoff_ms = int(time.time() * 1000) - (retention_days * 86400 * 1000)
    with get_connection(db_path) as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM telemetry WHERE timestamp < ?;", (cutoff_ms,))
        deleted = cur.rowcount
    return max(0, deleted)


def get_stats(db_path: Path) -> Dict[str, Any]:
    """Retrieve database summary statistics."""
    if not db_path.exists():
        return {"status": "not_found", "total_records": 0, "size_bytes": 0}

    size_bytes = db_path.stat().st_size
    with get_connection(db_path) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT 
                COUNT(*) as count,
                MIN(timestamp) as min_ts,
                MAX(timestamp) as max_ts,
                MIN(cpu) as min_cpu, MAX(cpu) as max_cpu, AVG(cpu) as avg_cpu,
                MIN(gpu) as min_gpu, MAX(gpu) as max_gpu, AVG(gpu) as avg_gpu,
                MIN(ram) as min_ram, MAX(ram) as max_ram, AVG(ram) as avg_ram
            FROM telemetry;
            """
        )
        row = cur.fetchone()
        return {
            "total_records": row["count"],
            "db_path": str(db_path),
            "size_bytes": size_bytes,
            "earliest_ts": row["min_ts"],
            "latest_ts": row["max_ts"],
            "cpu": {
                "min": row["min_cpu"],
                "max": row["max_cpu"],
                "avg": round(row["avg_cpu"], 1) if row["avg_cpu"] is not None else None,
            },
            "gpu": {
                "min": row["min_gpu"],
                "max": row["max_gpu"],
                "avg": round(row["avg_gpu"], 1) if row["avg_gpu"] is not None else None,
            },
            "ram": {
                "min": row["min_ram"],
                "max": row["max_ram"],
                "avg": round(row["avg_ram"], 1) if row["avg_ram"] is not None else None,
            },
        }


def export_to_csv(db_path: Path, output_file: Path) -> int:
    """Export all telemetry data to a CSV file."""
    with get_connection(db_path) as conn, open(output_file, "w", newline="", encoding="utf-8") as f:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, timestamp, cpu, gpu, ram, cpu_temp, gpu_temp
            FROM telemetry
            ORDER BY timestamp ASC;
            """
        )
        writer = csv.writer(f)
        writer.writerow(["id", "timestamp", "datetime_iso", "cpu_percent", "gpu_percent", "ram_percent", "cpu_temp_c", "gpu_temp_c"])
        count = 0
        for row in cur.fetchall():
            iso_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(row["timestamp"] / 1000))
            writer.writerow([
                row["id"],
                row["timestamp"],
                iso_time,
                row["cpu"],
                row["gpu"],
                row["ram"],
                row["cpu_temp"],
                row["gpu_temp"],
            ])
            count += 1
    return count


def fetch_and_record_once(db_path: Path, url: str = DEFAULT_LHM_URL) -> Optional[Dict[str, Any]]:
    """Fetch live data from LibreHardwareMonitor and insert into database."""
    req = urllib.request.Request(url, headers={"User-Agent": "OllamaHardwareLogger/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            content = resp.read()
            data = json.loads(content.decode("utf-8", errors="ignore"))
    except Exception as e:
        logger.debug("Failed to fetch LHM data: %s", e)
        return None

    metrics = extract_telemetry(data)
    ts = int(time.time() * 1000)
    insert_point(
        db_path,
        timestamp=ts,
        cpu=metrics["cpu"],
        gpu=metrics["gpu"],
        ram=metrics["ram"],
        cpu_temp=metrics["cpu_temp"],
        gpu_temp=metrics["gpu_temp"],
    )
    return {"timestamp": ts, **metrics}


def run_collection_daemon(
    db_path: Path,
    url: str = DEFAULT_LHM_URL,
    interval: float = 1.0,
    retention_days: int = DEFAULT_RETENTION_DAYS,
) -> None:
    """
    Run continuous background collector loop.
    Ensures single daemon instance via local TCP socket mutex.
    """
    lock_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        lock_socket.bind(("127.0.0.1", 48991))
    except socket.error:
        logger.warning("Another hw_logger daemon is already running on port 48991. Exiting.")
        sys.exit(0)

    init_db(db_path)
    logger.info("Hardware telemetry daemon started. Target: %s, DB: %s", url, db_path)

    last_prune = time.time()
    while True:
        try:
            fetch_and_record_once(db_path, url)
        except Exception as e:
            logger.error("Error during telemetry capture: %s", e)

        # Prune once every 6 hours
        now = time.time()
        if now - last_prune > 21600:
            try:
                pruned = prune_old_records(db_path, retention_days)
                if pruned > 0:
                    logger.info("Pruned %d records older than %d days", pruned, retention_days)
            except Exception as e:
                logger.error("Error during pruning: %s", e)
            last_prune = now

        time.sleep(interval)


class HardwareAPIHandler(BaseHTTPRequestHandler):
    db_path: Path = Path(DEFAULT_DB_NAME)

    def log_message(self, format: str, *args: Any) -> None:
        # Suppress routine HTTP access log to keep console clean
        return

    def _send_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Accept, Range")

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._send_cors_headers()
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path in ("/api/hardware/stats", "/api/stats"):
            stats = get_stats(self.db_path)
            body = json.dumps(stats, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path in ("/api/hardware/export", "/api/export"):
            temp_csv = self.db_path.with_suffix(".temp.csv")
            export_to_csv(self.db_path, temp_csv)
            try:
                body = temp_csv.read_bytes()
            finally:
                if temp_csv.exists():
                    temp_csv.unlink()
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="hardware_telemetry.csv"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path in ("/api/hardware/prune", "/api/prune"):
            days = DEFAULT_RETENTION_DAYS
            if "days" in qs:
                try:
                    days = int(qs["days"][0])
                except (ValueError, IndexError):
                    days = DEFAULT_RETENTION_DAYS
            deleted = prune_old_records(self.db_path, retention_days=days)
            stats = get_stats(self.db_path)
            res = json.dumps({
                "status": "ok",
                "deleted": deleted,
                "retention_days": days,
                "remaining": stats.get("total_records", 0),
                "size_bytes": stats.get("size_bytes", 0),
            }, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(res)))
            self.end_headers()
            self.wfile.write(res)
            return

        if path in ("/api/hardware", "/api/telemetry"):
            range_sec = 3600
            if "range" in qs:
                try:
                    range_sec = int(qs["range"][0])
                except (ValueError, IndexError):
                    range_sec = 3600
            max_pts = 500
            if "maxPoints" in qs:
                try:
                    max_pts = int(qs["maxPoints"][0])
                except (ValueError, IndexError):
                    max_pts = 500

            data = query_telemetry(self.db_path, range_seconds=range_sec, max_points=max_pts)
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        msg = json.dumps({"error": "Not Found", "path": path}).encode("utf-8")
        self.send_response(404)
        self._send_cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(msg)))
        self.end_headers()
        self.wfile.write(msg)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path in ("/api/hardware", "/api/telemetry"):
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length).decode("utf-8", errors="ignore")
            try:
                payload = json.loads(raw)
                if isinstance(payload, list):
                    count = insert_batch(self.db_path, payload)
                elif isinstance(payload, dict):
                    count = insert_batch(self.db_path, [payload])
                else:
                    count = 0
                res = json.dumps({"status": "ok", "inserted": count}).encode("utf-8")
                status_code = 200
            except Exception as e:
                res = json.dumps({"status": "error", "message": str(e)}).encode("utf-8")
                status_code = 400

            self.send_response(status_code)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(res)))
            self.end_headers()
            self.wfile.write(res)
            return

        if path in ("/api/hardware/prune", "/api/prune"):
            days = DEFAULT_RETENTION_DAYS
            length = int(self.headers.get("Content-Length", 0))
            if length > 0:
                raw = self.rfile.read(length).decode("utf-8", errors="ignore")
                try:
                    body_data = json.loads(raw)
                    if isinstance(body_data, dict) and "days" in body_data:
                        days = int(body_data["days"])
                except Exception:
                    pass
            deleted = prune_old_records(self.db_path, retention_days=days)
            stats = get_stats(self.db_path)
            res = json.dumps({
                "status": "ok",
                "deleted": deleted,
                "retention_days": days,
                "remaining": stats.get("total_records", 0),
                "size_bytes": stats.get("size_bytes", 0),
            }, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(res)))
            self.end_headers()
            self.wfile.write(res)
            return

        msg = json.dumps({"error": "Not Found", "path": path}).encode("utf-8")
        self.send_response(404)
        self._send_cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(msg)))
        self.end_headers()
        self.wfile.write(msg)


def run_server(
    db_path: Path,
    port: int = DEFAULT_SERVER_PORT,
    lhm_url: str = DEFAULT_LHM_URL,
    interval: float = 1.0,
    retention_days: int = DEFAULT_RETENTION_DAYS,
) -> None:
    init_db(db_path)
    HardwareAPIHandler.db_path = db_path

    # Start collector in background daemon thread
    def collector_worker() -> None:
        logger.info("Collector thread started for %s", lhm_url)
        last_prune = time.time()
        while True:
            try:
                fetch_and_record_once(db_path, lhm_url)
            except Exception as e:
                logger.debug("Telemetry error: %s", e)

            now = time.time()
            if now - last_prune > 21600:
                try:
                    pruned = prune_old_records(db_path, retention_days)
                    if pruned > 0:
                        logger.info("Pruned %d records older than %d days", pruned, retention_days)
                except Exception as e:
                    logger.debug("Prune error: %s", e)
                last_prune = now
            time.sleep(interval)

    collector_thread = threading.Thread(target=collector_worker, daemon=True, name="TelemetryCollector")
    collector_thread.start()

    server = ThreadingHTTPServer(("127.0.0.1", port), HardwareAPIHandler)
    logger.info("SQLite Hardware Telemetry Server running on http://127.0.0.1:%d/ (DB: %s)", port, db_path)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down telemetry server...")
    finally:
        server.server_close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="LibreHardwareMonitor SQLite Logger & API")
    parser.add_argument("--db", type=str, default=None, help="Path to SQLite database file")
    parser.add_argument("--serve", action="store_true", help="Run background collector and HTTP API server")
    parser.add_argument("--port", type=int, default=DEFAULT_SERVER_PORT, help="Port for HTTP API server (default: 8086)")
    parser.add_argument("--collect", action="store_true", help="Run background collector daemon only (no HTTP server)")
    parser.add_argument("--interval", type=float, default=1.0, help="Collection interval in seconds")
    parser.add_argument("--query", type=int, default=None, help="Query telemetry records for the last N seconds (outputs JSON)")
    parser.add_argument("--max-points", type=int, default=500, help="Max points for query downsampling (default: 500)")
    parser.add_argument("--stats", action="store_true", help="Display database statistics")
    parser.add_argument("--stats-json", action="store_true", help="Display database statistics as JSON")
    parser.add_argument("--insert-stdin", action="store_true", help="Read JSON object or array from stdin and insert into DB")
    parser.add_argument("--prune", type=int, default=None, help="Prune records older than N days")
    parser.add_argument("--export-csv", type=str, default=None, help="Export all records to CSV file")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    db_path = get_db_path(args.db)
    init_db(db_path)

    if args.serve:
        run_server(db_path, port=args.port, interval=args.interval)
        return

    if args.collect:
        run_collection_daemon(db_path, interval=args.interval)
        return

    if args.query is not None:
        data = query_telemetry(db_path, range_seconds=args.query, max_points=args.max_points)
        print(json.dumps(data, ensure_ascii=False))
        return

    if args.insert_stdin:
        raw = sys.stdin.read().strip()
        if not raw:
            print(json.dumps({"status": "error", "message": "Empty stdin"}))
            return
        try:
            payload = json.loads(raw)
            if isinstance(payload, list):
                count = insert_batch(db_path, payload)
            elif isinstance(payload, dict):
                count = insert_batch(db_path, [payload])
            else:
                count = 0
            print(json.dumps({"status": "ok", "inserted": count}))
        except Exception as e:
            print(json.dumps({"status": "error", "message": str(e)}))
        return

    if args.stats_json:
        stats = get_stats(db_path)
        print(json.dumps(stats, ensure_ascii=False, indent=2))
        return

    if args.stats:
        stats = get_stats(db_path)
        print("Hardware Telemetry SQLite Statistics:")
        print(f"  Database File: {stats.get('db_path')}")
        print(f"  Total Records: {stats.get('total_records', 0):,}")
        size_kb = stats.get('size_bytes', 0) / 1024
        print(f"  File Size:     {size_kb:.1f} KB")
        if stats.get('total_records', 0) > 0:
            earliest = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stats['earliest_ts'] / 1000))
            latest = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stats['latest_ts'] / 1000))
            print(f"  Time Span:     {earliest}  to  {latest}")
            print(f"  CPU Load:      Min: {stats['cpu']['min']}% | Avg: {stats['cpu']['avg']}% | Max: {stats['cpu']['max']}%")
            print(f"  GPU Load:      Min: {stats['gpu']['min']}% | Avg: {stats['gpu']['avg']}% | Max: {stats['gpu']['max']}%")
            print(f"  RAM Load:      Min: {stats['ram']['min']}% | Avg: {stats['ram']['avg']}% | Max: {stats['ram']['max']}%")
        return

    if args.prune is not None:
        deleted = prune_old_records(db_path, retention_days=args.prune)
        print(f"Pruned {deleted} records older than {args.prune} days.")
        return

    if args.export_csv:
        count = export_to_csv(db_path, Path(args.export_csv))
        print(f"Exported {count} records to {args.export_csv}")
        return

    # Default action if no flag specified: capture one reading
    result = fetch_and_record_once(db_path)
    if result:
        print(f"Recorded snapshot at {time.strftime('%H:%M:%S')}: CPU={result['cpu']}% GPU={result['gpu']}% RAM={result['ram']}% CPU_Temp={result['cpu_temp']}°C GPU_Temp={result['gpu_temp']}°C")
    else:
        print("Could not fetch data from LibreHardwareMonitor (is it running on port 8085?)")


if __name__ == "__main__":
    main()
