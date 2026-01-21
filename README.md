# Production SMTP Campaign Sender

This project is a production-grade SMTP bulk sender focused on correctness,
security, compliance, and observability. It avoids deceptive headers and
does not implement spam-filter bypass techniques.

## Requirements

- Python 3.10+
- SMTP credentials supplied via environment or `.env`
- Dependencies (minimal):
  - `pydantic`
  - `python-dotenv` (optional)
  - `openai` (optional, for AI content generation)

## Setup

1. Copy `.env.example` to `.env` and fill in values.
2. Create `emails.txt` with one address per line.
3. Optionally create `suppression.txt` with one address per line to exclude.
4. For WARMUP mode, create an allowlist file with allowed domains.

## Run

Warmup mode (requires allowlist):
```bash
python main.py --mode WARMUP --allowlist-file allowlist.txt
```

Offer mode:
```bash
python main.py --mode OFFER
```

Dry run:
```bash
python main.py --mode OFFER --dry-run
```

## Unsubscribe Handling (One-Click)

See `unsubscribe_handler.py` for a minimal handler contract. Your endpoint must
accept a POST from the `List-Unsubscribe` URL and mark the recipient as
unsubscribed without additional steps.

## Tests

```bash
python -m unittest discover -s tests
```

## Safety Notes

- Only send to recipients with proper consent.
- Configure SPF/DKIM/DMARC outside this tool.
- Warmup mode is restricted to allowlisted domains.