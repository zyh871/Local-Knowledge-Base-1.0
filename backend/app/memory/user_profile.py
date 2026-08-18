"""
SQLite-backed long-term user profile store.

Persists cross-session facts about each user (name, profession, preferences,
language, etc.) so the assistant can "know the user" over time.

Schema:
    user_profile (
        username     TEXT PRIMARY KEY,
        display_name TEXT,
        profession   TEXT,
        language     TEXT,        -- preferred reply language, e.g. 'zh', 'en'
        facts        TEXT,        -- JSON list of {fact, last_mentioned, weight}
        preferences  TEXT,        -- JSON dict of preference key->value
        created_at   TEXT,
        updated_at   TEXT
    )

Features:
    - Fact deduplication by content hash
    - Timestamp tracking for last mentioned date
    - Weight decay for infrequently mentioned facts
    - Automatic cleanup of low-weight facts
"""

import json
import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data")
os.makedirs(DB_DIR, exist_ok=True)
DB_PATH = os.path.join(DB_DIR, "user_profile.db")

_lock = threading.RLock()
_conn: Optional[sqlite3.Connection] = None

DECAY_DAYS = 30
MIN_WEIGHT = 0.1
MAX_FACTS = 50


def _connect() -> sqlite3.Connection:
    """Return a single long-lived shared connection (protected by _lock)."""
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
        _conn.row_factory = sqlite3.Row
    return _conn


def _init_schema() -> None:
    with _lock, _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_profile (
                username      TEXT PRIMARY KEY,
                display_name  TEXT DEFAULT '',
                profession    TEXT DEFAULT '',
                language      TEXT DEFAULT 'auto',
                facts         TEXT DEFAULT '[]',
                preferences   TEXT DEFAULT '{}',
                created_at    TEXT,
                updated_at    TEXT
            )
            """
        )
        conn.commit()


_init_schema()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _now_timestamp() -> float:
    return datetime.now(timezone.utc).timestamp()


def _compute_weight(last_mentioned_timestamp: float, mentions: int = 1) -> float:
    """Compute fact weight based on recency and mention count.
    
    Weight decays exponentially over time, with a boost for multiple mentions.
    """
    now = _now_timestamp()
    days_since = (now - last_mentioned_timestamp) / (24 * 3600)
    decay_factor = max(0.01, 1 - (days_since / DECAY_DAYS))
    mention_boost = min(3.0, 1 + mentions * 0.2)
    return round(max(MIN_WEIGHT, decay_factor * mention_boost), 4)


def _row_to_dict(row: Optional[sqlite3.Row]) -> Dict[str, Any]:
    if row is None:
        return {}
    d = dict(row)
    for key in ("facts", "preferences"):
        raw = d.get(key)
        if not raw:
            d[key] = [] if key == "facts" else {}
            continue
        try:
            d[key] = json.loads(raw)
        except (TypeError, ValueError):
            d[key] = [] if key == "facts" else {}
    return d


def get_profile(username: str) -> Dict[str, Any]:
    """Return the profile for `username`, creating an empty one if needed."""
    if not username:
        return {}
    with _lock, _connect() as conn:
        row = conn.execute("SELECT * FROM user_profile WHERE username = ?", (username,)).fetchone()
    if row is None:
        return {
            "username": username,
            "display_name": "",
            "profession": "",
            "language": "auto",
            "facts": [],
            "preferences": {},
            "created_at": _now(),
            "updated_at": _now(),
        }
    return _row_to_dict(row)


def update_profile(username: str, fields: Dict[str, Any]) -> Dict[str, Any]:
    """
    Merge-update the profile. Allowed scalar fields: display_name, profession, language.
    `facts` is a list — existing facts are merged with deduplication and weight decay.
    `preferences` is a dict — merged.
    """
    if not username or not isinstance(fields, dict):
        return get_profile(username)

    current = get_profile(username)

    merged_facts: List[Dict[str, Any]] = []
    seen_hashes = set()

    for f in current.get("facts") or []:
        if isinstance(f, dict) and f.get("fact"):
            fact_hash = hash(f["fact"].strip().lower())
            if fact_hash not in seen_hashes:
                seen_hashes.add(fact_hash)
                weight = _compute_weight(f.get("last_mentioned", _now_timestamp()), f.get("mentions", 1))
                if weight >= MIN_WEIGHT:
                    merged_facts.append({
                        "fact": f["fact"],
                        "last_mentioned": f.get("last_mentioned", _now_timestamp()),
                        "mentions": f.get("mentions", 1),
                        "weight": weight,
                    })

    for new_fact in fields.get("facts") or []:
        if isinstance(new_fact, str) and new_fact.strip():
            fact_text = new_fact.strip()
            fact_hash = hash(fact_text.lower())
            if fact_hash not in seen_hashes:
                seen_hashes.add(fact_hash)
                merged_facts.append({
                    "fact": fact_text,
                    "last_mentioned": _now_timestamp(),
                    "mentions": 1,
                    "weight": 1.0,
                })
            else:
                for existing in merged_facts:
                    if hash(existing["fact"].strip().lower()) == fact_hash:
                        existing["last_mentioned"] = _now_timestamp()
                        existing["mentions"] = existing.get("mentions", 1) + 1
                        existing["weight"] = _compute_weight(existing["last_mentioned"], existing["mentions"])
                        break

    merged_facts.sort(key=lambda x: x.get("weight", 0), reverse=True)
    merged_facts = merged_facts[:MAX_FACTS]

    merged_prefs: Dict[str, Any] = dict(current.get("preferences") or {})
    for k, v in (fields.get("preferences") or {}).items():
        merged_prefs[str(k)] = v

    new_row = {
        "display_name": fields.get("display_name", current.get("display_name", "")),
        "profession": fields.get("profession", current.get("profession", "")),
        "language": fields.get("language", current.get("language", "auto")),
        "facts": json.dumps(merged_facts, ensure_ascii=False),
        "preferences": json.dumps(merged_prefs, ensure_ascii=False),
        "updated_at": _now(),
    }

    with _lock, _connect() as conn:
        conn.execute(
            """
            INSERT INTO user_profile (username, display_name, profession, language, facts, preferences, created_at, updated_at)
            VALUES (:username, :display_name, :profession, :language, :facts, :preferences, :created_at, :updated_at)
            ON CONFLICT(username) DO UPDATE SET
                display_name = excluded.display_name,
                profession   = excluded.profession,
                language     = excluded.language,
                facts        = excluded.facts,
                preferences  = excluded.preferences,
                updated_at   = excluded.updated_at
            """,
            {**new_row, "username": username, "created_at": current.get("created_at") or _now()},
        )
        conn.commit()

    return get_profile(username)


def delete_profile(username: str) -> bool:
    with _lock, _connect() as conn:
        cur = conn.execute("DELETE FROM user_profile WHERE username = ?", (username,))
        conn.commit()
    return cur.rowcount > 0


def list_profiles() -> List[Dict[str, Any]]:
    with _lock, _connect() as conn:
        rows = conn.execute("SELECT * FROM user_profile ORDER BY updated_at DESC").fetchall()
    return [_row_to_dict(r) for r in rows]


def cleanup_old_facts(username: str) -> Dict[str, Any]:
    """Clean up facts with weight below threshold for a user."""
    profile = get_profile(username)
    if not profile:
        return profile
    
    original_count = len(profile.get("facts", []))
    profile["facts"] = [
        f for f in profile.get("facts", [])
        if isinstance(f, dict) and f.get("weight", 0) >= MIN_WEIGHT
    ]
    cleaned_count = len(profile["facts"])
    
    if cleaned_count != original_count:
        profile["updated_at"] = _now()
        with _lock, _connect() as conn:
            conn.execute(
                "UPDATE user_profile SET facts = ?, updated_at = ? WHERE username = ?",
                (json.dumps(profile["facts"], ensure_ascii=False), profile["updated_at"], username),
            )
            conn.commit()
        logger.info(f"Cleaned up {original_count - cleaned_count} old facts for user {username}")
    
    return profile
