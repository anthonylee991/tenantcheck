"""Durable submission state (SQLite, stdlib only).

A submission is a URL someone asked us to verify, plus what we found. This is the
state the API reads and writes; keeping it on disk means a restart does not lose a
user's result. Deliberately simple — one table, one row per submission.
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS submissions (
    id               TEXT PRIMARY KEY,
    url              TEXT NOT NULL,
    status           TEXT NOT NULL,
    ownership_method TEXT,
    instructions     TEXT,
    baseline_status  TEXT,
    findings_count   INTEGER NOT NULL DEFAULT 0,
    report           TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
)
"""

_UPDATABLE = (
    "status",
    "ownership_method",
    "instructions",
    "baseline_status",
    "findings_count",
    "report",
)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class Submission:
    id: str
    url: str
    status: str
    ownership_method: str | None = None
    instructions: str | None = None
    baseline_status: str | None = None
    findings_count: int = 0
    report: str | None = None
    created_at: str = ""
    updated_at: str = ""


class SubmissionStore:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(_SCHEMA)
        self._conn.commit()

    def create(self, url: str) -> Submission:
        now = _now()
        submission = Submission(id=uuid.uuid4().hex, url=url, status="pending",
                                created_at=now, updated_at=now)
        self._conn.execute(
            "INSERT INTO submissions (id, url, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (submission.id, submission.url, submission.status, now, now),
        )
        self._conn.commit()
        return submission

    def get(self, submission_id: str) -> Submission | None:
        row = self._conn.execute(
            "SELECT * FROM submissions WHERE id = ?", (submission_id,)
        ).fetchone()
        return Submission(**dict(row)) if row else None

    def all(self) -> list[Submission]:
        rows = self._conn.execute(
            "SELECT * FROM submissions ORDER BY created_at DESC"
        ).fetchall()
        return [Submission(**dict(row)) for row in rows]

    def update(self, submission: Submission) -> Submission:
        now = _now()
        assignments = ", ".join(f"{field} = ?" for field in _UPDATABLE)
        values = [getattr(submission, field) for field in _UPDATABLE]
        self._conn.execute(
            f"UPDATE submissions SET {assignments}, updated_at = ? WHERE id = ?",
            (*values, now, submission.id),
        )
        self._conn.commit()
        updated = self.get(submission.id)
        assert updated is not None
        return updated

    def close(self) -> None:
        self._conn.close()
