from __future__ import annotations

from src.data import get_pool
from src.tools._memory import ensure_session_memory
from src.utils.http.helpers import (
    apply_service_tokens_to_headers,
    load_latest_service_tokens_from_db,
)
from src.utils.llm.streaming import StreamingLLM
from src.utils.sql.kv_manager import KVManager
from src.utils.log import log
from tools.moltbook.helpers.mutation_loop import run_mutation_loop

LEAVE_OUT = "KEEP"

DEFINITION: dict = {
    "type": "function",
    "function": {
        "name": "create_post",
        "description": (
            "Create a post on Moltbook. Supports regular posts (with body content) "
            "and link posts (a single URL). Handles the AI verification challenge "
            "automatically (up to 5 attempts)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "submolt_name": {
                    "type": "string",
                    "description": "The submolt (community) to post in, e.g. 'general'.",
                },
                "title": {
                    "type": "string",
                    "description": "Title of the post.",
                },
                "content": {
                    "type": "string",
                    "description": (
                        "Body content of the post. "
                        "Mutually exclusive with session_memory_key and link_post_url."
                    ),
                },
                "session_memory_key": {
                    "type": "string",
                    "description": (
                        "Session memory key whose value is used as the post body. "
                        "Mutually exclusive with content and link_post_url."
                    ),
                },
                "link_post_url": {
                    "type": "string",
                    "description": (
                        "URL for a link post. When provided the post is treated as a "
                        "link post by Moltbook. Mutually exclusive with content and "
                        "session_memory_key — do not provide either when using this."
                    ),
                },
            },
            "required": ["submolt_name", "title"],
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

    log("Executing create_post tool...")

    submolt_name: str = args["submolt_name"]
    title: str = args["title"]

    # --- resolve post type (mutually exclusive sources) ---
    content_direct: str | None = args.get("content")
    memory_key: str | None = args.get("session_memory_key")
    link_post_url: str | None = args.get("link_post_url")

    if link_post_url is not None:
        if content_direct is not None or memory_key is not None:
            return (
                "create_post: 'link_post_url' is mutually exclusive with "
                "'content' and 'session_memory_key'."
            )
        post_data = {"submolt_name": submolt_name, "title": title, "url": link_post_url}
    else:
        if content_direct is not None and memory_key is not None:
            return "create_post: provide either 'content' or 'session_memory_key', not both."
        if content_direct is None and memory_key is None:
            return (
                "create_post: one of 'content', 'session_memory_key', or "
                "'link_post_url' is required."
            )

        if memory_key is not None:
            memory = ensure_session_memory(session_data)
            value = memory.get(memory_key)
            if value is None:
                return f"create_post: session memory key {memory_key!r} not found."
            if not isinstance(value, str):
                return (
                    f"create_post: session memory key {memory_key!r} does not hold a text "
                    f"value (got {type(value).__name__})."
                )
            content = value
        else:
            content = content_direct

        post_data = {"submolt_name": submolt_name, "title": title, "content": content}

    # --- load moltbook service token ---
    try:
        tokens, missing = load_latest_service_tokens_from_db(["moltbook"])
        log(repr(tokens))
        log(repr(missing))
    except Exception as e:
        return f"create_post: failed to load moltbook service token: {e}"
    if missing:
        return (
            "create_post: no service token found for 'moltbook'. "
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
            "create_post: could not load LLM configuration from the database. "
            "Make sure an active token and endpoint are configured."
        )
    llm = StreamingLLM(
        endpoint=llm_config["endpoint_url"],
        token=llm_config["token_value"],
        model=llm_config["model"],
        timeout_s=30,
    )

    return run_mutation_loop(
        endpoint="/posts",
        method="POST",
        llm=llm,
        base_headers=base_headers,
        base_url=_BASE_URL,
        data=post_data,
    )
