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
        "name": "submolt_image",
        "description": (
            "Upload an avatar or banner image for a submolt you own or moderate. "
            "Supported formats: JPEG, PNG, GIF, WebP. "
            "Max size: 500 KB for avatar, 2 MB for banner."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "submolt_name": {
                    "type": "string",
                    "description": "The URL-safe name of the submolt.",
                },
                "image_type": {
                    "type": "string",
                    "enum": ["avatar", "banner"],
                    "description": "Whether to upload the submolt avatar or banner.",
                },
                "filepath": {
                    "type": "string",
                    "description": "Path to the image file to upload.",
                },
            },
            "required": ["submolt_name", "image_type", "filepath"],
            "additionalProperties": False,
        },
    },
}

_BASE_URL = "https://www.moltbook.com/api/v1"
_ALLOWED_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
_MAX_BYTES = {
    "avatar": 500 * 1024,       # 500 KB
    "banner": 2 * 1024 * 1024,  # 2 MB
}


def needs_approval(args: dict) -> bool:
    from src.tools._approval import needs_path_approval
    return needs_path_approval(args.get("filepath"))


def execute(args: dict, session_data: dict) -> str:

    log("Executing submolt_image tool...")

    submolt_name: str = args["submolt_name"]
    image_type: str = args["image_type"]
    filepath: str = args["filepath"]

    # Validate format.
    content_type, _ = mimetypes.guess_type(filepath)
    if content_type not in _ALLOWED_TYPES:
        return (
            f"submolt_image: unsupported file format ({content_type!r}). "
            "Use JPEG, PNG, GIF, or WebP."
        )

    # Validate size.
    try:
        size = os.path.getsize(filepath)
    except OSError as e:
        return f"submolt_image: could not stat file: {e}"
    max_bytes = _MAX_BYTES[image_type]
    if size > max_bytes:
        limit_label = "500 KB" if image_type == "avatar" else "2 MB"
        return f"submolt_image: file is {size} bytes, exceeds the {limit_label} limit for {image_type}."

    # Read file.
    try:
        with open(filepath, "rb") as f:
            file_bytes = f.read()
    except OSError as e:
        return f"submolt_image: could not read file: {e}"

    filename = os.path.basename(filepath)

    # --- load moltbook service token ---
    try:
        tokens, missing = load_latest_service_tokens_from_db(["moltbook"])
        log(repr(tokens))
        log(repr(missing))
    except Exception as e:
        return f"submolt_image: failed to load moltbook service token: {e}"
    if missing:
        return (
            "submolt_image: no service token found for 'moltbook'. "
            "Create one with: slbp service-token set moltbook <token>"
        )

    # Multipart upload — omit Content-Type so httpx sets the multipart boundary.
    headers: dict[str, str] = {"Accept": "application/json"}
    headers = apply_service_tokens_to_headers(headers, tokens)
    if not any(k.lower() == "authorization" for k in headers):
        headers["Authorization"] = f"Bearer {next(iter(tokens.values()))}"

    url = f"{_BASE_URL}/submolts/{submolt_name}/{image_type}"

    try:
        resp = httpx.post(
            url,
            files={"file": (filename, file_bytes, content_type)},
            headers=headers,
            timeout=30,
            follow_redirects=True,
        )
    except Exception as e:
        return f"submolt_image: HTTP error during upload: {e}"

    # curl -f equivalent: treat HTTP >= 400 as an error.
    if resp.status_code >= 400:
        return f"submolt_image: upload failed (HTTP {resp.status_code}): {resp.text[:400]}"

    try:
        data = resp.json()
    except Exception:
        return f"submolt_image: upload succeeded (HTTP {resp.status_code}) but response was not JSON."

    if not data.get("success"):
        return f"submolt_image: upload failed: {data}"

    return f"submolt_image: {image_type} uploaded successfully for '{submolt_name}'."
