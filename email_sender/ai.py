"""
AI content generation module using OpenAI.

Handles rate limiting, retries, and content validation.
"""

import json
import logging
import time
from dataclasses import dataclass
from typing import Optional, Dict, Any
from threading import Lock

from .config import AIConfig
from .utils import calculate_backoff

logger = logging.getLogger(__name__)


@dataclass
class ContentResult:
    """Result of content generation."""
    subject: str
    body_text: str
    body_html: str
    from_cache: bool = False


class RateLimiter:
    """Simple rate limiter for API calls."""
    
    def __init__(self, requests_per_minute: int):
        self.requests_per_minute = requests_per_minute
        self.min_interval = 60.0 / requests_per_minute
        self.last_request_time = 0.0
        self._lock = Lock()
    
    def wait_if_needed(self):
        """Block until rate limit allows another request."""
        with self._lock:
            now = time.time()
            time_since_last = now - self.last_request_time
            
            if time_since_last < self.min_interval:
                sleep_time = self.min_interval - time_since_last
                time.sleep(sleep_time)
            
            self.last_request_time = time.time()


class AIContentGenerator:
    """
    AI-powered content generator with rate limiting and retries.
    
    Uses OpenAI API to generate unique email content per recipient.
    """
    
    WARMUP_SYSTEM_PROMPT = """You are writing a brief, casual email for internal testing purposes.
Generate a short, friendly email about a neutral topic (weather, general check-in, etc.).
Keep it to 2-3 sentences maximum. NO links, NO promotional content, NO marketing language.
Output ONLY valid JSON with exactly these keys: subject, body_text, body_html
The body_html should be simple HTML (p tags only) matching the body_text content."""

    OFFER_SYSTEM_PROMPT = """You are a professional email copywriter creating engaging marketing emails.
Generate a compelling email with a clear but soft call-to-action.
Keep it professional, friendly, and not pushy. Around 3-4 paragraphs.
Output ONLY valid JSON with exactly these keys: subject, body_text, body_html
The body_html should use proper HTML formatting (p, strong, em tags as appropriate)."""

    WARMUP_USER_TEMPLATE = """Generate a warm, casual internal test email to {name} ({email}).
The email should be brief and conversational, about a neutral everyday topic."""

    OFFER_USER_TEMPLATE = """Generate a marketing email to {name} ({email}).
The email should be engaging and include a soft call-to-action."""

    def __init__(self, config: AIConfig, state_store: Optional[Any] = None):
        """
        Initialize AI content generator.
        
        Args:
            config: AI configuration
            state_store: Optional StateStore for caching
        """
        self.config = config
        self.state_store = state_store
        self._client = None
        self._rate_limiter = RateLimiter(config.rate_limit_per_minute)
    
    @property
    def client(self):
        """Lazy initialization of OpenAI client."""
        if self._client is None:
            try:
                import openai
                self._client = openai.OpenAI(
                    api_key=self.config.api_key,
                    timeout=self.config.timeout
                )
            except ImportError:
                raise ImportError(
                    "OpenAI package not installed. "
                    "Install with: pip install openai"
                )
        return self._client
    
    def generate_content(
        self,
        email: str,
        name: str,
        mode: str,
        use_cache: bool = True
    ) -> ContentResult:
        """
        Generate email content for a recipient.
        
        Args:
            email: Recipient email address
            name: Recipient name
            mode: Campaign mode (WARMUP or OFFER)
            use_cache: Whether to use cached content if available
            
        Returns:
            ContentResult with generated content
        """
        # Check cache first
        if use_cache and self.config.cache_enabled and self.state_store:
            cached = self.state_store.get_cached_content(email, mode)
            if cached:
                logger.debug(f"Using cached content for {email}")
                return ContentResult(
                    subject=cached['subject'],
                    body_text=cached['body_text'],
                    body_html=cached['body_html'],
                    from_cache=True
                )
        
        # Generate new content
        content = self._generate_with_retry(email, name, mode)
        
        # Cache the result
        if self.config.cache_enabled and self.state_store:
            self.state_store.cache_content(
                email, mode,
                content.subject, content.body_text, content.body_html
            )
        
        return content
    
    def _generate_with_retry(
        self,
        email: str,
        name: str,
        mode: str
    ) -> ContentResult:
        """Generate content with retry logic."""
        last_error = None
        
        for attempt in range(self.config.max_retries):
            try:
                # Apply rate limiting
                self._rate_limiter.wait_if_needed()
                
                # Make API call
                return self._call_api(email, name, mode)
                
            except Exception as e:
                last_error = e
                error_str = str(e).lower()
                
                # Check if retryable
                if '429' in error_str or 'rate limit' in error_str:
                    # Rate limited - wait longer
                    delay = calculate_backoff(attempt, base=5.0, max_delay=120.0)
                    logger.warning(f"Rate limited, waiting {delay:.1f}s before retry")
                    time.sleep(delay)
                    continue
                
                if any(code in error_str for code in ['500', '502', '503', '504']):
                    # Server error - retry with backoff
                    delay = calculate_backoff(attempt)
                    logger.warning(f"Server error, retrying in {delay:.1f}s: {e}")
                    time.sleep(delay)
                    continue
                
                # Non-retryable error
                logger.error(f"Non-retryable AI error: {e}")
                break
        
        # All retries failed - return fallback
        logger.warning(f"AI generation failed after {self.config.max_retries} attempts, using fallback")
        return self._get_fallback_content(name, mode)
    
    def _call_api(self, email: str, name: str, mode: str) -> ContentResult:
        """Make actual API call to OpenAI."""
        # Select prompts based on mode
        if mode == "WARMUP":
            system_prompt = self.WARMUP_SYSTEM_PROMPT
            user_prompt = self.WARMUP_USER_TEMPLATE.format(name=name, email=email)
        else:
            system_prompt = self.OFFER_SYSTEM_PROMPT
            user_prompt = self.OFFER_USER_TEMPLATE.format(name=name, email=email)
        
        response = self.client.chat.completions.create(
            model=self.config.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            response_format={"type": "json_object"},
            temperature=0.7
        )
        
        # Parse response
        content_str = response.choices[0].message.content
        content = json.loads(content_str)
        
        # Validate required keys
        required_keys = ['subject', 'body_text', 'body_html']
        for key in required_keys:
            if key not in content:
                raise ValueError(f"AI response missing required key: {key}")
        
        return ContentResult(
            subject=content['subject'],
            body_text=content['body_text'],
            body_html=content['body_html'],
            from_cache=False
        )
    
    def _get_fallback_content(self, name: str, mode: str) -> ContentResult:
        """Get fallback content when AI fails."""
        if mode == "WARMUP":
            return ContentResult(
                subject="Quick hello",
                body_text=f"Hi {name},\n\nHope you're doing well! Just wanted to check in and say hello.\n\nBest regards",
                body_html=f"<p>Hi {name},</p><p>Hope you're doing well! Just wanted to check in and say hello.</p><p>Best regards</p>",
                from_cache=False
            )
        else:
            return ContentResult(
                subject="Something you might find interesting",
                body_text=f"Hi {name},\n\nI thought you might find this interesting and wanted to share it with you.\n\nLet me know if you'd like to learn more.\n\nBest regards",
                body_html=f"<p>Hi {name},</p><p>I thought you might find this interesting and wanted to share it with you.</p><p>Let me know if you'd like to learn more.</p><p>Best regards</p>",
                from_cache=False
            )


class FallbackContentGenerator:
    """
    Simple template-based content generator for no-AI mode.
    """
    
    WARMUP_SUBJECTS = [
        "Quick hello",
        "Checking in",
        "Hope all is well",
        "Just saying hi",
    ]
    
    WARMUP_TEMPLATES = [
        "Hi {name},\n\nHope you're having a great day! Just wanted to reach out and say hello.\n\nBest regards",
        "Hello {name},\n\nJust checking in to see how things are going. Hope all is well!\n\nCheers",
        "Hi {name},\n\nHope this message finds you well. Thinking of you today.\n\nAll the best",
    ]
    
    OFFER_SUBJECTS = [
        "Something you might like",
        "Quick update for you",
        "An opportunity worth exploring",
    ]
    
    OFFER_TEMPLATES = [
        "Hi {name},\n\nI wanted to share something I think you might find valuable.\n\nWould you be interested in learning more? Just reply to this email.\n\nBest regards",
        "Hello {name},\n\nI hope you're doing well! I have something exciting to share with you.\n\nLet me know if you'd like more details.\n\nCheers",
    ]
    
    def __init__(self, state_store: Optional[Any] = None):
        self.state_store = state_store
        self._index = 0
    
    def generate_content(
        self,
        email: str,
        name: str,
        mode: str,
        use_cache: bool = True
    ) -> ContentResult:
        """Generate content using templates."""
        import random
        
        # Check cache first
        if use_cache and self.state_store:
            cached = self.state_store.get_cached_content(email, mode)
            if cached:
                return ContentResult(
                    subject=cached['subject'],
                    body_text=cached['body_text'],
                    body_html=cached['body_html'],
                    from_cache=True
                )
        
        # Select template
        if mode == "WARMUP":
            subject = random.choice(self.WARMUP_SUBJECTS)
            body_text = random.choice(self.WARMUP_TEMPLATES).format(name=name)
        else:
            subject = random.choice(self.OFFER_SUBJECTS)
            body_text = random.choice(self.OFFER_TEMPLATES).format(name=name)
        
        # Convert to HTML
        body_html = "<p>" + body_text.replace("\n\n", "</p><p>") + "</p>"
        
        # Cache result
        if self.state_store:
            self.state_store.cache_content(email, mode, subject, body_text, body_html)
        
        return ContentResult(
            subject=subject,
            body_text=body_text,
            body_html=body_html,
            from_cache=False
        )


def create_content_generator(
    config: AIConfig,
    state_store: Optional[Any] = None
) -> Any:
    """
    Factory function to create appropriate content generator.
    
    Args:
        config: AI configuration
        state_store: Optional StateStore for caching
        
    Returns:
        Content generator instance
    """
    if config.enabled:
        return AIContentGenerator(config, state_store)
    else:
        logger.info("AI disabled, using template-based content generator")
        return FallbackContentGenerator(state_store)
