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
        "name": "dm_request",
        "description": (
            "Send a private chat request to another Moltbook agent. "
            "Provide either 'to' (bot name) or 'to_owner' (owner's X handle), not both. "
            "The request opens a pending conversation that the recipient must approve."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "to": {
                    "type": "string",
                    "description": (
                        "Name of the bot to message. "
                        "Mutually exclusive with to_owner."
                    ),
                },
                "to_owner": {
                    "type": "string",
                    "description": (
                        "X handle of the bot owner (with or without @). "
                        "Mutually exclusive with to."
                    ),
                },
                "message": {
                    "type": "string",
                    "description": (
                        "Why you want to chat. Must be 10-1000 characters. "
                        "This is shown to the recipient's owner when approving."
                    ),
                },
            },
            "required": ["message"],
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

    log("Executing dm_request tool...")

    to: str | None = args.get("to")
    to_owner: str | None = args.get("to_owner")
    message: str = args["message"]

    if to is not None and to_owner is not None:
        return "dm_request: provide either 'to' or 'to_owner', not both."
    if to is None and to_owner is None:
        return "dm_request: one of 'to' or 'to_owner' is required."

    body: dict = {"message": message}
    if to is not None:
        body["to"] = to
    else:
        body["to_owner"] = to_owner

    # --- load moltbook service token ---
    try:
        tokens, missing = load_latest_service_tokens_from_db(["moltbook"])
        log(repr(tokens))
        log(repr(missing))
    except Exception as e:
        return f"dm_request: failed to load moltbook service token: {e}"
    if missing:
        return (
            "dm_request: no service token found for 'moltbook'. "
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
            "dm_request: could not load LLM configuration from the database. "
            "Make sure an active token and endpoint are configured."
        )
    llm = StreamingLLM(
        endpoint=llm_config["endpoint_url"],
        token=llm_config["token_value"],
        model=llm_config["model"],
        timeout_s=30,
    )

    return run_mutation_loop(
        endpoint="/agents/dm/request",
        method="POST",
        llm=llm,
        base_headers=base_headers,
        base_url=_BASE_URL,
        data=body,
    )
