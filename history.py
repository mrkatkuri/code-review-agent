"""SQLite review history. Reports contain source excerpts; store locally."""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from schemas import ReviewReport


class ReviewHistory:
    def __init__(self, path: Path | str = ".data/reviews.sqlite3"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS reviews (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, repository TEXT NOT NULL, head_sha TEXT NOT NULL, report TEXT NOT NULL)"
            )

    def connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def save(self, report: ReviewReport) -> str:
        review_id = uuid4().hex
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO reviews VALUES (?, ?, ?, ?, ?)",
                (
                    review_id,
                    datetime.now(UTC).isoformat(),
                    report.repository,
                    report.head_sha,
                    report.model_dump_json(),
                ),
            )
        return review_id

    def list(self, limit: int = 50) -> list[dict]:
        with self.connect() as connection:
            connection.row_factory = sqlite3.Row
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT id, created_at, repository, head_sha FROM reviews ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                )
            ]

    def get(self, review_id: str) -> ReviewReport:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT report FROM reviews WHERE id = ?", (review_id,)
            ).fetchone()
        if row is None:
            raise ValueError("Review not found.")
        return ReviewReport.model_validate_json(row[0])
