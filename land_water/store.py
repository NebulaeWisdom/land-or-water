from __future__ import annotations

import sqlite3
from pathlib import Path

COLUMNS = [
    "test_name",
    "model",
    "longitude",
    "latitude",
    "truth",
    "answer",
    "status",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "usage_json",
    "response_json",
    "request_json",
    "latency_seconds",
    "attempts",
    "error",
    "created_at",
]

# Stats and the map only read these columns; raw request/response JSON stays for exports.
VIEW_COLUMNS = [
    "longitude",
    "latitude",
    "truth",
    "answer",
    "status",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "usage_json",
    "latency_seconds",
]


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS results (
                test_name TEXT NOT NULL,
                model TEXT,
                longitude REAL NOT NULL,
                latitude REAL NOT NULL,
                truth TEXT,
                answer TEXT,
                status TEXT,
                input_tokens INTEGER,
                output_tokens INTEGER,
                total_tokens INTEGER,
                usage_json TEXT,
                response_json TEXT,
                request_json TEXT,
                latency_seconds REAL,
                attempts INTEGER,
                error TEXT,
                created_at TEXT,
                UNIQUE (test_name, longitude, latitude)
            )
            """
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def completed_points(self, test_name: str) -> set[tuple[float, float]]:
        rows = self.connection.execute(
            "SELECT longitude, latitude FROM results WHERE test_name = ?",
            (test_name,),
        ).fetchall()
        return {(row["longitude"], row["latitude"]) for row in rows}

    def save_result(self, row: dict) -> None:
        placeholders = ", ".join("?" for _ in COLUMNS)
        self.connection.execute(
            f"INSERT OR IGNORE INTO results ({', '.join(COLUMNS)}) VALUES ({placeholders})",
            [row[column] for column in COLUMNS],
        )
        self.connection.commit()

    def results(self, test_name: str | None = None) -> list[dict]:
        if test_name is None:
            rows = self.connection.execute(
                "SELECT * FROM results ORDER BY test_name, latitude, longitude"
            ).fetchall()
        else:
            rows = self.connection.execute(
                "SELECT * FROM results WHERE test_name = ? ORDER BY latitude, longitude",
                (test_name,),
            ).fetchall()
        return [dict(row) for row in rows]

    def test_names(self) -> list[str]:
        rows = self.connection.execute(
            "SELECT DISTINCT test_name FROM results ORDER BY test_name"
        ).fetchall()
        return [row["test_name"] for row in rows]

    def result_stamp(self, test_name: str) -> tuple[int, str | None]:
        """Row count and newest created_at for one test: the panel's change marker."""
        row = self.connection.execute(
            "SELECT COUNT(*) AS count, MAX(created_at) AS latest_at FROM results WHERE test_name = ?",
            (test_name,),
        ).fetchone()
        return row["count"], row["latest_at"]

    def view_rows(self, test_name: str) -> list[dict]:
        """Stats/map rows (no response_json/request_json), ordered like results()."""
        columns = ", ".join(VIEW_COLUMNS)
        rows = self.connection.execute(
            f"SELECT {columns} FROM results WHERE test_name = ? ORDER BY latitude, longitude",
            (test_name,),
        ).fetchall()
        return [dict(row) for row in rows]

    def progress(self, test_name: str, since: str | None = None) -> dict:
        """SQL-aggregated run progress for one test, never touching response_json."""
        query = """
            SELECT
                COUNT(*) AS count,
                SUM(status = 'failed') AS failed,
                SUM(status = 'invalid') AS invalid,
                SUM(input_tokens) AS input_tokens,
                SUM(output_tokens) AS output_tokens,
                SUM(total_tokens) AS total_tokens,
                AVG(latency_seconds) AS avg_latency_seconds,
                MAX(created_at) AS latest_at
            FROM results
            WHERE test_name = ?
        """
        parameters: list = [test_name]
        if since is not None:
            query += " AND created_at >= ?"
            parameters.append(since)
        row = self.connection.execute(query, parameters).fetchone()
        return {
            "count": row["count"],
            "failed": row["failed"] or 0,
            "invalid": row["invalid"] or 0,
            "input_tokens": row["input_tokens"],
            "output_tokens": row["output_tokens"],
            "total_tokens": row["total_tokens"],
            "avg_latency_seconds": row["avg_latency_seconds"],
            "latest_at": row["latest_at"],
        }

    def rename_results(self, old_name: str, new_name: str) -> int:
        """Rename one test in place; returns affected row count."""
        cursor = self.connection.execute(
            "UPDATE results SET test_name = ? WHERE test_name = ?",
            (new_name, old_name),
        )
        self.connection.commit()
        return cursor.rowcount

    def delete_results(self, test_name: str) -> int:
        """Delete every row of one test; returns affected row count."""
        cursor = self.connection.execute(
            "DELETE FROM results WHERE test_name = ?",
            (test_name,),
        )
        self.connection.commit()
        return cursor.rowcount
