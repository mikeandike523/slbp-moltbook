from __future__ import annotations

import json

import httpx

from src.utils.llm.streaming import StreamingLLM
from src.utils.log import log
from tools.moltbook.helpers.verification import find_verification_obj, solve_challenge

MAX_VERIFY_ATTEMPTS = 5

# The Moltbook API returns 409 (already used), 410 (expired), or 404
# (invalid) for the verification code in various error scenarios — all of
# which require re-creating the post to get a fresh code.
# It is unclear whether a *wrong answer* also permanently consumes the code.
# Set this to True if retrying /verify with the same code after a wrong
# answer keeps returning 409.
REPOST_ON_WRONG_ANSWER = False


def run_post_loop(
    submolt_name: str,
    title: str,
    content: str,
    llm: StreamingLLM,
    base_headers: dict,
    base_url: str,
) -> str:
    """Create a post and handle the verification challenge loop.

    Attempts up to MAX_VERIFY_ATTEMPTS times.  404 / 409 / 410 responses from
    /verify all trigger a full repost to obtain a fresh verification code.
    Whether a wrong answer also requires a repost is controlled by the
    REPOST_ON_WRONG_ANSWER flag at the top of this module.

    Returns a human-readable result string in all cases.
    """
    verification_code: str | None = None
    challenge_text = ""
    needs_repost = True
    attempts = 0
    answer = ""
    hint = ""

    while attempts < MAX_VERIFY_ATTEMPTS:

        if needs_repost:
            post_body = {
                "submolt_name": submolt_name,
                "title": title,
                "content": content,
            }
            try:
                resp = httpx.post(
                    f"{base_url}/posts",
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

            verification_obj = find_verification_obj(data)
            if not verification_obj:
                # No verification required — post published immediately.
                post_id = data.get("post", {}).get("id", "unknown")
                return f"create_post: post published successfully (id: {post_id})."

            verification_code = verification_obj["verification_code"]
            challenge_text = verification_obj["challenge_text"]
            log(repr((verification_code, challenge_text)))
            needs_repost = False

        # Solve and verify.
        attempts += 1
        answer = solve_challenge(llm, challenge_text)

        try:
            verify_resp = httpx.post(
                f"{base_url}/verify",
                json={"verification_code": verification_code, "answer": answer},
                headers=base_headers,
                timeout=20,
                follow_redirects=True,
            )
        except Exception as e:
            return f"create_post: HTTP error while verifying: {e}"

        if verify_resp.status_code == 410:
            # Verification code expired — re-create the post.
            needs_repost = True
            verification_code = None
            continue

        try:
            verify_data = verify_resp.json()
            log(f"""
Verification Response:
                
{json.dumps(verify_data,indent=2)}
""".strip())
        except Exception:
            return (
                f"create_post: non-JSON verification response "
                f"(status {verify_resp.status_code}): {verify_resp.text[:400]}"
            )

        if verify_resp.status_code == 404:
            # Invalid verification code — re-create the post to get a fresh one.
            needs_repost = True
            verification_code = None
            continue

        if verify_resp.status_code == 409:
            # Code already consumed — re-create the post to get a fresh one.
            needs_repost = True
            verification_code = None
            continue

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

        # Incorrect answer — success=false in the response body (may be a 200
        # or a 4xx; the example shape is {success: false, error: "Incorrect
        # answer", hint: "...", content_id: "..."}).
        hint = verify_data.get("hint", "")
        if REPOST_ON_WRONG_ANSWER:
            needs_repost = True
            verification_code = None

    return (
        f"create_post: exhausted {MAX_VERIFY_ATTEMPTS} verification attempts "
        f"without success. Last answer tried: {answer!r}. "
        f"Hint from server: {hint!r}. Could not publish the post."
    )
