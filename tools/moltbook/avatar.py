from __future__ import annotations

import mimetypes
import os

import httpx

from src.utils.http.helpers import (
    apply_service_tokens_to_headers,
    load_latest_service_tokens_from_db,
)
from src.utils.log import log

DEFINITION: dict = {
    "type": "function",
    "function": {
        "name": "avatar",
        "description": (
            "Upload or remove your Moltbook avatar. "
            "Supported upload formats: JPEG, PNG, GIF, WebP. Max size: 1 MB."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["upload", "remove"],
                    "description": "Whether to upload a new avatar or remove the current one.",
                },
                "filepath": {
                    "type": "string",
                    "description": (
                        "Path to the image file to upload. "
                        "Required when action is 'upload'."
                    ),
                },
            },
            "required": ["action"],
            "additionalProperties": False,
        },
    },
}

_BASE_URL = "https://www.moltbook.com/api/v1"
_MAX_BYTES = 1 * 1024 * 1024  # 1 MB
_ALLOWED_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}


def needs_approval(args: dict) -> bool:
    from src.tools._approval import needs_path_approval
    return needs_path_approval(args.get("filepath"))


def execute(args: dict, session_data: dict) -> str:

    log("Executing avatar tool...")

    action: str = args["action"]
    filepath: str | None = args.get("filepath")

    if action == "upload" and not filepath:
        return "avatar: 'filepath' is required when action is 'upload'."
    if action == "remove" and filepath is not None:
        return "avatar: 'filepath' should not be provided when action is 'remove'."

    # --- load moltbook service token ---
    try:
        tokens, missing = load_latest_service_tokens_from_db(["moltbook"])
        log(repr(tokens))
        log(repr(missing))
    except Exception as e:
        return f"avatar: failed to load moltbook service token: {e}"
    if missing:
        return (
            "avatar: no service token found for 'moltbook'. "
            "Create one with: slbp service-token set moltbook <token>"
        )

    base_headers: dict[str, str] = {"Accept": "application/json"}
    base_headers = apply_service_tokens_to_headers(base_headers, tokens)
    if not any(k.lower() == "authorization" for k in base_headers):
        base_headers["Authorization"] = f"Bearer {next(iter(tokens.values()))}"

    if action == "remove":
        return _remove_avatar(base_headers)

    return _upload_avatar(filepath, base_headers)


def _upload_avatar(filepath: str, base_headers: dict) -> str:
    # Validate format by MIME type.
    content_type, _ = mimetypes.guess_type(filepath)
    if content_type not in _ALLOWED_TYPES:
        return (
            f"avatar: unsupported file format ({content_type!r}). "
            "Use JPEG, PNG, GIF, or WebP."
        )

    # Validate size before reading.
    try:
        size = os.path.getsize(filepath)
    except OSError as e:
        return f"avatar: could not stat file: {e}"
    if size > _MAX_BYTES:
        return f"avatar: file is {size} bytes, exceeds the 1 MB limit."

    # Read file.
    try:
        with open(filepath, "rb") as f:
            file_bytes = f.read()
    except OSError as e:
        return f"avatar: could not read file: {e}"

    filename = os.path.basename(filepath)

    # Multipart upload — do NOT include Content-Type: application/json;
    # httpx sets the correct multipart boundary header automatically.
    upload_headers = {k: v for k, v in base_headers.items() if k.lower() != "content-type"}

    try:
        resp = httpx.post(
            f"{_BASE_URL}/agents/me/avatar",
            files={"file": (filename, file_bytes, content_type)},
            headers=upload_headers,
            timeout=30,
            follow_redirects=True,
        )
    except Exception as e:
        return f"avatar: HTTP error during upload: {e}"

    # curl -f equivalent: treat HTTP >= 400 as an error.
    if resp.status_code >= 400:
        return f"avatar: upload failed (HTTP {resp.status_code}): {resp.text[:400]}"

    try:
        data = resp.json()
    except Exception:
        return f"avatar: upload succeeded (HTTP {resp.status_code}) but response was not JSON."

    if not data.get("success"):
        return f"avatar: upload failed: {data}"

    return f"avatar: avatar uploaded successfully (HTTP {resp.status_code})."


def _remove_avatar(base_headers: dict) -> str:
    # DELETE with Content-Type: application/json is fine for a bodyless request.
    headers = {**base_headers, "Content-Type": "application/json"}

    try:
        resp = httpx.delete(
            f"{_BASE_URL}/agents/me/avatar",
            headers=headers,
            timeout=20,
            follow_redirects=True,
        )
    except Exception as e:
        return f"avatar: HTTP error during remove: {e}"

    # curl -f equivalent: treat HTTP >= 400 as an error.
    if resp.status_code >= 400:
        return f"avatar: remove failed (HTTP {resp.status_code}): {resp.text[:400]}"

    try:
        data = resp.json()
    except Exception:
        return f"avatar: remove succeeded (HTTP {resp.status_code}) but response was not JSON."

    if not data.get("success"):
        return f"avatar: remove failed: {data}"

    return "avatar: avatar removed successfully."
