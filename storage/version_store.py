from __future__ import annotations

import json
import shutil
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator

from services.hasher import generate_file_sha256

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STORAGE_DIR = PROJECT_ROOT / "storage"
SNAPSHOT_DIR = STORAGE_DIR / "snapshots"
DATABASE_PATH = STORAGE_DIR / "versions.sqlite3"


def _connect() -> sqlite3.Connection:
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATABASE_PATH, timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


@contextmanager
def _connection() -> Generator[sqlite3.Connection, None, None]:
    connection = _connect()
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def initialize_store() -> None:
    with _connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS file_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_key TEXT NOT NULL,
                file_name TEXT NOT NULL,
                version INTEGER NOT NULL,
                file_hash TEXT NOT NULL,
                file_size INTEGER NOT NULL,
                file_type TEXT NOT NULL,
                source_path TEXT,
                hash_generated_at TEXT,
                registered_at TEXT NOT NULL,
                topic_id TEXT NOT NULL,
                transaction_id TEXT NOT NULL,
                sequence_number INTEGER,
                hashscan_url TEXT NOT NULL,
                snapshot_path TEXT NOT NULL,
                diff_json TEXT NOT NULL,
                UNIQUE(file_key, version),
                UNIQUE(file_key, file_hash)
            )
            """
        )
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(file_versions)").fetchall()
        }
        if "hash_generated_at" not in columns:
            connection.execute(
                "ALTER TABLE file_versions ADD COLUMN hash_generated_at TEXT"
            )
        if "source_path" not in columns:
            connection.execute("ALTER TABLE file_versions ADD COLUMN source_path TEXT")
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_versions_key ON file_versions(file_key, version)"
        )


def file_key(file_name: str) -> str:
    return Path(file_name).name.casefold()


def _record_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    record = dict(row)
    record["diff"] = json.loads(record.pop("diff_json"))
    return record


def get_latest(file_name: str) -> dict[str, Any] | None:
    with _connection() as connection:
        row = connection.execute(
            "SELECT * FROM file_versions WHERE file_key = ? ORDER BY version DESC LIMIT 1",
            (file_key(file_name),),
        ).fetchone()
    return _record_to_dict(row)


def get_versions(file_name: str) -> list[dict[str, Any]]:
    with _connection() as connection:
        rows = connection.execute(
            "SELECT * FROM file_versions WHERE file_key = ? ORDER BY version ASC",
            (file_key(file_name),),
        ).fetchall()
    records: list[dict[str, Any]] = []
    for row in rows:
        record = _record_to_dict(row)
        if record is not None:
            records.append(record)
    return records


def get_dashboard_data(limit: int = 20) -> dict[str, Any]:
    """Return aggregate counts and latest version per registered file."""
    if limit < 1:
        raise ValueError("limit debe ser mayor que cero.")
    with _connection() as connection:
        totals = connection.execute(
            """
            SELECT COUNT(DISTINCT file_key) AS file_count, COUNT(*) AS version_count
            FROM file_versions
            """
        ).fetchone()
        rows = connection.execute(
            """
            WITH latest AS (
                SELECT file_key, MAX(version) AS latest_version, COUNT(*) AS version_count
                FROM file_versions
                GROUP BY file_key
            )
            SELECT fv.*, latest.version_count
            FROM latest
            JOIN file_versions AS fv
              ON fv.file_key = latest.file_key
             AND fv.version = latest.latest_version
            ORDER BY fv.registered_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    files = []
    for row in rows:
        record = _record_to_dict(row)
        if record is not None:
            files.append(record)
    return {
        "file_count": int(totals["file_count"]) if totals else 0,
        "version_count": int(totals["version_count"]) if totals else 0,
        "record_count": int(totals["version_count"]) if totals else 0,
        "files": files,
    }


def update_source_path(file_name: str, source_path: str | Path) -> None:
    """Remember the last local path used to inspect a registered file."""
    with _connection() as connection:
        connection.execute(
            """
            UPDATE file_versions
            SET source_path = ?
            WHERE file_key = ?
              AND version = (
                  SELECT MAX(version) FROM file_versions WHERE file_key = ?
              )
            """,
            (str(Path(source_path).resolve()), file_key(file_name), file_key(file_name)),
        )


def get_version(file_name: str, version: int) -> dict[str, Any] | None:
    with _connection() as connection:
        row = connection.execute(
            "SELECT * FROM file_versions WHERE file_key = ? AND version = ?",
            (file_key(file_name), version),
        ).fetchone()
    return _record_to_dict(row)


def register_version(
    *,
    file_path: str | Path,
    file_name: str,
    file_hash: str,
    file_size: int,
    file_type: str,
    hash_generated_at: str | None = None,
    topic_id: str,
    transaction_id: str,
    sequence_number: int | None,
    hashscan_url: str,
    diff: dict[str, Any],
) -> dict[str, Any]:
    """Persist a Hedera-confirmed version and a local snapshot for later diffs."""
    source = Path(file_path)
    if generate_file_sha256(str(source)) != file_hash:
        raise ValueError("El archivo cambió mientras se registraba; vuelve a seleccionarlo.")

    key = file_key(file_name)
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    snapshot = SNAPSHOT_DIR / f"{key.replace('/', '_')}__{file_hash}{source.suffix.lower()}"
    temporary_snapshot = snapshot.with_suffix(snapshot.suffix + ".tmp")
    try:
        shutil.copy2(source, temporary_snapshot)
        temporary_snapshot.replace(snapshot)
        if generate_file_sha256(str(snapshot)) != file_hash:
            raise ValueError("La copia local no coincide con la huella registrada.")

        with _connection() as connection:
            latest_row = connection.execute(
                "SELECT version FROM file_versions WHERE file_key = ? ORDER BY version DESC LIMIT 1",
                (key,),
            ).fetchone()
            next_version = int(latest_row["version"]) + 1 if latest_row else 1
            registered_at = datetime.now(timezone.utc).isoformat()
            connection.execute(
                """
                INSERT INTO file_versions (
                    file_key, file_name, version, file_hash, file_size, file_type,
                    source_path, hash_generated_at, registered_at, topic_id, transaction_id, sequence_number,
                    hashscan_url, snapshot_path, diff_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    key,
                    Path(file_name).name,
                    next_version,
                    file_hash,
                    file_size,
                    file_type,
                    str(source.resolve()),
                    hash_generated_at,
                    registered_at,
                    topic_id,
                    transaction_id,
                    sequence_number,
                    hashscan_url,
                    str(snapshot),
                    json.dumps(diff, ensure_ascii=False, separators=(",", ":")),
                ),
            )
            row = connection.execute(
                "SELECT * FROM file_versions WHERE file_key = ? AND version = ?",
                (key, next_version),
            ).fetchone()
        record = _record_to_dict(row)
        if record is None:
            raise RuntimeError("No se pudo recuperar la versión guardada.")
        return record
    except Exception:
        temporary_snapshot.unlink(missing_ok=True)
        if snapshot.exists():
            with _connection() as connection:
                referenced = connection.execute(
                    "SELECT 1 FROM file_versions WHERE snapshot_path = ?", (str(snapshot),)
                ).fetchone()
            if not referenced:
                snapshot.unlink(missing_ok=True)
        raise
