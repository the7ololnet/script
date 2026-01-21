from __future__ import annotations

import json
import random
import time
from typing import Dict, Optional

from utils import RateLimiter


class AIContentGenerator:
    def __init__(
        self,
        api_key: Optional[str],
        model: str,
        rate_per_minute: int,
        timeout_seconds: float,
        use_ai: bool = True,
        cache_enabled: bool = True,
        base_url: Optional[str] = None,
        max_attempts: int = 3,
    ) -> None:
        self.use_ai = use_ai and bool(api_key)
        self.cache_enabled = cache_enabled
        self.model = model
        self.rate_limiter = RateLimiter(rate_per_minute)
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max_attempts
        self.client = None

        if self.use_ai:
            try:
                import openai  # type: ignore
            except Exception:
                self.use_ai = False
                return
            self.client = openai.OpenAI(api_key=api_key, base_url=base_url, timeout=timeout_seconds)

    def get_content(
        self,
        email: str,
        recipient_name: str,
        mode: str,
        company_name: str,
        company_address: str,
        unsub_url: Optional[str],
        state_store: Optional[object] = None,
    ) -> Dict[str, str]:
        if self.cache_enabled and state_store:
            cached = state_store.get_cached_content(email, mode)
            if cached:
                return cached

        if not self.use_ai or not self.client:
            content = self._fallback_content(recipient_name, mode, company_name, company_address, unsub_url)
        else:
            content = self._generate_content(email, recipient_name, mode)
            if mode == "OFFER":
                content = self._add_offer_footer(content, company_name, company_address, unsub_url)

        if self.cache_enabled and state_store:
            state_store.cache_content(email, mode, content)
        return content

    def _generate_content(self, email: str, recipient_name: str, mode: str) -> Dict[str, str]:
        system_prompt, user_prompt = self._build_prompts(email, recipient_name, mode)

        for attempt in range(1, self.max_attempts + 1):
            try:
                self.rate_limiter.wait()
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.7,
                )
                content = json.loads(response.choices[0].message.content)
                self._validate_content_schema(content)
                return content
            except Exception as exc:  # pragma: no cover - exercised in integration
                if attempt >= self.max_attempts or not self._is_retryable_openai_error(exc):
                    raise
                sleep_for = min(30.0, 2.0 ** (attempt - 1)) + random.uniform(0, 1.0)
                time.sleep(sleep_for)
        raise RuntimeError("OpenAI content generation failed after retries")

    def _build_prompts(self, email: str, recipient_name: str, mode: str) -> tuple[str, str]:
        if mode == "WARMUP":
            system_prompt = (
                "You are a professional email writer. Generate a casual, 2-sentence email "
                "about a neutral topic. No links or promotional content. "
                "Output ONLY valid JSON with keys: subject, body_text, body_html."
            )
            user_prompt = (
                f"Generate a warm, casual email to {recipient_name} ({email}). "
                "Two sentences maximum, neutral topic, no links."
            )
        else:
            system_prompt = (
                "You are a professional email marketer. Generate a curiosity-driven email with "
                "a soft call-to-action. Output ONLY valid JSON with keys: subject, body_text, body_html."
            )
            user_prompt = (
                f"Generate a marketing email to {recipient_name} ({email}). "
                "Include a soft CTA and keep the tone respectful."
            )
        return system_prompt, user_prompt

    def _validate_content_schema(self, content: Dict[str, object]) -> None:
        required = {"subject", "body_text", "body_html"}
        if not isinstance(content, dict) or not required.issubset(content.keys()):
            raise ValueError("AI response missing required keys")
        for key in required:
            if not isinstance(content[key], str):
                raise ValueError(f"AI response key {key} must be a string")

    def _fallback_content(
        self,
        recipient_name: str,
        mode: str,
        company_name: str,
        company_address: str,
        unsub_url: Optional[str],
    ) -> Dict[str, str]:
        if mode == "WARMUP":
            subject = "Just checking in"
            body_text = (
                f"Hi {recipient_name},\n\n"
                "Hope you're doing well. Just wanted to check in and see how things are going.\n"
            )
            body_html = (
                f"<p>Hi {recipient_name},</p>"
                "<p>Hope you're doing well. Just wanted to check in and see how things are going.</p>"
            )
            return {"subject": subject, "body_text": body_text, "body_html": body_html}

        subject = "A quick idea for you"
        body_text = (
            f"Hi {recipient_name},\n\n"
            "I wanted to share a quick idea that could help your team. "
            "If you're open to it, I'm happy to send a short summary.\n"
        )
        body_html = (
            f"<p>Hi {recipient_name},</p>"
            "<p>I wanted to share a quick idea that could help your team. "
            "If you're open to it, I'm happy to send a short summary.</p>"
        )
        content = {"subject": subject, "body_text": body_text, "body_html": body_html}
        return self._add_offer_footer(content, company_name, company_address, unsub_url)

    def _add_offer_footer(
        self,
        content: Dict[str, str],
        company_name: str,
        company_address: str,
        unsub_url: Optional[str],
    ) -> Dict[str, str]:
        unsubscribe_line = (
            f"To unsubscribe, visit {unsub_url}."
            if unsub_url
            else "To unsubscribe, reply with 'unsubscribe'."
        )
        footer_text = (
            f"\n\n--\n{company_name}\n{company_address}\n{unsubscribe_line}\n"
        )
        footer_html = (
            "<hr>"
            f"<p><strong>{company_name}</strong><br>"
            f"{company_address}<br>{unsubscribe_line}</p>"
        )
        content["body_text"] = content["body_text"].rstrip() + footer_text
        content["body_html"] = content["body_html"].rstrip() + footer_html
        return content

    def _is_retryable_openai_error(self, exc: Exception) -> bool:
        status = getattr(exc, "status_code", None)
        if status is None:
            status = getattr(exc, "http_status", None)
        if status is None:
            return False
        return status in {429, 500, 502, 503, 504}
