from __future__ import annotations

from typing import Dict, Tuple


def handle_unsubscribe(request_method: str, request_body: bytes, headers: Dict[str, str]) -> Tuple[int, str]:
    """
    Minimal handler contract for one-click unsubscribe.

    Expected:
    - POST request to the List-Unsubscribe URL
    - The body may include "List-Unsubscribe=One-Click"
    - You should mark the recipient as unsubscribed in your system
    """
    if request_method.upper() != "POST":
        return 405, "Method Not Allowed"
    # TODO: Parse request_body for recipient identifier and store suppression.
    return 200, "Unsubscribed"
