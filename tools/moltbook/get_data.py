from __future__ import annotations
import json

import httpx

from src.utils.http.helpers import (
    apply_service_tokens_to_headers,
    format_response,
    is_json_content_type,
    load_latest_service_tokens_from_db,
)
from src.utils.log import log

DEFINITION: dict = {
    "type": "function",
    "function": {
        "name": "get_data",
        "description": (
            "Make an authenticated GET request to the Moltbook API. "
            "Handles auth automatically using the stored moltbook service token."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path relative to the API base, e.g. '/posts' or 'posts/123'.",
                },
                "target": {
                    "type": "string",
                    "enum": ["return_value", "session_memory"],
                    "default": "return_value",
                    "description": (
                        "What to do with the response. 'return_value' (default) returns it "
                        "directly. 'session_memory' saves it to session memory under "
                        "'session_memory_key' and returns a confirmation."
                    ),
                },
                "session_memory_key": {
                    "type": "string",
                    "description": (
                        "Key under which to save the response in session memory. "
                        "Required when target is 'session_memory'."
                    ),
                },
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    },
}

_BASE_URL = "https://www.moltbook.com/api/v1"


def execute(args: dict, session_data: dict) -> str:
    path: str = args["path"]
    target: str = args.get("target", "return_value")
    session_memory_key: str | None = args.get("session_memory_key")

    if target == "session_memory" and not session_memory_key:
        return "get_data: 'session_memory_key' is required when target is 'session_memory'."

    log(f"Executing get_data tool: GET {path}")

    # Build full URL — normalize slashes.
    url = _BASE_URL.rstrip("/") + "/" + path.lstrip("/")

    # Load moltbook service token.
    try:
        tokens, missing = load_latest_service_tokens_from_db(["moltbook"])
    except Exception as e:
        return f"get_data: failed to load moltbook service token: {e}"
    if missing:
        return (
            "get_data: no service token found for 'moltbook'. "
            "Create one with: slbp service-token set moltbook <token>"
        )

    # Build headers.
    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    headers = apply_service_tokens_to_headers(headers, tokens)
    if not any(k.lower() == "authorization" for k in headers):
        headers["Authorization"] = f"Bearer {next(iter(tokens.values()))}"

    # Make request.
    try:
        resp = httpx.Client(follow_redirects=True, timeout=20).get(url, headers=headers)
    except Exception as e:
        return f"get_data: HTTP error during GET {path}: {e}"

    resp_ct = resp.headers.get("content-type")
    accept = "application/json"

    # Parse JSON response.
    json_value = None
    json_error = None
    if is_json_content_type(resp_ct):
        try:
            json_value = resp.json()
        except Exception as e:
            json_error = f"Failed to parse JSON: {e}"

    if target == "session_memory":
        memory = session_data.setdefault("memory", {})
        memory[session_memory_key] = json.dumps(json_value,indent=2) if json_value is not None else resp.text
        return f"get_data: response saved to session memory key {session_memory_key!r}."

    return format_response(
        status_code=resp.status_code,
        response_content_type=resp_ct,
        accept=accept,
        json_value=json_value,
        text_value=resp.text if json_value is None else None,
        json_error=json_error,
    )
