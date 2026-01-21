from __future__ import annotations

import random
import smtplib
import socket
import ssl
import time
from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, Optional, Tuple

from config import SMTPConfig


@dataclass
class SendResult:
    success: bool
    attempts: int
    error_category: Optional[str] = None
    error_message: Optional[str] = None
    smtp_code: Optional[int] = None


def classify_smtp_error(exc: Exception) -> Tuple[str, bool, Optional[int], str]:
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return "auth", False, exc.smtp_code, str(exc.smtp_error)

    if isinstance(exc, smtplib.SMTPResponseException):
        smtp_code = exc.smtp_code
        message = exc.smtp_error.decode(errors="ignore") if isinstance(exc.smtp_error, bytes) else str(exc.smtp_error)
        if 400 <= smtp_code < 500:
            return "transient", True, smtp_code, message
        if 500 <= smtp_code < 600:
            return "permanent", False, smtp_code, message
        return "unknown", False, smtp_code, message

    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        for _recipient, (code, message) in exc.recipients.items():
            msg = message.decode(errors="ignore") if isinstance(message, bytes) else str(message)
            if 400 <= code < 500:
                return "transient", True, code, msg
            if 500 <= code < 600:
                return "permanent", False, code, msg
            return "unknown", False, code, msg
        return "unknown", False, None, str(exc)

    if isinstance(exc, smtplib.SMTPServerDisconnected):
        return "connection", True, None, str(exc)

    if isinstance(exc, (socket.timeout, TimeoutError)):
        return "timeout", True, None, str(exc)

    if isinstance(exc, OSError):
        return "connection", True, None, str(exc)

    return "unknown", False, None, str(exc)


def is_hard_bounce(error_category: Optional[str], smtp_code: Optional[int], message: str) -> bool:
    if error_category != "permanent":
        return False
    if smtp_code in {550, 551, 552, 553, 554}:
        return True
    lowered = message.lower()
    return any(token in lowered for token in ("user unknown", "no such user", "mailbox unavailable", "invalid recipient"))


class SMTPServerState:
    def __init__(
        self,
        config: SMTPConfig,
        max_per_hour: int,
        circuit_breaker_threshold: int,
        cooldown_seconds: int,
    ) -> None:
        self.config = config
        self.max_per_hour = max_per_hour
        self.circuit_breaker_threshold = circuit_breaker_threshold
        self.cooldown_seconds = cooldown_seconds
        self.connection: Optional[smtplib.SMTP] = None
        self.failure_count = 0
        self.cooldown_until = 0.0
        self.sent_timestamps: Deque[float] = deque()
        self.sent_success = 0
        self.sent_failure = 0

    def can_send(self, now: float) -> bool:
        self._prune_timestamps(now)
        if now < self.cooldown_until:
            return False
        if self.max_per_hour and len(self.sent_timestamps) >= self.max_per_hour:
            return False
        return True

    def seconds_until_available(self, now: float) -> float:
        if now < self.cooldown_until:
            return self.cooldown_until - now
        if self.max_per_hour and len(self.sent_timestamps) >= self.max_per_hour:
            oldest = self.sent_timestamps[0]
            return max(0.0, 3600 - (now - oldest))
        return 0.0

    def record_success(self, now: float) -> None:
        self._prune_timestamps(now)
        self.sent_timestamps.append(now)
        self.failure_count = 0
        self.sent_success += 1

    def record_failure(self, now: float) -> None:
        self.failure_count += 1
        self.sent_failure += 1
        if self.failure_count >= self.circuit_breaker_threshold:
            self.cooldown_until = now + self.cooldown_seconds

    def close(self) -> None:
        if self.connection:
            try:
                self.connection.quit()
            except Exception:
                try:
                    self.connection.close()
                except Exception:
                    pass
            self.connection = None

    def connect(self) -> smtplib.SMTP:
        if self.connection:
            return self.connection
        timeout = self.config.timeout_seconds
        context = ssl.create_default_context()
        if self.config.use_ssl:
            server = smtplib.SMTP_SSL(self.config.server, self.config.port, timeout=timeout, context=context)
        else:
            server = smtplib.SMTP(self.config.server, self.config.port, timeout=timeout)
            server.ehlo()
            if self.config.use_starttls:
                server.starttls(context=context)
                server.ehlo()
        server.login(self.config.user, self.config.pass_)
        self.connection = server
        return server

    def _prune_timestamps(self, now: float) -> None:
        while self.sent_timestamps and (now - self.sent_timestamps[0]) > 3600:
            self.sent_timestamps.popleft()


class SMTPPool:
    def __init__(
        self,
        servers: Dict[str, SMTPConfig],
        max_per_hour: int,
        circuit_breaker_threshold: int,
        cooldown_seconds: int,
        rotation: str = "roundrobin",
    ) -> None:
        self.rotation = rotation
        self.server_states = [
            SMTPServerState(config, max_per_hour, circuit_breaker_threshold, cooldown_seconds)
            for config in servers.values()
        ]
        self.round_robin_index = 0

    def available_servers(self) -> list[SMTPServerState]:
        now = time.time()
        return [server for server in self.server_states if server.can_send(now)]

    def next_server(self) -> Optional[SMTPServerState]:
        available = self.available_servers()
        if not available:
            return None
        if self.rotation == "weighted":
            weights = [server.config.weight for server in available]
            return random.choices(available, weights=weights, k=1)[0]
        server = available[self.round_robin_index % len(available)]
        self.round_robin_index += 1
        return server

    def seconds_until_available(self) -> float:
        now = time.time()
        waits = [server.seconds_until_available(now) for server in self.server_states]
        waits = [wait for wait in waits if wait > 0]
        return min(waits) if waits else 0.0

    def send_message(
        self,
        server: SMTPServerState,
        message,
        to_email: str,
        envelope_from: str,
        max_attempts: int,
        retry_base_seconds: float,
        retry_max_seconds: float,
    ) -> SendResult:
        attempts = 0
        while attempts < max_attempts:
            attempts += 1
            now = time.time()
            try:
                connection = server.connect()
                connection.send_message(message, from_addr=envelope_from, to_addrs=[to_email])
                server.record_success(now)
                return SendResult(success=True, attempts=attempts)
            except Exception as exc:
                category, retryable, smtp_code, message = classify_smtp_error(exc)
                server.record_failure(now)
                server.close()
                if not retryable or attempts >= max_attempts:
                    return SendResult(
                        success=False,
                        attempts=attempts,
                        error_category=category,
                        error_message=message,
                        smtp_code=smtp_code,
                    )
                sleep_for = min(retry_max_seconds, retry_base_seconds * (2 ** (attempts - 1)))
                sleep_for += random.uniform(0, 1.0)
                time.sleep(sleep_for)

        return SendResult(success=False, attempts=attempts, error_category="unknown", error_message="max attempts")

    def close_all(self) -> None:
        for server in self.server_states:
            server.close()
