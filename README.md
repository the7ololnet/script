# Production-Grade Email Campaign Sender

A secure, reliable, and compliant bulk email sending platform built for Python 3.10+.

## Features

- **Secure Configuration**: No hardcoded secrets; loads from environment variables or `.env` file
- **Dual SMTP Rotation**: Support for multiple SMTP servers with round-robin or weighted rotation
- **Connection Pooling**: Reuses SMTP connections for efficiency
- **Circuit Breaker**: Automatically cools down failing servers
- **Retry with Backoff**: Exponential backoff with jitter for transient failures
- **Idempotent Sending**: SQLite state store tracks sent emails; resume interrupted campaigns
- **Bounce Suppression**: Auto-suppresses hard bounces; supports suppression file
- **AI Content Generation**: Optional OpenAI integration for unique per-recipient content
- **Compliance Headers**: RFC 8058 compliant List-Unsubscribe headers for OFFER mode
- **Structured Logging**: JSON logs for production; human-readable console output
- **Dry-Run Mode**: Test without actually sending
- **WARMUP Mode**: Restricted mode for IP/domain warmup with domain allowlists

## Installation

### Requirements

- Python 3.10+
- CentOS 9 / RHEL 9 / Ubuntu 22.04+ (or compatible)

### Setup

```bash
# Clone or copy the project
cd email_sender

# Create virtual environment (recommended)
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Copy environment template
cp .env.example .env

# Edit .env with your configuration
nano .env
```

### Dependencies

Minimal dependencies (most are stdlib):

```
python-dotenv>=1.0.0  # Optional: for .env file loading
openai>=1.0.0         # Optional: for AI content generation
```

## Configuration

### Environment Variables

All configuration is done via environment variables. See `.env.example` for full documentation.

**Required for each SMTP server:**
- `SMTP1_SERVER` - SMTP server hostname/IP
- `SMTP1_PORT` - SMTP port (587 for STARTTLS, 465 for SSL)
- `SMTP1_USER` - SMTP username
- `SMTP1_PASS` - SMTP password
- `SMTP1_SENDER_EMAIL` - Sender email address
- `SMTP1_DOMAIN` - Sending domain
- `SMTP1_SENDER_NAME` - Sender display name (supports spintax)
- `SMTP1_REPLY_TO` - Reply-to address

**For OFFER mode (marketing emails):**
- `SMTP1_UNSUB_URL` - Unsubscribe URL (required)
- `SMTP1_LIST_ID` - List identifier

**For AI content generation:**
- `OPENAI_API_KEY` - Your OpenAI API key

## Usage

### Basic Usage

```bash
# Run warmup campaign (internal testing mode)
python -m email_sender.main --mode WARMUP --emails-file emails.txt

# Run offer campaign (marketing mode)
python -m email_sender.main --mode OFFER --emails-file emails.txt

# Dry-run (no actual sending)
python -m email_sender.main --mode WARMUP --emails-file emails.txt --dry-run
```

### CLI Options

```
--mode {WARMUP,OFFER}     Campaign mode (default: WARMUP)
--emails-file FILE        Path to recipient emails file
--min-sleep SECONDS       Minimum delay between sends (default: 10)
--max-sleep SECONDS       Maximum delay between sends (default: 40)
--rate-per-minute N       Maximum emails per minute (default: 10)
--server-rotation TYPE    roundrobin or weighted (default: roundrobin)
--max-per-server-per-hour N  Hourly limit per server (default: 100)
--suppression-file FILE   Path to suppression list
--allowlist-file FILE     Path to domain allowlist (WARMUP mode)
--db-path PATH           SQLite database path (default: campaign_state.db)
--log-file PATH          Log file path (default: campaign.log)
--no-ai                  Disable AI content generation
--dry-run                Simulate sending without SMTP connection
--verbose                Enable debug logging
--company-name NAME      Company name for footer (OFFER mode)
--physical-address ADDR  Physical address for footer (OFFER mode)
```

### Input Files

**emails.txt** - One email per line:
```
john@example.com
jane@company.org
# Comments are ignored
```

**suppression.txt** - Emails to never send to:
```
bounced@example.com
unsubscribed@example.com
```

**allowlist.txt** - Allowed domains for WARMUP mode:
```
gmail.com
outlook.com
yourdomain.com
```

## Campaign Modes

### WARMUP Mode

Use for IP/domain warmup with seeded mailboxes:

- Generates neutral, non-promotional content
- No marketing headers (List-Unsubscribe, etc.)
- Optional domain allowlist enforcement
- Lower sending rates recommended

```bash
python -m email_sender.main \
    --mode WARMUP \
    --emails-file warmup_seeds.txt \
    --allowlist-file allowed_domains.txt \
    --min-sleep 30 \
    --max-sleep 60
```

### OFFER Mode

Use for legitimate marketing campaigns:

- Requires unsubscribe URL configuration
- Includes RFC 8058 List-Unsubscribe headers
- Adds compliance footer with company info
- Full content generation

```bash
python -m email_sender.main \
    --mode OFFER \
    --emails-file subscribers.txt \
    --company-name "Your Company Inc." \
    --physical-address "123 Main St, City, State 12345"
```

## Resuming Interrupted Campaigns

The SQLite state store tracks all sent emails. If a campaign is interrupted:

```bash
# Simply run the same command - it will skip already-sent emails
python -m email_sender.main --mode WARMUP --emails-file emails.txt
```

To start fresh, delete the database:
```bash
rm campaign_state.db
```

## Running Tests

```bash
# Run all tests
python -m pytest tests/ -v

# Run specific test file
python -m pytest tests/test_utils.py -v

# Run with unittest (no pytest required)
python -m unittest discover tests/
```

## Project Structure

```
email_sender/
├── __init__.py          # Package initialization
├── main.py              # CLI entry point
├── config.py            # Configuration models and validation
├── utils.py             # Utility functions (spintax, name extraction)
├── state.py             # SQLite state management
├── ai.py                # AI content generation
├── mime_builder.py      # MIME message creation
├── smtp_client.py       # SMTP connection pool and sending
└── logging_config.py    # Structured logging setup

tests/
├── test_utils.py        # Utility function tests
├── test_config.py       # Configuration validation tests
├── test_mime_builder.py # MIME message tests
└── test_state.py        # State store tests
```

## What Was Fixed (vs Original Script)

### Security Issues Fixed
1. **Removed hardcoded API keys** - All secrets now loaded from environment
2. **Removed hardcoded SMTP passwords** - Now via `SMTP1_PASS`, `SMTP2_PASS`
3. **Added `.env.example`** - Template without real secrets

### Correctness Bugs Fixed
1. **Unified config keys** - Consistent naming: `server`, `port`, `user`, `password`, `sender_email`, etc.
2. **Fixed `create_mime_message` signature** - All parameters now explicit; no globals
3. **Removed `get_smtp_config()` confusion** - Config now properly structured
4. **Fixed undefined variables** - All variables passed explicitly to functions

### Removed Problematic Patterns
1. **Removed User-Agent rotation** - This is a deceptive practice; removed entirely
2. **Removed X-Mailer spoofing** - Removed fake email client headers
3. **Removed tracking pixel references** - No tracking implementation included

### Added Production Features
1. **Connection pooling** - Reuses SMTP connections
2. **Retry with exponential backoff** - Handles transient failures
3. **Circuit breaker** - Cools down failing servers
4. **Idempotent sending** - SQLite state prevents duplicates
5. **Structured logging** - JSON logs for production
6. **Proper TLS handling** - STARTTLS and implicit TLS support
7. **CLI interface** - Full argparse CLI with all options

## Compliance Notes

This tool is designed for **legitimate email marketing** only:

1. **Obtain proper consent** - Only email people who have opted in
2. **Configure SPF/DKIM/DMARC** - Set up proper authentication on your domains
3. **Include unsubscribe** - OFFER mode includes required unsubscribe headers
4. **Honor unsubscribes** - Implement the unsubscribe endpoint and process requests
5. **Include physical address** - Required by CAN-SPAM for commercial email
6. **Respect suppression lists** - Maintain and honor bounce/unsubscribe lists

## Unsubscribe Endpoint Contract

For OFFER mode, you must implement an unsubscribe handler at your `UNSUB_URL`:

```python
# Example Flask endpoint
@app.route('/unsubscribe', methods=['GET', 'POST'])
def unsubscribe():
    email = request.args.get('email') or request.form.get('email')
    if email:
        # Add to suppression list
        add_to_suppression(email)
        return "Successfully unsubscribed", 200
    return "Email required", 400
```

The endpoint should:
- Accept both GET (link click) and POST (one-click)
- Add email to suppression list
- Return success page

## License

This project is provided as-is for legitimate email marketing purposes.
