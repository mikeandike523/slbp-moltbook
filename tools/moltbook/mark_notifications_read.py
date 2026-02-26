from __future__ import annotations

from src.data import get_pool
from src.utils.http.helpers import (
    apply_service_tokens_to_headers,
    load_latest_service_tokens_from_db,
)
from src.utils.llm.streaming import StreamingLLM
from src.utils.sql.kv_manager import KVManager
from src.utils.log import log
from tools.moltbook.helpers.mutation_loop import run_mutation_loop

DEFINITION: dict = {
    "type": "function",
    "function": {
        "name": "mark_notifications_read",
        "description": (
            "Mark Moltbook notifications as read. "
            "Provide post_id to mark only that post's notifications as read, "
            "or omit it to mark all notifications as read at once."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "post_id": {
                    "type": "string",
                    "description": (
                        "ID of the post whose notifications to mark as read. "
                        "Omit to mark all notifications as read."
                    ),
                },
            },
            "required": [],
            "additionalProperties": False,
        },
    },
}


_BASE_URL = "https://www.moltbook.com/api/v1"


def _load_llm_config() -> dict | None:
    """Load just the fields needed to instantiate StreamingLLM."""
    try:
        pool = get_pool()
    except Exception:
        return None
    with pool.get_connection() as conn:
        kv = KVManager(conn)
        active_token = kv.get_value("active_token")
        if not active_token:
            return None
        provider = active_token["provider"]
        token_name = active_token.get("name", "")
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT token_value, endpoint_url
                FROM tokens
                WHERE BINARY provider = BINARY %s
                  AND BINARY token_name = BINARY %s
                LIMIT 1
                """,
                (provider, token_name),
            )
            row = cursor.fetchone()
        if not row:
            return None
        token_value, endpoint_url = row
        model = kv.get_value("model") or None
    return {"endpoint_url": endpoint_url, "token_value": token_value, "model": model}


def execute(args: dict, session_data: dict) -> str:

    log("Executing mark_notifications_read tool...")

    post_id: str | None = args.get("post_id")

    if post_id:
        endpoint = f"/notifications/read-by-post/{post_id}"
    else:
        endpoint = "/notifications/read-all"

    # --- load moltbook service token ---
    try:
        tokens, missing = load_latest_service_tokens_from_db(["moltbook"])
        log(repr(tokens))
        log(repr(missing))
    except Exception as e:
        return f"mark_notifications_read: failed to load moltbook service token: {e}"
    if missing:
        return (
            "mark_notifications_read: no service token found for 'moltbook'. "
            "Create one with: slbp service-token set moltbook <token>"
        )

    base_headers = {"Content-Type": "application/json", "Accept": "application/json"}
    base_headers = apply_service_tokens_to_headers(base_headers, tokens)
    if not any(k.lower() == "authorization" for k in base_headers):
        base_headers["Authorization"] = f"Bearer {next(iter(tokens.values()))}"

    # --- load LLM config ---
    llm_config = _load_llm_config()
    if not llm_config:
        return (
            "mark_notifications_read: could not load LLM configuration from the database. "
            "Make sure an active token and endpoint are configured."
        )
    llm = StreamingLLM(
        endpoint=llm_config["endpoint_url"],
        token=llm_config["token_value"],
        model=llm_config["model"],
        timeout_s=30,
    )

    return run_mutation_loop(
        endpoint=endpoint,
        method="POST",
        llm=llm,
        base_headers=base_headers,
        base_url=_BASE_URL,
        data=None,
    )
