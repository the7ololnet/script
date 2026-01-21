"""
Structured logging configuration.

Provides JSON logging for production and human-readable logs for development.
"""

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Optional


class JSONFormatter(logging.Formatter):
    """
    JSON log formatter for structured logging.
    """
    
    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON."""
        log_data = {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
        }
        
        # Add extra fields if present
        if hasattr(record, 'email'):
            log_data['email'] = record.email
        if hasattr(record, 'server_id'):
            log_data['server_id'] = record.server_id
        if hasattr(record, 'duration_ms'):
            log_data['duration_ms'] = record.duration_ms
        if hasattr(record, 'error_type'):
            log_data['error_type'] = record.error_type
        
        # Add exception info if present
        if record.exc_info:
            log_data['exception'] = self.formatException(record.exc_info)
        
        return json.dumps(log_data)


class ConsoleFormatter(logging.Formatter):
    """
    Human-readable console formatter.
    """
    
    COLORS = {
        'DEBUG': '\033[36m',     # Cyan
        'INFO': '\033[32m',      # Green
        'WARNING': '\033[33m',   # Yellow
        'ERROR': '\033[31m',     # Red
        'CRITICAL': '\033[35m',  # Magenta
        'RESET': '\033[0m',
    }
    
    def format(self, record: logging.LogRecord) -> str:
        """Format log record for console."""
        # Add color if terminal supports it
        color = self.COLORS.get(record.levelname, '')
        reset = self.COLORS['RESET']
        
        # Format timestamp
        timestamp = datetime.now().strftime('%H:%M:%S')
        
        # Build message
        level = f"{color}{record.levelname:8}{reset}"
        message = record.getMessage()
        
        return f"{timestamp} {level} {message}"


def setup_logging(
    log_file: Optional[str] = None,
    console_level: int = logging.INFO,
    file_level: int = logging.DEBUG,
    json_logs: bool = True
) -> logging.Logger:
    """
    Set up logging configuration.
    
    Args:
        log_file: Path to log file (None for no file logging)
        console_level: Log level for console output
        file_level: Log level for file output
        json_logs: Use JSON format for file logs
        
    Returns:
        Root logger
    """
    # Get root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    
    # Remove existing handlers
    root_logger.handlers = []
    
    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(console_level)
    console_handler.setFormatter(ConsoleFormatter())
    root_logger.addHandler(console_handler)
    
    # File handler
    if log_file:
        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        file_handler.setLevel(file_level)
        
        if json_logs:
            file_handler.setFormatter(JSONFormatter())
        else:
            file_handler.setFormatter(logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            ))
        
        root_logger.addHandler(file_handler)
    
    return root_logger


def log_send_attempt(
    logger: logging.Logger,
    email: str,
    server_id: str,
    success: bool,
    duration_ms: float,
    error: Optional[str] = None,
    error_type: Optional[str] = None
):
    """
    Log a send attempt with structured data.
    
    Args:
        logger: Logger instance
        email: Recipient email
        server_id: SMTP server used
        success: Whether send succeeded
        duration_ms: Duration in milliseconds
        error: Error message if failed
        error_type: Error classification
    """
    extra = {
        'email': email,
        'server_id': server_id,
        'duration_ms': duration_ms,
    }
    
    if error_type:
        extra['error_type'] = error_type
    
    if success:
        logger.info(f"Sent to {email} via {server_id} ({duration_ms:.0f}ms)", extra=extra)
    else:
        extra['error'] = error
        logger.error(f"Failed to send to {email}: {error}", extra=extra)
