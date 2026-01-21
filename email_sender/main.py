#!/usr/bin/env python3
"""
Production-grade Email Campaign Sender
======================================

A compliant, secure, and reliable bulk email sending platform.

Usage:
    python -m email_sender.main --mode WARMUP --emails-file emails.txt
    python -m email_sender.main --mode OFFER --emails-file emails.txt --dry-run
    
See --help for full options.
"""

import argparse
import logging
import os
import random
import signal
import sys
import time
from datetime import datetime, timezone
from typing import Optional

# Add parent directory to path for imports when running as script
if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from email_sender.config import (
    AppConfig, CampaignConfig, AIConfig, SMTPConfig,
    load_config_from_env, validate_startup, ConfigurationError
)
from email_sender.state import StateStore, RecipientStatus
from email_sender.utils import (
    load_emails_from_file, load_suppression_list, load_allowlist_domains,
    is_email_in_allowlist, extract_name_from_email
)
from email_sender.ai import create_content_generator
from email_sender.mime_builder import create_mime_message, get_envelope_from
from email_sender.smtp_client import create_smtp_pool, SendResult
from email_sender.logging_config import setup_logging, log_send_attempt


logger = logging.getLogger(__name__)

# Global flag for graceful shutdown
shutdown_requested = False


def signal_handler(signum, frame):
    """Handle shutdown signals gracefully."""
    global shutdown_requested
    logger.info("Shutdown requested, finishing current operation...")
    shutdown_requested = True


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Production-grade email campaign sender",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run warmup campaign with dry-run
  python -m email_sender.main --mode WARMUP --emails-file emails.txt --dry-run
  
  # Run offer campaign with custom timing
  python -m email_sender.main --mode OFFER --emails-file emails.txt \\
    --min-sleep 5 --max-sleep 20 --rate-per-minute 15
  
  # Resume interrupted campaign
  python -m email_sender.main --mode WARMUP --emails-file emails.txt \\
    --db-path campaign_state.db

Environment Variables:
  SMTP1_SERVER, SMTP1_PORT, SMTP1_USER, SMTP1_PASS, etc.
  OPENAI_API_KEY
  See .env.example for full list.
        """
    )
    
    # Required arguments
    parser.add_argument(
        '--mode', '-m',
        choices=['WARMUP', 'OFFER'],
        default='WARMUP',
        help='Campaign mode: WARMUP (internal testing) or OFFER (marketing)'
    )
    
    parser.add_argument(
        '--emails-file', '-e',
        default='emails.txt',
        help='Path to file containing recipient emails (one per line)'
    )
    
    # Timing configuration
    parser.add_argument(
        '--min-sleep',
        type=int,
        default=10,
        help='Minimum seconds between sends (default: 10)'
    )
    
    parser.add_argument(
        '--max-sleep',
        type=int,
        default=40,
        help='Maximum seconds between sends (default: 40)'
    )
    
    parser.add_argument(
        '--rate-per-minute',
        type=int,
        default=10,
        help='Maximum emails per minute (default: 10)'
    )
    
    # Server configuration
    parser.add_argument(
        '--server-rotation',
        choices=['roundrobin', 'weighted'],
        default='roundrobin',
        help='Server rotation strategy (default: roundrobin)'
    )
    
    parser.add_argument(
        '--max-per-server-per-hour',
        type=int,
        default=100,
        help='Maximum emails per server per hour (default: 100)'
    )
    
    # Files
    parser.add_argument(
        '--suppression-file',
        help='Path to suppression list file'
    )
    
    parser.add_argument(
        '--allowlist-file',
        help='Path to domain allowlist file (for WARMUP mode)'
    )
    
    parser.add_argument(
        '--db-path',
        default='campaign_state.db',
        help='Path to SQLite state database (default: campaign_state.db)'
    )
    
    parser.add_argument(
        '--log-file',
        default='campaign.log',
        help='Path to log file (default: campaign.log)'
    )
    
    # AI configuration
    parser.add_argument(
        '--no-ai',
        action='store_true',
        help='Disable AI content generation, use templates instead'
    )
    
    # Operational modes
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Simulate sending without actually connecting to SMTP servers'
    )
    
    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Enable verbose output'
    )
    
    parser.add_argument(
        '--max-retries',
        type=int,
        default=3,
        help='Maximum retry attempts per email (default: 3)'
    )
    
    # Compliance
    parser.add_argument(
        '--company-name',
        default='[Company Name]',
        help='Company name for email footer (OFFER mode)'
    )
    
    parser.add_argument(
        '--physical-address',
        default='[Physical Address]',
        help='Physical address for email footer (OFFER mode)'
    )
    
    return parser.parse_args()


def build_config(args: argparse.Namespace) -> AppConfig:
    """
    Build application configuration from arguments and environment.
    
    Args:
        args: Parsed command-line arguments
        
    Returns:
        Complete application configuration
    """
    # Set AI_ENABLED environment variable before loading config
    # This ensures validation doesn't require API key when --no-ai is used
    if args.no_ai:
        os.environ['AI_ENABLED'] = 'false'
    
    # Load base config from environment
    config = load_config_from_env()
    
    # Override with command-line arguments
    config.campaign.mode = args.mode
    config.campaign.emails_file = args.emails_file
    config.campaign.min_sleep = args.min_sleep
    config.campaign.max_sleep = args.max_sleep
    config.campaign.rate_per_minute = args.rate_per_minute
    config.campaign.server_rotation = args.server_rotation
    config.campaign.max_per_server_per_hour = args.max_per_server_per_hour
    config.campaign.db_path = args.db_path
    config.campaign.log_file = args.log_file
    config.campaign.dry_run = args.dry_run
    config.campaign.max_retries = args.max_retries
    
    if args.suppression_file:
        config.campaign.suppression_file = args.suppression_file
    if args.allowlist_file:
        config.campaign.allowlist_file = args.allowlist_file
    
    return config


def print_summary(
    start_time: datetime,
    stats: dict,
    server_stats: dict,
    error_summary: list
):
    """Print campaign summary."""
    duration = datetime.now(timezone.utc) - start_time
    
    print("\n" + "=" * 60)
    print("CAMPAIGN SUMMARY")
    print("=" * 60)
    print(f"Duration: {duration}")
    print(f"Total recipients: {stats.get('pending', 0) + stats.get('sent', 0) + stats.get('failed', 0)}")
    print(f"Sent: {stats.get('sent', 0)}")
    print(f"Failed: {stats.get('failed', 0)}")
    print(f"Suppressed: {stats.get('suppressed', 0)}")
    print(f"Pending: {stats.get('pending', 0)}")
    
    if stats.get('sent', 0) + stats.get('failed', 0) > 0:
        success_rate = stats.get('sent', 0) / (stats.get('sent', 0) + stats.get('failed', 0)) * 100
        print(f"Success rate: {success_rate:.1f}%")
    
    print("\nPer-Server Statistics:")
    for server_id, server_stat in server_stats.items():
        print(f"  {server_id} ({server_stat['domain']}):")
        print(f"    Sends: {server_stat['sends']}")
        print(f"    Successes: {server_stat['total_successes']}")
        print(f"    Failures: {server_stat['total_failures']}")
        print(f"    Available: {server_stat['is_available']}")
    
    if error_summary:
        print("\nError Summary:")
        for error, count in error_summary[:10]:
            print(f"  {count}x: {error[:60]}...")
    
    print("=" * 60)


def run_campaign(config: AppConfig, args: argparse.Namespace) -> int:
    """
    Run the email campaign.
    
    Args:
        config: Application configuration
        args: Command-line arguments
        
    Returns:
        Exit code (0 for success, 1 for failure)
    """
    global shutdown_requested
    
    start_time = datetime.now(timezone.utc)
    
    # Initialize state store
    logger.info(f"Initializing state store: {config.campaign.db_path}")
    state_store = StateStore(config.campaign.db_path)
    
    # Load emails
    logger.info(f"Loading emails from: {config.campaign.emails_file}")
    try:
        emails = load_emails_from_file(config.campaign.emails_file)
    except FileNotFoundError as e:
        logger.error(str(e))
        return 1
    
    logger.info(f"Loaded {len(emails)} unique email addresses")
    
    # Load suppression list
    suppression_set = load_suppression_list(config.campaign.suppression_file)
    if config.campaign.suppression_file:
        state_store.load_suppression_file(config.campaign.suppression_file)
    logger.info(f"Loaded {len(suppression_set)} suppressed addresses")
    
    # Load allowlist for WARMUP mode
    allowlist = None
    if config.campaign.mode == "WARMUP" and config.campaign.allowlist_file:
        allowlist = load_allowlist_domains(config.campaign.allowlist_file)
        if allowlist:
            logger.info(f"Loaded {len(allowlist)} allowed domains for WARMUP")
    
    # Add recipients to state store
    added = state_store.add_recipients(emails)
    logger.info(f"Added {added} new recipients to state store")
    
    # Initialize content generator
    logger.info(f"Initializing content generator (AI enabled: {config.ai.enabled})")
    content_gen = create_content_generator(config.ai, state_store)
    
    # Initialize SMTP pool
    logger.info(f"Initializing SMTP pool (dry-run: {config.campaign.dry_run})")
    smtp_pool = create_smtp_pool(
        config.smtp_configs,
        dry_run=config.campaign.dry_run,
        rotation=config.campaign.server_rotation,
        max_retries=config.campaign.max_retries
    )
    
    # Get pending recipients
    pending = state_store.get_pending_recipients()
    logger.info(f"Processing {len(pending)} pending recipients")
    
    # Statistics
    processed = 0
    sent = 0
    failed = 0
    skipped = 0
    
    try:
        for recipient in pending:
            if shutdown_requested:
                logger.info("Shutdown requested, stopping...")
                break
            
            email = recipient.email
            processed += 1
            
            # Check suppression
            if state_store.is_suppressed(email) or email in suppression_set:
                logger.info(f"[{processed}/{len(pending)}] Skipping suppressed: {email}")
                state_store.update_recipient_status(email, RecipientStatus.SUPPRESSED)
                skipped += 1
                continue
            
            # Check allowlist (WARMUP mode)
            if config.campaign.mode == "WARMUP" and allowlist:
                if not is_email_in_allowlist(email, allowlist):
                    logger.info(f"[{processed}/{len(pending)}] Skipping (not in allowlist): {email}")
                    state_store.update_recipient_status(email, RecipientStatus.SKIPPED, "not in allowlist")
                    skipped += 1
                    continue
            
            logger.info(f"[{processed}/{len(pending)}] Processing: {email}")
            
            # Extract name
            name = extract_name_from_email(email)
            
            # Generate content
            try:
                content = content_gen.generate_content(
                    email, name, config.campaign.mode
                )
                logger.debug(f"Content generated (cached: {content.from_cache}): {content.subject}")
            except Exception as e:
                logger.error(f"Content generation failed for {email}: {e}")
                state_store.update_recipient_status(email, RecipientStatus.FAILED, str(e))
                failed += 1
                continue
            
            # Select SMTP config (round-robin for message creation)
            smtp_config = config.smtp_configs[processed % len(config.smtp_configs)]
            
            # Create MIME message
            message = create_mime_message(
                to_email=email,
                to_name=name,
                subject=content.subject,
                body_text=content.body_text,
                body_html=content.body_html,
                smtp_config=smtp_config,
                mode=config.campaign.mode,
                company_name=args.company_name,
                physical_address=args.physical_address
            )
            
            # Send email
            envelope_from = get_envelope_from(smtp_config)
            result: SendResult = smtp_pool.send(message, email, envelope_from)
            
            # Log result
            log_send_attempt(
                logger, email, result.server_id,
                result.success, result.duration_ms,
                result.error, result.error_type
            )
            
            # Update state
            if result.success:
                state_store.update_recipient_status(email, RecipientStatus.SENT)
                state_store.record_send(
                    email, result.server_id, config.campaign.mode,
                    "success", subject=content.subject
                )
                sent += 1
            else:
                state_store.update_recipient_status(
                    email, RecipientStatus.FAILED, result.error
                )
                state_store.record_send(
                    email, result.server_id, config.campaign.mode,
                    "failed", error=result.error, subject=content.subject
                )
                failed += 1
                
                # Auto-suppress on permanent failure
                if result.error_type == "permanent":
                    state_store.add_to_suppression(email, "hard_bounce")
                    logger.info(f"Auto-suppressed {email} due to permanent failure")
            
            # Sleep between sends (unless last email or dry-run)
            if processed < len(pending) and not config.campaign.dry_run:
                sleep_time = random.randint(
                    config.campaign.min_sleep,
                    config.campaign.max_sleep
                )
                logger.debug(f"Sleeping {sleep_time}s before next send")
                
                # Interruptible sleep
                for _ in range(sleep_time):
                    if shutdown_requested:
                        break
                    time.sleep(1)
    
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    
    finally:
        # Cleanup
        smtp_pool.close_all()
        
        # Print summary
        stats = state_store.get_statistics()
        server_stats = smtp_pool.get_server_stats()
        error_summary = state_store.get_error_summary()
        
        print_summary(start_time, stats, server_stats, error_summary)
    
    return 0 if failed == 0 else 1


def main():
    """Main entry point."""
    # Parse arguments
    args = parse_args()
    
    # Set up logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    setup_logging(
        log_file=args.log_file,
        console_level=log_level,
        json_logs=True
    )
    
    # Register signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    logger.info("=" * 60)
    logger.info("Email Campaign Sender Starting")
    logger.info(f"Mode: {args.mode}")
    logger.info(f"Dry-run: {args.dry_run}")
    logger.info("=" * 60)
    
    # Build configuration
    try:
        config = build_config(args)
    except ConfigurationError as e:
        logger.error(f"Configuration error: {e}")
        sys.exit(1)
    
    # Validate startup
    try:
        warnings = validate_startup(config)
        for warning in warnings:
            logger.warning(warning)
    except ConfigurationError as e:
        logger.error(f"Validation error: {e}")
        sys.exit(1)
    
    # Run campaign
    exit_code = run_campaign(config, args)
    
    logger.info("Campaign finished")
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
