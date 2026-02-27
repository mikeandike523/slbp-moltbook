from __future__ import annotations

import os

from src.utils.log import log

# Paths are resolved relative to this file: tools/moltbook/ -> ../../skill-files/
_SKILL_FILES_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "skill-files")

# Ordered list of (session_memory_key, filename) pairs.
# Keys are reserved — the agent must not use them for any other purpose.
_SKILL_FILES: list[tuple[str, str]] = [
    ("moltbook_skill_home",       "home.txt"),
    ("moltbook_skill_posts",      "posts.txt"),
    ("moltbook_skill_comments",   "comments.txt"),
    ("moltbook_skill_voting",     "voting.txt"),
    ("moltbook_skill_following",  "following.txt"),
    ("moltbook_skill_submolts",   "submolts.txt"),
    ("moltbook_skill_moderation", "moderation.txt"),
    ("moltbook_skill_profile",    "profile.txt"),
    ("moltbook_skill_feed",       "feed.txt"),
    ("moltbook_skill_search",     "search.txt"),
    ("moltbook_skill_dm",         "dm.txt"),
    ("moltbook_skill_heartbeat",  "heartbeat.txt"),
    ("moltbook_skill_rules",      "rules.txt"),
]

DEFINITION: dict = {
    "type": "function",
    "function": {
        "name": "load_skill_files",
        "description": (
            "Idempotently load the Moltbook skill documentation files into session memory. "
            "Call this before every user request. Safe to call multiple times — it always "
            "force-overwrites the reserved skill keys. After loading, use "
            "session_memory_search_by_regex to search the skill files for API details."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    },
}


def execute(args: dict, session_data: dict) -> str:

    log("Executing load_skill_files tool...")

    memory: dict = session_data.setdefault("memory", {})
    loaded: list[str] = []
    errors: list[str] = []

    for key, filename in _SKILL_FILES:
        path = os.path.normpath(os.path.join(_SKILL_FILES_DIR, filename))
        try:
            with open(path, "r", encoding="utf-8") as f:
                memory[key] = f.read()
            loaded.append(key)
        except OSError as e:
            errors.append(f"{filename}: {e}")

    if errors:
        return (
            f"load_skill_files: loaded {len(loaded)} file(s), "
            f"but failed to load {len(errors)} file(s): {'; '.join(errors)}"
        )

    keys_list = ", ".join(loaded)
    return (
        f"load_skill_files: {len(loaded)} skill files loaded into session memory. "
        f"Keys: {keys_list}. "
        f"Use session_memory_search_by_regex to search them."
    )
