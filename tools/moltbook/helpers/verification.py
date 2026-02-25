from __future__ import annotations

import json

from src.utils.llm.streaming import StreamingLLM
from src.utils.log import log

VERIFICATION_MODEL = "meta-llama/llama-3.2-3b-instruct"
VERIFICATION_MAX_TOKENS = 32


def find_verification_obj(data: object) -> dict | None:
    """Recursively search *data* for a 'verification' key whose value is a dict
    containing at least 'verification_code' and 'challenge_text'.

    Returns the first matching verification dict, or None if not found.
    """
    if isinstance(data, dict):
        candidate = data.get("verification")
        if (
            isinstance(candidate, dict)
            and "verification_code" in candidate
            and "challenge_text" in candidate
        ):
            return candidate
        for value in data.values():
            result = find_verification_obj(value)
            if result is not None:
                return result
    elif isinstance(data, list):
        for item in data:
            result = find_verification_obj(item)
            if result is not None:
                return result
    return None


def solve_challenge(llm: StreamingLLM, challenge_text: str) -> str:
    """Ask the LLM to decode and solve the obfuscated math problem.

    Always overrides the model to VERIFICATION_MODEL and caps output at
    VERIFICATION_MAX_TOKENS so a lightweight, non-reasoning model is used.
    """
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
    result = llm.fetch(
        messages,
        max_tokens=VERIFICATION_MAX_TOKENS,
        parameters={"model": VERIFICATION_MODEL},
    )
    log(f"""
Messages:

{json.dumps(messages, indent=2)}


Result:
{result}

""")
    if not result.content.strip():
        raise ValueError("Empty answer from LLM for math challenge.")
    return result.content.strip()
