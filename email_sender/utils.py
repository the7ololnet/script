"""
Utility functions for email campaign sender.

Contains spintax parsing, name extraction, and other helper functions.
"""

import os
import re
import random
from typing import List, Set, Optional


def parse_spintax(text: str) -> str:
    """
    Parse spintax syntax {option1|option2|option3} and randomly select one option.
    Supports nested spintax and multiple spintax blocks in one string.
    
    Args:
        text: String potentially containing spintax blocks
        
    Returns:
        String with all spintax blocks replaced with random selections
        
    Examples:
        >>> parse_spintax("{Hello|Hi} {World|There}")  # Returns e.g. "Hi There"
        >>> parse_spintax("No spintax here")  # Returns "No spintax here"
        >>> parse_spintax("{A|B|C}")  # Returns one of "A", "B", or "C"
    """
    if not text:
        return ""
    
    # Pattern matches non-nested spintax blocks
    pattern = r'\{([^{}]+)\}'
    
    def replace_spintax(match):
        options = match.group(1).split('|')
        return random.choice(options).strip()
    
    # Replace all spintax blocks (iterate for nested spintax)
    result = text
    max_iterations = 10  # Prevent infinite loops
    iteration = 0
    
    while re.search(pattern, result) and iteration < max_iterations:
        result = re.sub(pattern, replace_spintax, result)
        iteration += 1
    
    return result.strip()


def extract_name_from_email(email: str) -> str:
    """
    Extract a plausible first name from an email address.
    
    Args:
        email: Email address to extract name from
        
    Returns:
        Capitalized first name or "there" as fallback
        
    Examples:
        >>> extract_name_from_email("karim.ben88@gmail.com")
        'Karim'
        >>> extract_name_from_email("john.doe@company.com")
        'John'
        >>> extract_name_from_email("sarah_miller123@yahoo.com")
        'Sarah'
        >>> extract_name_from_email("admin@example.com")
        'Admin'
        >>> extract_name_from_email("123@test.com")
        'there'
    """
    if not email or '@' not in email:
        return "there"
    
    # Get local part (before @)
    local_part = email.split('@')[0].lower()
    
    # Remove numbers and clean up
    cleaned = re.sub(r'\d+', '', local_part)
    
    # Try to extract first name from common patterns
    # Pattern 1: firstname.lastname or firstname_lastname or firstname-lastname
    match = re.match(r'^([a-zA-Z]+)[._-]', cleaned)
    if match:
        name = match.group(1)
        if len(name) >= 2:
            return name.capitalize()
    
    # Pattern 2: Just letters at the start
    match = re.match(r'^([a-zA-Z]+)', cleaned)
    if match:
        name = match.group(1)
        if len(name) >= 2:
            return name.capitalize()
    
    # Fallback
    return "there"


def normalize_email(email: str) -> Optional[str]:
    """
    Normalize and validate an email address.
    
    Args:
        email: Raw email string
        
    Returns:
        Normalized email or None if invalid
    """
    if not email:
        return None
    
    # Strip whitespace and convert to lowercase
    email = email.strip().lower()
    
    # Basic validation
    if '@' not in email or '.' not in email:
        return None
    
    # Check for valid format
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    if not re.match(pattern, email, re.IGNORECASE):
        return None
    
    return email


def load_emails_from_file(filepath: str) -> List[str]:
    """
    Load and deduplicate email addresses from a file.
    
    Args:
        filepath: Path to file containing email addresses (one per line)
        
    Returns:
        List of normalized, deduplicated email addresses
    """
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"Email file not found: {filepath}")
    
    emails = []
    seen: Set[str] = set()
    
    with open(filepath, 'r', encoding='utf-8') as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            
            # Skip empty lines and comments
            if not line or line.startswith('#'):
                continue
            
            # Normalize and validate
            normalized = normalize_email(line)
            if normalized:
                if normalized not in seen:
                    emails.append(normalized)
                    seen.add(normalized)
            # Silently skip invalid emails
    
    return emails


def load_suppression_list(filepath: Optional[str]) -> Set[str]:
    """
    Load suppression list (emails to skip).
    
    Args:
        filepath: Path to suppression file or None
        
    Returns:
        Set of normalized email addresses to suppress
    """
    if not filepath or not os.path.exists(filepath):
        return set()
    
    suppressed = set()
    with open(filepath, 'r', encoding='utf-8') as f:
        for line in f:
            normalized = normalize_email(line.strip())
            if normalized:
                suppressed.add(normalized)
    
    return suppressed


def load_allowlist_domains(filepath: Optional[str]) -> Optional[Set[str]]:
    """
    Load allowlist of domains for WARMUP mode.
    
    Args:
        filepath: Path to allowlist file or None
        
    Returns:
        Set of allowed domains (lowercase) or None if no restriction
    """
    if not filepath or not os.path.exists(filepath):
        return None
    
    domains = set()
    with open(filepath, 'r', encoding='utf-8') as f:
        for line in f:
            domain = line.strip().lower()
            if domain and not domain.startswith('#'):
                # Remove @ prefix if present
                if domain.startswith('@'):
                    domain = domain[1:]
                domains.add(domain)
    
    return domains if domains else None


def is_email_in_allowlist(email: str, allowlist: Optional[Set[str]]) -> bool:
    """
    Check if email domain is in the allowlist.
    
    Args:
        email: Email address to check
        allowlist: Set of allowed domains or None for no restriction
        
    Returns:
        True if allowed or no restriction, False otherwise
    """
    if allowlist is None:
        return True
    
    if '@' not in email:
        return False
    
    domain = email.split('@')[1].lower()
    return domain in allowlist


def classify_smtp_error(error: Exception) -> str:
    """
    Classify SMTP error as transient or permanent.
    
    Args:
        error: The exception that occurred
        
    Returns:
        One of: "transient", "permanent", "auth", "connection"
    """
    error_str = str(error).lower()
    error_type = type(error).__name__
    
    # Connection errors
    if 'connection' in error_str or 'timeout' in error_str:
        return "connection"
    
    if 'refused' in error_str or 'reset' in error_str:
        return "connection"
    
    # Authentication errors
    if 'auth' in error_str or '535' in error_str or '530' in error_str:
        return "auth"
    
    # Check SMTP response codes if available
    if hasattr(error, 'smtp_code'):
        code = error.smtp_code
        if 400 <= code < 500:
            return "transient"  # 4xx are transient
        if code >= 500:
            return "permanent"  # 5xx are permanent
    
    # Check for specific transient patterns
    transient_patterns = [
        'try again', 'temporarily', 'too many', 'rate limit',
        'busy', 'unavailable', '421', '450', '451', '452'
    ]
    if any(p in error_str for p in transient_patterns):
        return "transient"
    
    # Check for permanent patterns
    permanent_patterns = [
        'does not exist', 'invalid', 'rejected', 'blocked',
        'blacklist', 'spam', '550', '551', '552', '553', '554'
    ]
    if any(p in error_str for p in permanent_patterns):
        return "permanent"
    
    # Default to transient (safer for retries)
    return "transient"


def is_retryable_error(error: Exception) -> bool:
    """
    Determine if an error should trigger a retry.
    
    Args:
        error: The exception that occurred
        
    Returns:
        True if the operation should be retried
    """
    classification = classify_smtp_error(error)
    return classification in ("transient", "connection")


def calculate_backoff(attempt: int, base: float = 2.0, max_delay: float = 60.0) -> float:
    """
    Calculate exponential backoff delay with jitter.
    
    Args:
        attempt: Current attempt number (0-indexed)
        base: Base delay multiplier
        max_delay: Maximum delay in seconds
        
    Returns:
        Delay in seconds with jitter applied
    """
    # Exponential backoff: base^attempt
    delay = min(base ** attempt, max_delay)
    
    # Add jitter (0.5 to 1.5 of calculated delay)
    jitter = random.uniform(0.5, 1.5)
    
    return delay * jitter
