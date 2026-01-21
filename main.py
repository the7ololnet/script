#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
import time
import uuid
from typing import Dict, List, Optional, Set

from ai import AIContentGenerator
from config import apply_cli_overrides, load_config_from_env
from mime_builder import create_mime_message
from smtp_client import SMTPPool, is_hard_bounce
from state import StateStore
from utils import (
    RateLimiter,
    extract_name_from_email,
    is_valid_email,
    load_simple_list,
    normalize_email,
    parse_spintax,
    random_sleep,
    setup_logging,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Production-ready SMTP campaign sender")
    parser.add_argument("--mode", choices=["WARMUP", "OFFER"], help="Campaign mode")
    parser.add_argument("--emails-file", help="Path to emails.txt")
    parser.add_argument("--min-sleep", type=int, help="Minimum sleep seconds between sends")
    parser.add_argument("--max-sleep", type=int, help="Maximum sleep seconds between sends")
    parser.add_argument("--rate-per-minute", type=int, help="Max sends per minute")
    parser.add_argument("--dry-run", action="store_true", help="Do not send emails")
    parser.add_argument(
        "--server-rotation",
        choices=["roundrobin", "weighted"],
        help="Server rotation strategy",
    )
    parser.add_argument("--max-per-server-per-hour", type=int, help="Per-server hourly cap")
    parser.add_argument("--allowlist-file", help="Allowlist domains for warmup")
    parser.add_argument("--db-path", help="SQLite DB path")
    parser.add_argument("--suppression-file", help="Suppression list file")
    parser.add_argument("--log-file", help="Log file path")
    parser.add_argument("--no-ai", action="store_true", help="Disable AI content generation")
    parser.add_argument("--ai-rate-per-minute", type=int, help="AI requests per minute")
    parser.add_argument("--max-attempts", type=int, help="Max SMTP attempts per send")
    return parser.parse_args()


def load_allowlist_domains(path: Optional[str]) -> Set[str]:
    entries = load_simple_list(path)
    domains = set()
    for entry in entries:
        if "@" in entry:
            domains.add(entry.split("@", 1)[1])
        else:
            domains.add(entry)
    return domains


def load_emails(path: str, allowlist_domains: Optional[Set[str]]) -> List[str]:
    emails: List[str] = []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                raw = line.strip()
                if not raw:
                    continue
                email = normalize_email(raw)
                if not is_valid_email(email):
                    continue
                if allowlist_domains and email.split("@")[1] not in allowlist_domains:
                    continue
                emails.append(email)
    except FileNotFoundError:
        return []
    deduped = sorted(set(emails))
    return deduped


def append_suppression(path: str, email: str) -> None:
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(f"{email}\n")
    except Exception:
        return


class Metrics:
    def __init__(self, total: int, server_ids: List[str]) -> None:
        self.total = total
        self.sent = 0
        self.failed = 0
        self.suppressed = 0
        self.retries = 0
        self.server_stats = {server_id: {"sent": 0, "failed": 0} for server_id in server_ids}
        self.start_time = time.time()

    def record_send(self, server_id: str, success: bool, attempts: int) -> None:
        if success:
            self.sent += 1
            self.server_stats[server_id]["sent"] += 1
        else:
            self.failed += 1
            self.server_stats[server_id]["failed"] += 1
        if attempts > 1:
            self.retries += attempts - 1

    def record_suppressed(self) -> None:
        self.suppressed += 1

    def summary(self) -> Dict[str, object]:
        return {
            "total": self.total,
            "sent": self.sent,
            "failed": self.failed,
            "suppressed": self.suppressed,
            "retries": self.retries,
            "per_server": self.server_stats,
            "duration_seconds": round(time.time() - self.start_time, 2),
        }


def main() -> int:
    args = parse_args()
    try:
        app_config = load_config_from_env()
    except Exception as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    overrides = {
        "mode": args.mode,
        "emails_file": args.emails_file,
        "min_sleep_seconds": args.min_sleep,
        "max_sleep_seconds": args.max_sleep,
        "rate_per_minute": args.rate_per_minute,
        "dry_run": args.dry_run,
        "server_rotation": args.server_rotation,
        "max_per_server_per_hour": args.max_per_server_per_hour,
        "allowlist_file": args.allowlist_file,
        "db_path": args.db_path,
        "suppression_file": args.suppression_file,
        "log_file": args.log_file,
        "use_ai": False if args.no_ai else None,
        "ai_rate_per_minute": args.ai_rate_per_minute,
        "max_attempts": args.max_attempts,
    }
    campaign = apply_cli_overrides(app_config.campaign, overrides)

    logger = setup_logging(campaign.log_file)
    logger.info("Campaign start", extra={"event": "campaign_start", "context": {"mode": campaign.mode}})

    if campaign.use_ai and not campaign.openai_api_key:
        logger.error(
            "OpenAI API key missing; set OPENAI_API_KEY or disable AI with --no-ai",
            extra={"event": "config_error", "context": {}},
        )
        return 1

    if campaign.mode == "WARMUP" and not campaign.allowlist_file:
        logger.error(
            "Warmup mode requires --allowlist-file",
            extra={"event": "config_error", "context": {}},
        )
        return 1

    allowlist_domains = load_allowlist_domains(campaign.allowlist_file)
    if campaign.mode == "WARMUP" and not allowlist_domains:
        logger.error(
            "Warmup allowlist is empty or missing",
            extra={"event": "config_error", "context": {}},
        )
        return 1
    suppression = load_simple_list(campaign.suppression_file)
    emails = load_emails(campaign.emails_file, allowlist_domains if campaign.mode == "WARMUP" else None)

    if not emails:
        logger.error("No valid emails to send", extra={"event": "no_emails", "context": {}})
        return 1

    state = StateStore(campaign.db_path)
    state.initialize()
    state.ensure_recipients(emails)

    for email in suppression:
        state.mark_suppressed(email, "suppression_list")

    send_queue = state.get_recipients_to_send(include_failed=True)
    if not send_queue:
        logger.info("No pending recipients", extra={"event": "no_pending", "context": {}})
        return 0

    smtp_servers = {server.id: server for server in app_config.smtp_servers}
    smtp_pool = SMTPPool(
        smtp_servers,
        max_per_hour=campaign.max_per_server_per_hour,
        circuit_breaker_threshold=campaign.circuit_breaker_threshold,
        cooldown_seconds=campaign.circuit_breaker_cooldown_seconds,
        rotation=campaign.server_rotation,
    )

    metrics = Metrics(total=len(send_queue), server_ids=list(smtp_servers.keys()))
    send_rate = RateLimiter(campaign.rate_per_minute)

    ai_generator = AIContentGenerator(
        api_key=campaign.openai_api_key,
        model=campaign.openai_model,
        rate_per_minute=campaign.ai_rate_per_minute,
        timeout_seconds=campaign.ai_timeout_seconds,
        use_ai=campaign.use_ai,
        cache_enabled=campaign.cache_ai_content,
        base_url=campaign.openai_base_url,
        max_attempts=campaign.max_attempts,
    )

    for index, email in enumerate(send_queue, start=1):
        if email in suppression:
            state.mark_suppressed(email, "suppression_list")
            metrics.record_suppressed()
            continue

        recipient_name = extract_name_from_email(email)
        server_state = smtp_pool.next_server()
        if server_state is None:
            wait_for = smtp_pool.seconds_until_available()
            if wait_for > 0:
                logger.info(
                    "All servers throttled, sleeping",
                    extra={"event": "server_throttle", "context": {"sleep_seconds": wait_for}},
                )
                time.sleep(wait_for)
            server_state = smtp_pool.next_server()
            if server_state is None:
                logger.error("No SMTP server available", extra={"event": "no_server", "context": {}})
                break

        sender_name = parse_spintax(server_state.config.sender_name)
        content = ai_generator.get_content(
            email=email,
            recipient_name=recipient_name,
            mode=campaign.mode,
            company_name=campaign.company_name,
            company_address=campaign.company_address,
            unsub_url=server_state.config.unsub_url,
            state_store=state,
        )

        message = create_mime_message(
            to_email=email,
            to_name=recipient_name,
            subject=content["subject"],
            body_text=content["body_text"],
            body_html=content["body_html"],
            domain=server_state.config.domain,
            from_name=sender_name,
            from_email=server_state.config.sender_email,
            reply_to=server_state.config.reply_to,
            list_id=server_state.config.list_id,
            unsub_url=server_state.config.unsub_url,
            mode=campaign.mode,
        )

        if campaign.dry_run:
            logger.info(
                "Dry run send",
                extra={
                    "event": "dry_run",
                    "context": {
                        "email": email,
                        "server_id": server_state.config.id,
                        "subject": content["subject"],
                    },
                },
            )
            continue

        send_rate.wait()
        result = smtp_pool.send_message(
            server=server_state,
            message=message,
            to_email=email,
            envelope_from=server_state.config.sender_email,
            max_attempts=campaign.max_attempts,
            retry_base_seconds=campaign.retry_base_seconds,
            retry_max_seconds=campaign.retry_max_seconds,
        )

        message_id = message.get("Message-ID", str(uuid.uuid4()))
        if result.success:
            state.mark_sent(email)
            state.record_send(
                message_uuid=message_id,
                email=email,
                smtp_server_id=server_state.config.id,
                mode=campaign.mode,
                result="sent",
                error_category=None,
                error_message=None,
            )
        else:
            state.record_send(
                message_uuid=message_id,
                email=email,
                smtp_server_id=server_state.config.id,
                mode=campaign.mode,
                result="failed",
                error_category=result.error_category,
                error_message=result.error_message,
            )
            if is_hard_bounce(result.error_category, result.smtp_code, result.error_message or ""):
                state.mark_suppressed(email, "hard_bounce")
                metrics.record_suppressed()
                append_suppression(campaign.suppression_file, email)
            else:
                state.mark_failed(email, result.error_category or "unknown", result.error_message or "unknown error")

        metrics.record_send(server_state.config.id, result.success, result.attempts)

        logger.info(
            "Send result",
            extra={
                "event": "send_result",
                "context": {
                    "email": email,
                    "server_id": server_state.config.id,
                    "success": result.success,
                    "attempts": result.attempts,
                    "error_category": result.error_category,
                    "error_message": result.error_message,
                },
            },
        )

        if index < len(send_queue):
            random_sleep(campaign.min_sleep_seconds, campaign.max_sleep_seconds)

    logger.info(
        "Campaign summary",
        extra={"event": "campaign_summary", "context": metrics.summary()},
    )
    smtp_pool.close_all()
    state.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
