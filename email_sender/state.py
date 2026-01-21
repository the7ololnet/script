"""
State management module using SQLite for idempotency and persistence.

Tracks recipient status, send history, and suppression across restarts.
"""

import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional, Iterator, Tuple


class RecipientStatus(Enum):
    """Status of a recipient in the campaign."""
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    SUPPRESSED = "suppressed"
    SKIPPED = "skipped"  # e.g., not in allowlist


@dataclass
class Recipient:
    """Recipient record from database."""
    email: str
    status: RecipientStatus
    last_error: Optional[str] = None
    last_attempt_at: Optional[datetime] = None
    attempts: int = 0


@dataclass
class SendRecord:
    """Record of a send attempt."""
    message_uuid: str
    email: str
    smtp_server_id: str
    mode: str
    timestamp: datetime
    result: str
    error: Optional[str] = None


class StateStore:
    """
    SQLite-based state store for campaign persistence.
    
    Provides idempotent sending by tracking what has been sent.
    """
    
    def __init__(self, db_path: str):
        """
        Initialize state store.
        
        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = db_path
        self._init_database()
    
    def _init_database(self):
        """Create database tables if they don't exist."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # Recipients table - tracks status of each email address
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS recipients (
                    email TEXT PRIMARY KEY,
                    status TEXT NOT NULL DEFAULT 'pending',
                    last_error TEXT,
                    last_attempt_at TEXT,
                    attempts INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Sends table - detailed log of each send attempt
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sends (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_uuid TEXT NOT NULL UNIQUE,
                    email TEXT NOT NULL,
                    smtp_server_id TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    result TEXT NOT NULL,
                    error TEXT,
                    subject TEXT,
                    FOREIGN KEY (email) REFERENCES recipients(email)
                )
            """)
            
            # Suppression table - persistent suppression list
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS suppression (
                    email TEXT PRIMARY KEY,
                    reason TEXT,
                    added_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # AI cache table - cache generated content
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS ai_cache (
                    email TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    subject TEXT,
                    body_text TEXT,
                    body_html TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (email, mode)
                )
            """)
            
            # Create indexes for performance
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_recipients_status 
                ON recipients(status)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_sends_email 
                ON sends(email)
            """)
            
            conn.commit()
    
    @contextmanager
    def _get_connection(self) -> Iterator[sqlite3.Connection]:
        """Get database connection with automatic cleanup."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()
    
    def add_recipients(self, emails: List[str]) -> int:
        """
        Add recipients to the database (ignore if already exists).
        
        Args:
            emails: List of email addresses
            
        Returns:
            Number of new recipients added
        """
        added = 0
        with self._get_connection() as conn:
            cursor = conn.cursor()
            for email in emails:
                try:
                    cursor.execute(
                        "INSERT OR IGNORE INTO recipients (email, status) VALUES (?, ?)",
                        (email.lower(), RecipientStatus.PENDING.value)
                    )
                    if cursor.rowcount > 0:
                        added += 1
                except sqlite3.IntegrityError:
                    pass
            conn.commit()
        return added
    
    def get_pending_recipients(self, limit: Optional[int] = None) -> List[Recipient]:
        """
        Get recipients that haven't been processed yet.
        
        Args:
            limit: Maximum number to return
            
        Returns:
            List of pending recipients
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            query = """
                SELECT email, status, last_error, last_attempt_at, attempts
                FROM recipients 
                WHERE status = ?
                ORDER BY created_at
            """
            if limit:
                query += f" LIMIT {limit}"
            
            cursor.execute(query, (RecipientStatus.PENDING.value,))
            
            return [
                Recipient(
                    email=row['email'],
                    status=RecipientStatus(row['status']),
                    last_error=row['last_error'],
                    last_attempt_at=datetime.fromisoformat(row['last_attempt_at']) if row['last_attempt_at'] else None,
                    attempts=row['attempts']
                )
                for row in cursor.fetchall()
            ]
    
    def get_recipient_status(self, email: str) -> Optional[RecipientStatus]:
        """Get the current status of a recipient."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT status FROM recipients WHERE email = ?",
                (email.lower(),)
            )
            row = cursor.fetchone()
            if row:
                return RecipientStatus(row['status'])
            return None
    
    def update_recipient_status(
        self,
        email: str,
        status: RecipientStatus,
        error: Optional[str] = None
    ):
        """
        Update recipient status after a send attempt.
        
        Args:
            email: Email address
            status: New status
            error: Error message if failed
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE recipients 
                SET status = ?, 
                    last_error = ?,
                    last_attempt_at = ?,
                    attempts = attempts + 1
                WHERE email = ?
            """, (
                status.value,
                error,
                datetime.now(timezone.utc).isoformat(),
                email.lower()
            ))
            conn.commit()
    
    def record_send(
        self,
        email: str,
        smtp_server_id: str,
        mode: str,
        result: str,
        error: Optional[str] = None,
        subject: Optional[str] = None
    ) -> str:
        """
        Record a send attempt.
        
        Args:
            email: Recipient email
            smtp_server_id: ID of SMTP server used
            mode: Campaign mode (WARMUP/OFFER)
            result: Result (success/failed)
            error: Error message if failed
            subject: Email subject
            
        Returns:
            Generated message UUID
        """
        message_uuid = str(uuid.uuid4())
        
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO sends (
                    message_uuid, email, smtp_server_id, mode, 
                    timestamp, result, error, subject
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                message_uuid,
                email.lower(),
                smtp_server_id,
                mode,
                datetime.now(timezone.utc).isoformat(),
                result,
                error,
                subject
            ))
            conn.commit()
        
        return message_uuid
    
    def is_suppressed(self, email: str) -> bool:
        """Check if email is in suppression list."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT 1 FROM suppression WHERE email = ?",
                (email.lower(),)
            )
            return cursor.fetchone() is not None
    
    def add_to_suppression(self, email: str, reason: str = "hard_bounce"):
        """Add email to suppression list."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR REPLACE INTO suppression (email, reason) VALUES (?, ?)",
                (email.lower(), reason)
            )
            conn.commit()
    
    def load_suppression_file(self, filepath: str) -> int:
        """
        Load suppression list from file.
        
        Args:
            filepath: Path to suppression file
            
        Returns:
            Number of emails added
        """
        added = 0
        with open(filepath, 'r', encoding='utf-8') as f:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                for line in f:
                    email = line.strip().lower()
                    if email and '@' in email:
                        cursor.execute(
                            "INSERT OR IGNORE INTO suppression (email, reason) VALUES (?, ?)",
                            (email, "file_import")
                        )
                        if cursor.rowcount > 0:
                            added += 1
                conn.commit()
        return added
    
    def get_cached_content(self, email: str, mode: str) -> Optional[dict]:
        """Get cached AI content for a recipient."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT subject, body_text, body_html 
                FROM ai_cache 
                WHERE email = ? AND mode = ?
            """, (email.lower(), mode))
            row = cursor.fetchone()
            if row:
                return {
                    'subject': row['subject'],
                    'body_text': row['body_text'],
                    'body_html': row['body_html']
                }
            return None
    
    def cache_content(
        self,
        email: str,
        mode: str,
        subject: str,
        body_text: str,
        body_html: str
    ):
        """Cache AI-generated content for a recipient."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO ai_cache 
                (email, mode, subject, body_text, body_html)
                VALUES (?, ?, ?, ?, ?)
            """, (email.lower(), mode, subject, body_text, body_html))
            conn.commit()
    
    def get_statistics(self) -> dict:
        """
        Get campaign statistics.
        
        Returns:
            Dictionary with campaign stats
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # Count by status
            cursor.execute("""
                SELECT status, COUNT(*) as count 
                FROM recipients 
                GROUP BY status
            """)
            status_counts = {row['status']: row['count'] for row in cursor.fetchall()}
            
            # Count by server
            cursor.execute("""
                SELECT smtp_server_id, result, COUNT(*) as count
                FROM sends
                GROUP BY smtp_server_id, result
            """)
            server_stats = {}
            for row in cursor.fetchall():
                server_id = row['smtp_server_id']
                if server_id not in server_stats:
                    server_stats[server_id] = {'success': 0, 'failed': 0}
                if row['result'] == 'success':
                    server_stats[server_id]['success'] = row['count']
                else:
                    server_stats[server_id]['failed'] = row['count']
            
            # Total sends
            cursor.execute("SELECT COUNT(*) as total FROM sends")
            total_sends = cursor.fetchone()['total']
            
            # Suppressed count
            cursor.execute("SELECT COUNT(*) as total FROM suppression")
            suppressed = cursor.fetchone()['total']
            
            return {
                'by_status': status_counts,
                'by_server': server_stats,
                'total_sends': total_sends,
                'suppressed': suppressed,
                'pending': status_counts.get('pending', 0),
                'sent': status_counts.get('sent', 0),
                'failed': status_counts.get('failed', 0),
            }
    
    def get_error_summary(self) -> List[Tuple[str, int]]:
        """Get summary of errors by type."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT error, COUNT(*) as count
                FROM sends
                WHERE error IS NOT NULL
                GROUP BY error
                ORDER BY count DESC
                LIMIT 20
            """)
            return [(row['error'], row['count']) for row in cursor.fetchall()]
