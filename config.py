from __future__ import annotations

import os
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _load_dotenv_if_available() -> None:
    try:
        from dotenv import load_dotenv  # type: ignore
    except Exception:
        return
    load_dotenv()


def _parse_bool(value: Optional[str], default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _parse_int(value: Optional[str], default: int) -> int:
    if value is None or value == "":
        return default
    return int(value)


def _parse_float(value: Optional[str], default: float) -> float:
    if value is None or value == "":
        return default
    return float(value)


class SMTPConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str
    server: str
    port: int
    user: str
    pass_: str = Field(alias="pass", repr=False)
    sender_email: str
    domain: str
    sender_name: str
    reply_to: str
    list_id: Optional[str] = None
    unsub_url: Optional[str] = None
    use_starttls: bool = True
    use_ssl: bool = False
    weight: int = 1
    timeout_seconds: float = 20.0

    @field_validator("port")
    @classmethod
    def _validate_port(cls, value: int) -> int:
        if value <= 0 or value > 65535:
            raise ValueError("port must be in 1-65535")
        return value

    @field_validator("weight")
    @classmethod
    def _validate_weight(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("weight must be >= 1")
        return value

    @model_validator(mode="after")
    def _validate_tls_flags(self) -> "SMTPConfig":
        if self.use_ssl and self.use_starttls:
            raise ValueError("use_ssl and use_starttls cannot both be true")
        return self


class CampaignConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["WARMUP", "OFFER"] = "WARMUP"
    emails_file: str = "emails.txt"
    suppression_file: str = "suppression.txt"
    allowlist_file: Optional[str] = None
    db_path: str = "campaign.db"
    log_file: str = "campaign.log"

    min_sleep_seconds: int = 10
    max_sleep_seconds: int = 40
    rate_per_minute: int = 30
    dry_run: bool = False
    server_rotation: Literal["roundrobin", "weighted"] = "roundrobin"
    max_per_server_per_hour: int = 200

    max_attempts: int = 4
    retry_base_seconds: float = 2.0
    retry_max_seconds: float = 30.0
    circuit_breaker_threshold: int = 3
    circuit_breaker_cooldown_seconds: int = 300

    openai_api_key: Optional[str] = None
    openai_model: str = "gpt-4o-mini"
    openai_base_url: Optional[str] = None
    use_ai: bool = True
    ai_rate_per_minute: int = 60
    ai_timeout_seconds: float = 30.0
    cache_ai_content: bool = True

    company_name: str = "Example Company"
    company_address: str = "123 Example Street, City, ST 00000"
    unsubscribe_instructions: str = "To unsubscribe, use the link provided in this email."

    @model_validator(mode="after")
    def _validate_sleep(self) -> "CampaignConfig":
        if self.min_sleep_seconds < 0:
            raise ValueError("min_sleep_seconds must be >= 0")
        if self.max_sleep_seconds < self.min_sleep_seconds:
            raise ValueError("max_sleep_seconds must be >= min_sleep_seconds")
        if self.rate_per_minute <= 0:
            raise ValueError("rate_per_minute must be > 0")
        if self.max_attempts <= 0:
            raise ValueError("max_attempts must be > 0")
        if self.ai_rate_per_minute <= 0:
            raise ValueError("ai_rate_per_minute must be > 0")
        return self

    @model_validator(mode="after")
    def _validate_ai_config(self) -> "CampaignConfig":
        if self.use_ai and not self.openai_api_key:
            raise ValueError("openai_api_key is required when use_ai is true")
        return self


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    smtp_servers: List[SMTPConfig]
    campaign: CampaignConfig


def _read_smtp_config(prefix: str, server_id: str) -> Optional[SMTPConfig]:
    required_keys = [
        "SERVER",
        "PORT",
        "USER",
        "PASS",
        "SENDER_EMAIL",
        "DOMAIN",
        "SENDER_NAME",
        "REPLY_TO",
    ]
    env = os.environ
    present = {key: env.get(f"{prefix}_{key}") for key in required_keys}
    if all(value in (None, "") for value in present.values()):
        return None

    missing = [key for key, value in present.items() if not value]
    if missing:
        missing_csv = ", ".join(f"{prefix}_{key}" for key in missing)
        raise ValueError(f"Missing required SMTP settings: {missing_csv}")

    port = _parse_int(env.get(f"{prefix}_PORT"), 0)
    use_ssl = _parse_bool(env.get(f"{prefix}_USE_SSL"), False)
    use_starttls = _parse_bool(env.get(f"{prefix}_USE_STARTTLS"), not use_ssl)
    if port == 465 and env.get(f"{prefix}_USE_SSL") is None:
        use_ssl = True
        use_starttls = False

    return SMTPConfig(
        id=server_id,
        server=env.get(f"{prefix}_SERVER", ""),
        port=port,
        user=env.get(f"{prefix}_USER", ""),
        pass_=env.get(f"{prefix}_PASS", ""),
        sender_email=env.get(f"{prefix}_SENDER_EMAIL", ""),
        domain=env.get(f"{prefix}_DOMAIN", ""),
        sender_name=env.get(f"{prefix}_SENDER_NAME", ""),
        reply_to=env.get(f"{prefix}_REPLY_TO", ""),
        list_id=env.get(f"{prefix}_LIST_ID") or None,
        unsub_url=env.get(f"{prefix}_UNSUB_URL") or None,
        use_starttls=use_starttls,
        use_ssl=use_ssl,
        weight=_parse_int(env.get(f"{prefix}_WEIGHT"), 1),
        timeout_seconds=_parse_float(env.get(f"{prefix}_TIMEOUT_SECONDS"), 20.0),
    )


def load_config_from_env() -> AppConfig:
    _load_dotenv_if_available()

    smtp_servers: List[SMTPConfig] = []
    for index, prefix in enumerate(("SMTP_1", "SMTP_2"), start=1):
        smtp = _read_smtp_config(prefix, server_id=str(index))
        if smtp:
            smtp_servers.append(smtp)

    if not smtp_servers:
        raise ValueError("At least one SMTP configuration must be provided.")

    campaign = CampaignConfig(
        mode=os.getenv("CAMPAIGN_MODE", "WARMUP"),
        emails_file=os.getenv("EMAILS_FILE", "emails.txt"),
        suppression_file=os.getenv("SUPPRESSION_FILE", "suppression.txt"),
        allowlist_file=os.getenv("ALLOWLIST_FILE"),
        db_path=os.getenv("DB_PATH", "campaign.db"),
        log_file=os.getenv("LOG_FILE", "campaign.log"),
        min_sleep_seconds=_parse_int(os.getenv("MIN_SLEEP_SECONDS"), 10),
        max_sleep_seconds=_parse_int(os.getenv("MAX_SLEEP_SECONDS"), 40),
        rate_per_minute=_parse_int(os.getenv("RATE_PER_MINUTE"), 30),
        dry_run=_parse_bool(os.getenv("DRY_RUN"), False),
        server_rotation=os.getenv("SERVER_ROTATION", "roundrobin"),
        max_per_server_per_hour=_parse_int(os.getenv("MAX_PER_SERVER_PER_HOUR"), 200),
        max_attempts=_parse_int(os.getenv("MAX_ATTEMPTS"), 4),
        retry_base_seconds=_parse_float(os.getenv("RETRY_BASE_SECONDS"), 2.0),
        retry_max_seconds=_parse_float(os.getenv("RETRY_MAX_SECONDS"), 30.0),
        circuit_breaker_threshold=_parse_int(os.getenv("CIRCUIT_BREAKER_THRESHOLD"), 3),
        circuit_breaker_cooldown_seconds=_parse_int(os.getenv("CIRCUIT_BREAKER_COOLDOWN_SECONDS"), 300),
        openai_api_key=os.getenv("OPENAI_API_KEY"),
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        openai_base_url=os.getenv("OPENAI_BASE_URL"),
        use_ai=_parse_bool(os.getenv("USE_AI"), True),
        ai_rate_per_minute=_parse_int(os.getenv("AI_RATE_PER_MINUTE"), 60),
        ai_timeout_seconds=_parse_float(os.getenv("AI_TIMEOUT_SECONDS"), 30.0),
        cache_ai_content=_parse_bool(os.getenv("CACHE_AI_CONTENT"), True),
        company_name=os.getenv("COMPANY_NAME", "Example Company"),
        company_address=os.getenv("COMPANY_ADDRESS", "123 Example Street, City, ST 00000"),
        unsubscribe_instructions=os.getenv(
            "UNSUBSCRIBE_INSTRUCTIONS",
            "To unsubscribe, use the link provided in this email.",
        ),
    )

    return AppConfig(smtp_servers=smtp_servers, campaign=campaign)


def apply_cli_overrides(campaign: CampaignConfig, overrides: Dict[str, object]) -> CampaignConfig:
    clean_overrides = {key: value for key, value in overrides.items() if value is not None}
    if not clean_overrides:
        return campaign
    return campaign.model_copy(update=clean_overrides)
