import json
import sqlite3
import tempfile
import time
from pathlib import Path

import pytest
from hw_logger import (
    DEFAULT_RETENTION_DAYS,
    export_to_csv,
    extract_telemetry,
    get_connection,
    get_stats,
    init_db,
    insert_batch,
    insert_point,
    prune_old_records,
    query_telemetry,
)


@pytest.fixture
def temp_db(tmp_path: Path) -> Path:
    db_file = tmp_path / "test_hardware.db"
    init_db(db_file)
    return db_file


def test_init_db(temp_db: Path) -> None:
    assert temp_db.exists()
    with get_connection(temp_db) as conn:
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='telemetry';"
        ).fetchall()
        assert len(tables) == 1
        indexes = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_telemetry_timestamp';"
        ).fetchall()
        assert len(indexes) == 1


def test_insert_and_query_single_point(temp_db: Path) -> None:
    now_ms = int(time.time() * 1000)
    row_id = insert_point(
        temp_db,
        timestamp=now_ms,
        cpu=15.2,
        gpu=42.0,
        ram=70.5,
        cpu_temp=55.0,
        gpu_temp=48.5,
    )
    assert row_id > 0

    results = query_telemetry(temp_db, range_seconds=60)
    assert len(results) == 1
    assert results[0]["cpu"] == "15.2"
    assert results[0]["gpu"] == "42.0"
    assert results[0]["ram"] == "70.5"
    assert results[0]["cpuTemp"] == "55.0"
    assert results[0]["gpuTemp"] == "48.5"


def test_insert_batch_and_downsampling(temp_db: Path) -> None:
    now_ms = int(time.time() * 1000)
    records = []
    # Insert 100 records spaced 1 second apart
    for i in range(100):
        records.append({
            "timestamp": now_ms - (100 - i) * 1000,
            "cpu": 10.0 + (i % 20),
            "gpu": 20.0,
            "ram": 50.0,
            "cpu_temp": 45.0,
            "gpu_temp": 40.0,
        })
    count = insert_batch(temp_db, records)
    assert count == 100

    # Query without downsampling
    full_data = query_telemetry(temp_db, range_seconds=150, max_points=200)
    assert len(full_data) == 100

    # Query with downsampling max_points=10
    downsampled = query_telemetry(temp_db, range_seconds=150, max_points=10)
    assert len(downsampled) <= 15
    assert len(downsampled) > 0


def test_extract_telemetry_from_mock_tree() -> None:
    mock_tree = {
        "Text": "SensorTree",
        "Children": [
            {
                "Text": "Intel Core i7",
                "Children": [
                    {"Text": "CPU Total", "Value": "24.5 %"},
                    {"Text": "CPU Package", "Value": "62.0 °C"},
                ],
            },
            {
                "Text": "NVIDIA GeForce RTX",
                "Children": [
                    {"Text": "GPU Core", "Value": "12.3 %"},
                    {"Text": "GPU Hot Spot", "Value": "54.8 °C"},
                ],
            },
            {
                "Text": "Generic Memory",
                "Children": [
                    {"Text": "Memory", "Value": "58.2 %"},
                ],
            },
        ],
    }
    extracted = extract_telemetry(mock_tree)
    assert extracted["cpu"] == 24.5
    assert extracted["gpu"] == 12.3
    assert extracted["ram"] == 58.2
    assert extracted["cpu_temp"] == 62.0
    assert extracted["gpu_temp"] == 54.8


def test_get_stats(temp_db: Path) -> None:
    now_ms = int(time.time() * 1000)
    insert_point(temp_db, now_ms - 2000, 10.0, 20.0, 30.0, 40.0, 50.0)
    insert_point(temp_db, now_ms, 30.0, 40.0, 50.0, 60.0, 70.0)

    stats = get_stats(temp_db)
    assert stats["total_records"] == 2
    assert stats["cpu"]["min"] == 10.0
    assert stats["cpu"]["max"] == 30.0
    assert stats["cpu"]["avg"] == 20.0


def test_prune_old_records(temp_db: Path) -> None:
    now_ms = int(time.time() * 1000)
    ten_days_ago_ms = now_ms - (10 * 86400 * 1000)
    insert_point(temp_db, ten_days_ago_ms, 5.0, 5.0, 5.0, 40.0, 40.0)
    insert_point(temp_db, now_ms, 10.0, 10.0, 10.0, 45.0, 45.0)

    stats_before = get_stats(temp_db)
    assert stats_before["total_records"] == 2

    deleted = prune_old_records(temp_db, retention_days=7)
    assert deleted == 1

    stats_after = get_stats(temp_db)
    assert stats_after["total_records"] == 1


def test_export_to_csv(temp_db: Path, tmp_path: Path) -> None:
    now_ms = int(time.time() * 1000)
    insert_point(temp_db, now_ms, 12.5, 34.5, 56.5, 45.0, 40.0)
    csv_file = tmp_path / "export.csv"
    count = export_to_csv(temp_db, csv_file)
    assert count == 1
    assert csv_file.exists()
    content = csv_file.read_text(encoding="utf-8")
    assert "cpu_percent" in content
    assert "12.5" in content


def test_http_api_endpoints(temp_db: Path) -> None:
    from http.server import ThreadingHTTPServer
    import urllib.request
    from hw_logger import HardwareAPIHandler

    HardwareAPIHandler.db_path = temp_db
    server = ThreadingHTTPServer(("127.0.0.1", 0), HardwareAPIHandler)
    port = server.server_address[1]

    import threading
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    base_url = f"http://127.0.0.1:{port}"
    try:
        # Test POST insertion
        post_data = json.dumps([
            {"t": 1700000000000, "cpu": 15.0, "gpu": 25.0, "ram": 55.0, "cpuTemp": 50.0, "gpuTemp": 45.0}
        ]).encode("utf-8")
        req = urllib.request.Request(f"{base_url}/api/hardware", data=post_data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=2) as resp:
            assert resp.status == 200
            res_json = json.loads(resp.read().decode("utf-8"))
            assert res_json["status"] == "ok"
            assert res_json["inserted"] == 1

        # Test GET query
        with urllib.request.urlopen(f"{base_url}/api/hardware?range=1000000000", timeout=2) as resp:
            assert resp.status == 200
            query_res = json.loads(resp.read().decode("utf-8"))
            assert len(query_res) == 1
            assert query_res[0]["cpu"] == "15.0"

        # Test GET stats
        with urllib.request.urlopen(f"{base_url}/api/hardware/stats", timeout=2) as resp:
            assert resp.status == 200
            stats_res = json.loads(resp.read().decode("utf-8"))
            assert stats_res["total_records"] == 1

        # Test POST /api/hardware/prune
        prune_post_data = json.dumps({"days": 0}).encode("utf-8")
        prune_req = urllib.request.Request(f"{base_url}/api/hardware/prune", data=prune_post_data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(prune_req, timeout=2) as resp:
            assert resp.status == 200
            prune_res = json.loads(resp.read().decode("utf-8"))
            assert prune_res["status"] == "ok"
            assert "deleted" in prune_res

        # Test GET /api/hardware/prune
        with urllib.request.urlopen(f"{base_url}/api/hardware/prune?days=30", timeout=2) as resp:
            assert resp.status == 200
            prune_get_res = json.loads(resp.read().decode("utf-8"))
            assert prune_get_res["status"] == "ok"
    finally:
        server.shutdown()
        server.server_close()

