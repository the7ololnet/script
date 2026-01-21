from __future__ import annotations

import sqlite3
from typing import Dict, Iterable, List, Optional

from utils import now_utc_iso


class StateStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row

    def initialize(self) -> None:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS recipients (
                email TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                last_error TEXT,
                last_error_category TEXT,
                last_attempt_at TEXT,
                suppressed_reason TEXT
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS sends (
                message_uuid TEXT PRIMARY KEY,
                email TEXT NOT NULL,
                smtp_server_id TEXT NOT NULL,
                mode TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                result TEXT NOT NULL,
                error_category TEXT,
                error_message TEXT
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS ai_cache (
                email TEXT NOT NULL,
                mode TEXT NOT NULL,
                subject TEXT NOT NULL,
                body_text TEXT NOT NULL,
                body_html TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (email, mode)
            )
            """
        )
        self.conn.commit()

    def ensure_recipients(self, emails: Iterable[str], status: str = "pending") -> None:
        cursor = self.conn.cursor()
        for email in emails:
            cursor.execute(
                """
                INSERT INTO recipients (email, status)
                VALUES (?, ?)
                ON CONFLICT(email) DO NOTHING
                """,
                (email, status),
            )
        self.conn.commit()

    def mark_suppressed(self, email: str, reason: str) -> None:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            UPDATE recipients
            SET status = 'suppressed', suppressed_reason = ?
            WHERE email = ? AND status != 'sent'
            """,
            (reason, email),
        )
        self.conn.commit()

    def mark_sent(self, email: str) -> None:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            UPDATE recipients
            SET status = 'sent', last_error = NULL, last_error_category = NULL, last_attempt_at = ?
            WHERE email = ?
            """,
            (now_utc_iso(), email),
        )
        self.conn.commit()

    def mark_failed(self, email: str, error_category: str, error_message: str) -> None:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            UPDATE recipients
            SET status = 'failed', last_error = ?, last_error_category = ?, last_attempt_at = ?
            WHERE email = ?
            """,
            (error_message, error_category, now_utc_iso(), email),
        )
        self.conn.commit()

    def record_send(
        self,
        message_uuid: str,
        email: str,
        smtp_server_id: str,
        mode: str,
        result: str,
        error_category: Optional[str],
        error_message: Optional[str],
    ) -> None:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO sends (
                message_uuid, email, smtp_server_id, mode, timestamp,
                result, error_category, error_message
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                message_uuid,
                email,
                smtp_server_id,
                mode,
                now_utc_iso(),
                result,
                error_category,
                error_message,
            ),
        )
        self.conn.commit()

    def get_recipients_to_send(self, include_failed: bool = True) -> List[str]:
        cursor = self.conn.cursor()
        statuses = ("pending", "failed") if include_failed else ("pending",)
        placeholders = ",".join("?" for _ in statuses)
        query = f"SELECT email FROM recipients WHERE status IN ({placeholders})"
        cursor.execute(query, statuses)
        return [row["email"] for row in cursor.fetchall()]

    def get_recipient(self, email: str) -> Optional[Dict[str, str]]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM recipients WHERE email = ?", (email,))
        row = cursor.fetchone()
        return dict(row) if row else None

    def cache_content(self, email: str, mode: str, content: Dict[str, str]) -> None:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO ai_cache (email, mode, subject, body_text, body_html, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(email, mode) DO UPDATE SET
                subject=excluded.subject,
                body_text=excluded.body_text,
                body_html=excluded.body_html,
                updated_at=excluded.updated_at
            """,
            (
                email,
                mode,
                content["subject"],
                content["body_text"],
                content["body_html"],
                now_utc_iso(),
            ),
        )
        self.conn.commit()

    def get_cached_content(self, email: str, mode: str) -> Optional[Dict[str, str]]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT subject, body_text, body_html FROM ai_cache WHERE email = ? AND mode = ?",
            (email, mode),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return {"subject": row["subject"], "body_text": row["body_text"], "body_html": row["body_html"]}

    def close(self) -> None:
        self.conn.close()
