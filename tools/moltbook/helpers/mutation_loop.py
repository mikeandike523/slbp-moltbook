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


def run_mutation_loop(
    endpoint: str,
    method: str,
    llm: StreamingLLM,
    base_headers: dict,
    base_url: str,
    data: dict | None = None,
) -> str:
    """Submit a mutation and handle the verification challenge loop.

    Makes a request to ``{base_url}{endpoint}`` using ``method``. ``data``,
    if provided, is sent as the JSON body; omit it for bodyless requests such
    as DELETE.  Retries the verification challenge up to MAX_VERIFY_ATTEMPTS
    times.  404 / 409 / 410 responses from /verify all trigger a full
    re-submission to obtain a fresh verification code.  Whether a wrong answer
    also requires a re-submission is controlled by the REPOST_ON_WRONG_ANSWER
    flag at the top of this module.

    Returns a human-readable result string in all cases.
    """
    verification_code: str | None = None
    challenge_text = ""
    needs_resubmit = True
    attempts = 0
    answer = ""
    hint = ""
    url = f"{base_url}{endpoint}"

    while attempts < MAX_VERIFY_ATTEMPTS:

        if needs_resubmit:
            try:
                kwargs = {"headers": base_headers, "timeout": 20, "follow_redirects": True}
                if data is not None:
                    kwargs["json"] = data
                resp = httpx.request(method, url, **kwargs)
            except Exception as e:
                return f"mutation_loop: HTTP error during {method} {endpoint}: {e}"

            try:
                resp_data = resp.json()
            except Exception:
                return (
                    f"mutation_loop: non-JSON response from {endpoint} "
                    f"(status {resp.status_code}): {resp.text[:400]}"
                )

            if not resp_data.get("success"):
                return (
                    f"mutation_loop: request failed "
                    f"(status {resp.status_code}): {json.dumps(resp_data)}"
                )

            verification_obj = find_verification_obj(resp_data)
            if not verification_obj:
                # No verification required — request completed immediately.
                post_id = resp_data.get("post", {}).get("id", "unknown")
                return f"mutation_loop: request succeeded (id: {post_id})."

            verification_code = verification_obj["verification_code"]
            challenge_text = verification_obj["challenge_text"]
            log(repr((verification_code, challenge_text)))
            needs_resubmit = False

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
            return f"mutation_loop: HTTP error while verifying: {e}"

        if verify_resp.status_code == 410:
            # Verification code expired — re-submit.
            needs_resubmit = True
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
                f"mutation_loop: non-JSON verification response "
                f"(status {verify_resp.status_code}): {verify_resp.text[:400]}"
            )

        if verify_resp.status_code == 404:
            # Invalid verification code — re-submit to get a fresh one.
            needs_resubmit = True
            verification_code = None
            continue

        if verify_resp.status_code == 409:
            # Code already consumed — re-submit to get a fresh one.
            needs_resubmit = True
            verification_code = None
            continue

        if verify_data.get("success"):
            post_id = (
                verify_data.get("post", {}).get("id")
                or verify_data.get("content_id")
                or "unknown"
            )
            return (
                f"mutation_loop: verified and published successfully "
                f"(id: {post_id}, answer used: {answer!r})."
            )

        # Incorrect answer — success=false in the response body (may be a 200
        # or a 4xx; the example shape is {success: false, error: "Incorrect
        # answer", hint: "...", content_id: "..."}).
        hint = verify_data.get("hint", "")
        if REPOST_ON_WRONG_ANSWER:
            needs_resubmit = True
            verification_code = None

    return (
        f"mutation_loop: exhausted {MAX_VERIFY_ATTEMPTS} verification attempts "
        f"without success. Last answer tried: {answer!r}. "
        f"Hint from server: {hint!r}. Could not complete the request."
    )
