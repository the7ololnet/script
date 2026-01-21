"""
Configuration module for email campaign sender.

Loads configuration from environment variables and validates all required settings.
Uses dataclasses with validation for type safety and fail-fast behavior.
"""

import os
import re
import sys
from dataclasses import dataclass, field
from typing import List, Optional
from urllib.parse import urlparse


class ConfigurationError(Exception):
    """Raised when configuration validation fails."""
    pass


def _get_env(key: str, default: Optional[str] = None, required: bool = False) -> Optional[str]:
    """Get environment variable with optional default and required flag."""
    value = os.environ.get(key, default)
    if required and not value:
        raise ConfigurationError(f"Required environment variable '{key}' is not set")
    return value


def _get_env_int(key: str, default: int) -> int:
    """Get integer environment variable."""
    value = os.environ.get(key)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        raise ConfigurationError(f"Environment variable '{key}' must be an integer, got: {value}")


def _get_env_bool(key: str, default: bool) -> bool:
    """Get boolean environment variable."""
    value = os.environ.get(key)
    if value is None:
        return default
    return value.lower() in ('true', '1', 'yes', 'on')


def _validate_email(email: str, field_name: str) -> str:
    """Validate email format."""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    if not re.match(pattern, email):
        raise ConfigurationError(f"Invalid email format for {field_name}: {email}")
    return email


def _validate_url(url: str, field_name: str) -> str:
    """Validate URL format."""
    try:
        result = urlparse(url)
        if not all([result.scheme in ('http', 'https'), result.netloc]):
            raise ConfigurationError(f"Invalid URL for {field_name}: {url}")
    except Exception:
        raise ConfigurationError(f"Invalid URL for {field_name}: {url}")
    return url


def _validate_port(port: int, field_name: str) -> int:
    """Validate port number."""
    if not (1 <= port <= 65535):
        raise ConfigurationError(f"Invalid port for {field_name}: {port}. Must be 1-65535")
    return port


@dataclass
class SMTPConfig:
    """Configuration for a single SMTP server."""
    server: str
    port: int
    user: str
    password: str
    sender_email: str
    domain: str
    sender_name: str  # Can contain spintax
    reply_to: str
    list_id: Optional[str] = None
    unsub_url: Optional[str] = None
    use_tls: bool = True  # STARTTLS by default
    use_ssl: bool = False  # Implicit TLS
    timeout: int = 30
    
    def __post_init__(self):
        """Validate configuration after initialization."""
        if not self.server:
            raise ConfigurationError("SMTP server address is required")
        self.port = _validate_port(self.port, "port")
        if not self.user:
            raise ConfigurationError("SMTP user is required")
        if not self.password:
            raise ConfigurationError("SMTP password is required")
        self.sender_email = _validate_email(self.sender_email, "sender_email")
        if not self.domain:
            raise ConfigurationError("Domain is required")
        if not self.sender_name:
            raise ConfigurationError("Sender name is required")
        self.reply_to = _validate_email(self.reply_to, "reply_to")
        if self.unsub_url:
            self.unsub_url = _validate_url(self.unsub_url, "unsub_url")


@dataclass
class CampaignConfig:
    """Configuration for a campaign run."""
    mode: str = "WARMUP"  # WARMUP or OFFER
    emails_file: str = "emails.txt"
    suppression_file: Optional[str] = None
    allowlist_file: Optional[str] = None  # For WARMUP mode domain restriction
    min_sleep: int = 10
    max_sleep: int = 40
    rate_per_minute: int = 10
    max_per_server_per_hour: int = 100
    server_rotation: str = "roundrobin"  # roundrobin or weighted
    dry_run: bool = False
    db_path: str = "campaign_state.db"
    log_file: str = "campaign.log"
    max_retries: int = 3
    retry_backoff_base: float = 2.0
    retry_max_delay: float = 60.0
    
    def __post_init__(self):
        """Validate campaign configuration."""
        if self.mode not in ("WARMUP", "OFFER"):
            raise ConfigurationError(f"Invalid mode: {self.mode}. Must be WARMUP or OFFER")
        if self.min_sleep < 0:
            raise ConfigurationError("min_sleep must be non-negative")
        if self.max_sleep < self.min_sleep:
            raise ConfigurationError("max_sleep must be >= min_sleep")
        if self.rate_per_minute <= 0:
            raise ConfigurationError("rate_per_minute must be positive")
        if self.server_rotation not in ("roundrobin", "weighted"):
            raise ConfigurationError(f"Invalid server_rotation: {self.server_rotation}")


@dataclass 
class AIConfig:
    """Configuration for AI content generation."""
    api_key: str = ""
    model: str = "gpt-4o-mini"
    enabled: bool = True
    rate_limit_per_minute: int = 20
    cache_enabled: bool = True
    timeout: int = 30
    max_retries: int = 3
    
    def __post_init__(self):
        """Validate AI configuration."""
        if self.enabled and not self.api_key:
            raise ConfigurationError(
                "OpenAI API key is required when AI is enabled. "
                "Set OPENAI_API_KEY environment variable or disable AI with --no-ai"
            )


@dataclass
class AppConfig:
    """Main application configuration."""
    smtp_configs: List[SMTPConfig] = field(default_factory=list)
    campaign: CampaignConfig = field(default_factory=CampaignConfig)
    ai: AIConfig = field(default_factory=AIConfig)
    
    def __post_init__(self):
        """Validate that we have at least one SMTP config."""
        if not self.smtp_configs:
            raise ConfigurationError("At least one SMTP server configuration is required")


def load_smtp_config_from_env(prefix: str) -> Optional[SMTPConfig]:
    """
    Load SMTP configuration from environment variables with a given prefix.
    
    Example: prefix="SMTP1" looks for SMTP1_SERVER, SMTP1_PORT, etc.
    """
    server = os.environ.get(f"{prefix}_SERVER")
    if not server:
        return None
    
    return SMTPConfig(
        server=server,
        port=_get_env_int(f"{prefix}_PORT", 587),
        user=_get_env(f"{prefix}_USER", required=True),
        password=_get_env(f"{prefix}_PASS", required=True),
        sender_email=_get_env(f"{prefix}_SENDER_EMAIL", required=True),
        domain=_get_env(f"{prefix}_DOMAIN", required=True),
        sender_name=_get_env(f"{prefix}_SENDER_NAME", required=True),
        reply_to=_get_env(f"{prefix}_REPLY_TO", required=True),
        list_id=_get_env(f"{prefix}_LIST_ID"),
        unsub_url=_get_env(f"{prefix}_UNSUB_URL"),
        use_tls=_get_env_bool(f"{prefix}_USE_TLS", True),
        use_ssl=_get_env_bool(f"{prefix}_USE_SSL", False),
        timeout=_get_env_int(f"{prefix}_TIMEOUT", 30),
    )


def load_config_from_env() -> AppConfig:
    """
    Load complete application configuration from environment variables.
    
    Attempts to load .env file if python-dotenv is available.
    """
    # Try to load .env file
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass  # python-dotenv not installed, continue with regular env vars
    
    # Load SMTP configurations (support up to 10 servers)
    smtp_configs = []
    for i in range(1, 11):
        config = load_smtp_config_from_env(f"SMTP{i}")
        if config:
            smtp_configs.append(config)
    
    if not smtp_configs:
        raise ConfigurationError(
            "No SMTP servers configured. Set SMTP1_SERVER, SMTP1_PORT, etc. "
            "See .env.example for required variables."
        )
    
    # Load AI configuration
    ai_enabled = _get_env_bool("AI_ENABLED", True)
    ai_config = AIConfig(
        api_key=_get_env("OPENAI_API_KEY", "") if ai_enabled else "",
        model=_get_env("OPENAI_MODEL", "gpt-4o-mini"),
        enabled=ai_enabled,
        rate_limit_per_minute=_get_env_int("AI_RATE_LIMIT_PER_MINUTE", 20),
        cache_enabled=_get_env_bool("AI_CACHE_ENABLED", True),
        timeout=_get_env_int("AI_TIMEOUT", 30),
        max_retries=_get_env_int("AI_MAX_RETRIES", 3),
    )
    
    # Load campaign defaults
    campaign_config = CampaignConfig(
        mode=_get_env("CAMPAIGN_MODE", "WARMUP"),
        emails_file=_get_env("EMAILS_FILE", "emails.txt"),
        suppression_file=_get_env("SUPPRESSION_FILE"),
        allowlist_file=_get_env("ALLOWLIST_FILE"),
        min_sleep=_get_env_int("MIN_SLEEP", 10),
        max_sleep=_get_env_int("MAX_SLEEP", 40),
        rate_per_minute=_get_env_int("RATE_PER_MINUTE", 10),
        max_per_server_per_hour=_get_env_int("MAX_PER_SERVER_PER_HOUR", 100),
        server_rotation=_get_env("SERVER_ROTATION", "roundrobin"),
        db_path=_get_env("DB_PATH", "campaign_state.db"),
        log_file=_get_env("LOG_FILE", "campaign.log"),
        max_retries=_get_env_int("MAX_RETRIES", 3),
    )
    
    return AppConfig(
        smtp_configs=smtp_configs,
        campaign=campaign_config,
        ai=ai_config,
    )


def validate_startup(config: AppConfig) -> List[str]:
    """
    Perform startup validation and return list of warnings.
    
    Raises ConfigurationError if critical issues found.
    """
    warnings = []
    
    # Check WARMUP mode restrictions
    if config.campaign.mode == "WARMUP":
        if not config.campaign.allowlist_file:
            warnings.append(
                "WARMUP mode without allowlist_file. Consider restricting to seeded domains."
            )
        for smtp in config.smtp_configs:
            if smtp.unsub_url:
                warnings.append(
                    f"WARMUP mode with unsub_url set for {smtp.domain}. "
                    "Marketing headers will be included which may affect warmup."
                )
    
    # Check OFFER mode requirements
    if config.campaign.mode == "OFFER":
        for smtp in config.smtp_configs:
            if not smtp.unsub_url:
                raise ConfigurationError(
                    f"OFFER mode requires unsub_url for {smtp.domain}. "
                    "Set {prefix}_UNSUB_URL environment variable."
                )
    
    return warnings
