from __future__ import annotations

import json
import logging
import random
import re
import time
from datetime import datetime, timezone
from typing import Optional, Set
from urllib.parse import urlparse


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalize_email(email: str) -> str:
    return email.strip().lower()


def is_valid_email(email: str) -> bool:
    return bool(EMAIL_RE.match(email))


def extract_name_from_email(email: str) -> str:
    local_part = email.split("@")[0]
    match = re.match(r"^([a-zA-Z]+)[._-]", local_part)
    if match:
        return match.group(1).capitalize()
    match = re.match(r"^([a-zA-Z]+)", local_part)
    if match:
        return match.group(1).capitalize()
    return local_part.split(".")[0].split("_")[0].capitalize()


def parse_spintax(text: str, rng: Optional[random.Random] = None) -> str:
    if "{" not in text:
        return text
    rng = rng or random
    pattern = re.compile(r"\{([^{}]+)\}")

    result = text
    while True:
        match = pattern.search(result)
        if not match:
            break
        options = [option.strip() for option in match.group(1).split("|")]
        result = result[: match.start()] + rng.choice(options) + result[match.end() :]
    return result.strip()


def is_valid_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def load_simple_list(path: Optional[str]) -> Set[str]:
    if not path:
        return set()
    items: Set[str] = set()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                value = line.strip()
                if value:
                    items.add(value.lower())
    except FileNotFoundError:
        return set()
    return items


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "message": record.getMessage(),
        }
        if hasattr(record, "event"):
            payload["event"] = record.event
        if hasattr(record, "context") and isinstance(record.context, dict):
            payload.update(record.context)
        return json.dumps(payload, ensure_ascii=True)


def setup_logging(log_file: str) -> logging.Logger:
    logger = logging.getLogger("campaign")
    logger.setLevel(logging.INFO)
    logger.handlers = []
    formatter = JsonFormatter()

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    logger.propagate = False
    return logger


class RateLimiter:
    def __init__(self, rate_per_minute: int) -> None:
        self.rate_per_minute = max(1, rate_per_minute)
        self.calls: list[float] = []

    def wait(self) -> None:
        now = time.monotonic()
        calls = list(self.calls)
        calls = [t for t in calls if now - t < 60]
        if len(calls) >= self.rate_per_minute:
            sleep_for = 60 - (now - calls[0])
            if sleep_for > 0:
                time.sleep(sleep_for)
            now = time.monotonic()
            calls = [t for t in calls if now - t < 60]
        calls.append(now)
        self.calls = calls


def random_sleep(min_seconds: int, max_seconds: int) -> None:
    if max_seconds <= 0:
        return
    sleep_for = random.randint(min_seconds, max_seconds)
    time.sleep(sleep_for)
