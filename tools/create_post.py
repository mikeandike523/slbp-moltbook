from __future__ import annotations

import json

import httpx

from src.data import get_pool
from src.utils.http.helpers import (
    apply_service_tokens_to_headers,
    load_latest_service_tokens_from_db,
)
from src.utils.llm.streaming import StreamingLLM
from src.utils.sql.kv_manager import KVManager
from src.utils.log import log

LEAVE_OUT = "KEEP"

DEFINITION: dict = {
    "type": "function",
    "function": {
        "name": "create_post",
        "description": (
            "Create a post on Moltbook. Handles the AI verification challenge "
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
                    "description": "Body content of the post.",
                },
            },
            "required": ["submolt_name", "title", "content"],
            "additionalProperties": False,
        },
    },
}


_BASE_URL = "https://www.moltbook.com/api/v1"
_MAX_VERIFY_ATTEMPTS = 5


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


def _solve_challenge(llm: StreamingLLM, challenge_text: str) -> str:
    """Ask the LLM to decode and solve the obfuscated math problem."""
    prompt = (
        "Decode and solve the following math problem. "
        "The text has random capitalisation and punctuation noise "
        "(characters like ], [, ^, /, - scattered through the words). "
        "Strip all that noise, read the plain English sentence, solve it, "
        "and reply with ONLY the numeric answer formatted to exactly 2 decimal "
        "places (e.g. '15.00'). No explanation, no other text.\n\n"
        f"Problem: {challenge_text.lower()}"
    )
    messages = [{"role": "user", "content": prompt}]
     # Deal with reasoning tokens and hope that it makes it to final answer in time
    result = llm.fetch(messages, max_tokens=16384)
    if not result.content.strip():
        raise ValueError("Empty answer from llm for math challenge." \
        "Model likely did not finish reasoning stage and get to final answer." \
        "consider using a non-thinking model")
    log(f"""
Messages:
        
{json.dumps(messages, indent=2)}


Result:
{result}

""")
    return result.content.strip()


def execute(args: dict, session_data: dict) -> str:

    log(f"Executing create_post tool...")

    submolt_name: str = args["submolt_name"]
    title: str = args["title"]
    content: str = args["content"]

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

    # --- attempt loop ---
    verification_code: str | None = None
    challenge_text = ""
    needs_repost = True
    attempts = 0
    answer = ""
    hint = ""

    while attempts < _MAX_VERIFY_ATTEMPTS:

        if needs_repost:
            # (Re-)create the post
            post_body = {
                "submolt_name": submolt_name,
                "title": title,
                "content": content,
            }
            try:
                resp = httpx.post(
                    f"{_BASE_URL}/posts",
                    json=post_body,
                    headers=base_headers,
                    timeout=20,
                    follow_redirects=True,
                )
            except Exception as e:
                return f"create_post: HTTP error while creating post: {e}"

            try:
                data = resp.json()
            except Exception:
                return (
                    f"create_post: non-JSON response from /posts "
                    f"(status {resp.status_code}): {resp.text[:400]}"
                )

            if not data.get("success"):
                return (
                    f"create_post: post creation failed "
                    f"(status {resp.status_code}): {json.dumps(data)}"
                )

            verification_obj = (
                data.get("post", {}).get("verification")
                or data.get("verification")
            )
            if not verification_obj:
                # No verification required — post published immediately
                post_id = data.get("post", {}).get("id", "unknown")
                return f"create_post: post published successfully (id: {post_id})."

            verification_code = verification_obj.get("verification_code")
            challenge_text = verification_obj.get("challenge_text", "")

            log(repr((verification_code, challenge_text)))
            needs_repost = False

        # Solve and verify
        attempts += 1
        answer = _solve_challenge(llm, challenge_text)

        try:
            verify_resp = httpx.post(
                f"{_BASE_URL}/verify",
                json={"verification_code": verification_code, "answer": answer},
                headers=base_headers,
                timeout=20,
                follow_redirects=True,
            )
        except Exception as e:
            return f"create_post: HTTP error while verifying: {e}"

        if verify_resp.status_code == 410:
            # Verification code expired — re-create the post
            needs_repost = True
            verification_code = None
            continue

        try:
            verify_data = verify_resp.json()
        except Exception:
            return (
                f"create_post: non-JSON verification response "
                f"(status {verify_resp.status_code}): {verify_resp.text[:400]}"
            )

        if verify_resp.status_code == 404:
            return (
                f"create_post: verification failed — invalid verification code "
                f"(404). Cannot recover: {json.dumps(verify_data)}"
            )

        if verify_resp.status_code == 409:
            return (
                f"create_post: verification failed — code already used "
                f"(409). Cannot recover: {json.dumps(verify_data)}"
            )

        if verify_data.get("success"):
            post_id = (
                verify_data.get("post", {}).get("id")
                or verify_data.get("content_id")
                or "unknown"
            )
            return (
                f"create_post: post verified and published successfully "
                f"(id: {post_id}, answer used: {answer!r})."
            )

        # Incorrect answer — retry with same verification code
        hint = verify_data.get("hint", "")
        # challenge_text stays the same, loop continues

    return (
        f"create_post: exhausted {_MAX_VERIFY_ATTEMPTS} verification attempts "
        f"without success. Last answer tried: {answer!r}. "
        f"Hint from server: {hint!r}. Could not publish the post."
    )
