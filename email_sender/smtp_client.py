"""
SMTP client module with connection pooling, retries, and circuit breaker.

Provides reliable email sending with proper error handling.
"""

import logging
import smtplib
import socket
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from typing import Dict, List, Optional, Tuple
from threading import Lock

from .config import SMTPConfig
from .utils import classify_smtp_error, is_retryable_error, calculate_backoff

logger = logging.getLogger(__name__)


@dataclass
class ServerHealth:
    """Health tracking for an SMTP server."""
    server_id: str
    consecutive_failures: int = 0
    total_failures: int = 0
    total_successes: int = 0
    last_failure_at: Optional[datetime] = None
    cooldown_until: Optional[datetime] = None
    
    # Circuit breaker thresholds
    failure_threshold: int = 5  # Failures before cooldown
    cooldown_seconds: int = 300  # 5 minutes cooldown
    
    def record_success(self):
        """Record a successful send."""
        self.consecutive_failures = 0
        self.total_successes += 1
    
    def record_failure(self):
        """Record a failed send and potentially trigger cooldown."""
        self.consecutive_failures += 1
        self.total_failures += 1
        self.last_failure_at = datetime.now(timezone.utc)
        
        if self.consecutive_failures >= self.failure_threshold:
            self.cooldown_until = datetime.now(timezone.utc) + timedelta(seconds=self.cooldown_seconds)
            logger.warning(
                f"Server {self.server_id} in cooldown until "
                f"{self.cooldown_until.isoformat()} after {self.consecutive_failures} failures"
            )
    
    def is_available(self) -> bool:
        """Check if server is available (not in cooldown)."""
        if self.cooldown_until is None:
            return True
        if datetime.now(timezone.utc) > self.cooldown_until:
            # Cooldown expired, reset
            self.cooldown_until = None
            self.consecutive_failures = 0
            return True
        return False


@dataclass
class SendResult:
    """Result of a send attempt."""
    success: bool
    server_id: str
    error: Optional[str] = None
    error_type: Optional[str] = None
    attempts: int = 1
    duration_ms: float = 0


class SMTPConnectionPool:
    """
    Connection pool for SMTP servers.
    
    Maintains persistent connections and handles reconnection on failures.
    """
    
    def __init__(
        self,
        configs: List[SMTPConfig],
        rotation: str = "roundrobin",
        max_retries: int = 3,
        retry_backoff_base: float = 2.0,
        retry_max_delay: float = 60.0
    ):
        """
        Initialize connection pool.
        
        Args:
            configs: List of SMTP configurations
            rotation: Server rotation strategy ("roundrobin" or "weighted")
            max_retries: Maximum retry attempts per send
            retry_backoff_base: Base for exponential backoff
            retry_max_delay: Maximum delay between retries
        """
        self.configs = {f"server_{i}": cfg for i, cfg in enumerate(configs)}
        self.rotation = rotation
        self.max_retries = max_retries
        self.retry_backoff_base = retry_backoff_base
        self.retry_max_delay = retry_max_delay
        
        # Connection and health tracking
        self._connections: Dict[str, smtplib.SMTP] = {}
        self._health: Dict[str, ServerHealth] = {
            sid: ServerHealth(server_id=sid) for sid in self.configs
        }
        self._send_counts: Dict[str, int] = {sid: 0 for sid in self.configs}
        self._lock = Lock()
        self._current_index = 0
    
    def _get_next_server(self) -> Optional[str]:
        """
        Get next available server based on rotation strategy.
        
        Returns:
            Server ID or None if all servers unavailable
        """
        with self._lock:
            server_ids = list(self.configs.keys())
            
            if self.rotation == "roundrobin":
                # Try each server in round-robin order
                for _ in range(len(server_ids)):
                    server_id = server_ids[self._current_index % len(server_ids)]
                    self._current_index += 1
                    
                    if self._health[server_id].is_available():
                        return server_id
            else:
                # Weighted: prefer server with fewer failures
                available = [
                    sid for sid in server_ids
                    if self._health[sid].is_available()
                ]
                if available:
                    # Sort by consecutive failures (ascending)
                    available.sort(key=lambda s: self._health[s].consecutive_failures)
                    return available[0]
            
            return None
    
    def _get_connection(self, server_id: str) -> smtplib.SMTP:
        """
        Get or create SMTP connection for a server.
        
        Args:
            server_id: Server identifier
            
        Returns:
            SMTP connection
        """
        config = self.configs[server_id]
        
        # Check if we have an existing connection
        if server_id in self._connections:
            try:
                # Test connection with NOOP
                self._connections[server_id].noop()
                return self._connections[server_id]
            except Exception:
                # Connection dead, close and recreate
                try:
                    self._connections[server_id].quit()
                except Exception:
                    pass
                del self._connections[server_id]
        
        # Create new connection
        logger.debug(f"Creating new connection to {config.server}:{config.port}")
        
        if config.use_ssl:
            # Implicit TLS (SSL from start)
            conn = smtplib.SMTP_SSL(
                config.server,
                config.port,
                timeout=config.timeout
            )
        else:
            # Plain connection (will upgrade with STARTTLS if configured)
            conn = smtplib.SMTP(
                config.server,
                config.port,
                timeout=config.timeout
            )
            
            if config.use_tls:
                # Upgrade to TLS
                conn.ehlo()
                conn.starttls()
                conn.ehlo()
        
        # Authenticate
        conn.login(config.user, config.password)
        
        # Cache connection
        self._connections[server_id] = conn
        
        return conn
    
    def _close_connection(self, server_id: str):
        """Close and remove a connection."""
        if server_id in self._connections:
            try:
                self._connections[server_id].quit()
            except Exception:
                pass
            del self._connections[server_id]
    
    def send(
        self,
        message: MIMEMultipart,
        to_email: str,
        envelope_from: str
    ) -> SendResult:
        """
        Send an email with automatic retries and server failover.
        
        Args:
            message: MIME message to send
            to_email: Recipient email address
            envelope_from: MAIL FROM address
            
        Returns:
            SendResult with outcome details
        """
        start_time = time.time()
        last_error = None
        last_error_type = None
        attempts = 0
        servers_tried = set()
        
        while attempts < self.max_retries:
            # Get next available server
            server_id = self._get_next_server()
            
            if server_id is None:
                # All servers unavailable
                logger.error("All SMTP servers unavailable or in cooldown")
                return SendResult(
                    success=False,
                    server_id="none",
                    error="All servers unavailable",
                    error_type="connection",
                    attempts=attempts,
                    duration_ms=(time.time() - start_time) * 1000
                )
            
            servers_tried.add(server_id)
            attempts += 1
            
            try:
                conn = self._get_connection(server_id)
                
                # Send the email
                conn.sendmail(
                    envelope_from,
                    [to_email],
                    message.as_string()
                )
                
                # Success!
                self._health[server_id].record_success()
                self._send_counts[server_id] += 1
                
                logger.debug(f"Successfully sent to {to_email} via {server_id}")
                
                return SendResult(
                    success=True,
                    server_id=server_id,
                    attempts=attempts,
                    duration_ms=(time.time() - start_time) * 1000
                )
                
            except smtplib.SMTPRecipientsRefused as e:
                # Permanent failure - don't retry
                error_msg = str(e)
                self._health[server_id].record_failure()
                self._close_connection(server_id)
                
                return SendResult(
                    success=False,
                    server_id=server_id,
                    error=error_msg,
                    error_type="permanent",
                    attempts=attempts,
                    duration_ms=(time.time() - start_time) * 1000
                )
                
            except smtplib.SMTPAuthenticationError as e:
                # Auth error - server misconfigured, skip it
                error_msg = str(e)
                last_error = error_msg
                last_error_type = "auth"
                
                logger.error(f"Authentication failed for {server_id}: {e}")
                self._health[server_id].record_failure()
                self._close_connection(server_id)
                
                # Try next server immediately
                continue
                
            except (smtplib.SMTPException, socket.error, OSError) as e:
                error_msg = str(e)
                error_type = classify_smtp_error(e)
                last_error = error_msg
                last_error_type = error_type
                
                logger.warning(f"SMTP error on {server_id}: {e} (type: {error_type})")
                self._health[server_id].record_failure()
                self._close_connection(server_id)
                
                if not is_retryable_error(e):
                    # Permanent error
                    return SendResult(
                        success=False,
                        server_id=server_id,
                        error=error_msg,
                        error_type=error_type,
                        attempts=attempts,
                        duration_ms=(time.time() - start_time) * 1000
                    )
                
                # Retryable error - backoff and try again
                delay = calculate_backoff(
                    attempts - 1,
                    base=self.retry_backoff_base,
                    max_delay=self.retry_max_delay
                )
                logger.info(f"Retrying in {delay:.1f}s (attempt {attempts}/{self.max_retries})")
                time.sleep(delay)
                
            except Exception as e:
                # Unexpected error
                error_msg = str(e)
                last_error = error_msg
                last_error_type = "unknown"
                
                logger.error(f"Unexpected error sending via {server_id}: {e}")
                self._close_connection(server_id)
                
                # Apply backoff
                delay = calculate_backoff(
                    attempts - 1,
                    base=self.retry_backoff_base,
                    max_delay=self.retry_max_delay
                )
                time.sleep(delay)
        
        # All retries exhausted
        return SendResult(
            success=False,
            server_id=",".join(servers_tried),
            error=last_error or "Max retries exceeded",
            error_type=last_error_type or "unknown",
            attempts=attempts,
            duration_ms=(time.time() - start_time) * 1000
        )
    
    def get_server_stats(self) -> Dict[str, dict]:
        """Get statistics for all servers."""
        stats = {}
        for server_id, health in self._health.items():
            config = self.configs[server_id]
            stats[server_id] = {
                'server': config.server,
                'domain': config.domain,
                'total_successes': health.total_successes,
                'total_failures': health.total_failures,
                'consecutive_failures': health.consecutive_failures,
                'is_available': health.is_available(),
                'sends': self._send_counts[server_id],
            }
        return stats
    
    def close_all(self):
        """Close all connections."""
        for server_id in list(self._connections.keys()):
            self._close_connection(server_id)
        logger.debug("All SMTP connections closed")


class DryRunSMTPPool:
    """
    Mock SMTP pool for dry-run mode.
    
    Simulates sending without actually connecting to servers.
    """
    
    def __init__(self, configs: List[SMTPConfig], **kwargs):
        self.configs = {f"server_{i}": cfg for i, cfg in enumerate(configs)}
        self._send_counts: Dict[str, int] = {sid: 0 for sid in self.configs}
        self._current_index = 0
    
    def send(
        self,
        message: MIMEMultipart,
        to_email: str,
        envelope_from: str
    ) -> SendResult:
        """Simulate sending an email."""
        # Round-robin through servers
        server_ids = list(self.configs.keys())
        server_id = server_ids[self._current_index % len(server_ids)]
        self._current_index += 1
        self._send_counts[server_id] += 1
        
        logger.info(f"[DRY-RUN] Would send to {to_email} via {server_id}")
        logger.debug(f"[DRY-RUN] From: {envelope_from}")
        logger.debug(f"[DRY-RUN] Subject: {message.get('Subject', 'N/A')}")
        
        return SendResult(
            success=True,
            server_id=server_id,
            attempts=1,
            duration_ms=0
        )
    
    def get_server_stats(self) -> Dict[str, dict]:
        """Get statistics for dry-run mode."""
        stats = {}
        for server_id, config in self.configs.items():
            stats[server_id] = {
                'server': config.server,
                'domain': config.domain,
                'total_successes': self._send_counts[server_id],
                'total_failures': 0,
                'consecutive_failures': 0,
                'is_available': True,
                'sends': self._send_counts[server_id],
            }
        return stats
    
    def close_all(self):
        """No-op for dry run."""
        pass


def create_smtp_pool(
    configs: List[SMTPConfig],
    dry_run: bool = False,
    **kwargs
) -> SMTPConnectionPool:
    """
    Factory function to create SMTP pool.
    
    Args:
        configs: List of SMTP configurations
        dry_run: If True, create mock pool
        **kwargs: Additional arguments for pool
        
    Returns:
        SMTP connection pool instance
    """
    if dry_run:
        return DryRunSMTPPool(configs, **kwargs)
    return SMTPConnectionPool(configs, **kwargs)
