from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable

from .config import APP_DIR, DEFAULT_SEARCH_KEYWORDS

SETTINGS_DIR = APP_DIR / "data"
KEYWORDS_PATH = SETTINGS_DIR / "search_keywords.json"


def clean_keywords(values: Iterable[str]) -> list[str]:
    """Normalize user-entered keywords while preserving order."""
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        for item in re.split(r"[\r\n,，;；]+", str(value or "")):
            keyword = item.strip().strip('"\'')
            key = keyword.casefold()
            if keyword and key not in seen:
                seen.add(key)
                output.append(keyword)
    return output


def _load_payload() -> dict:
    try:
        data = json.loads(KEYWORDS_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"keywords": data}
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return {}


def load_search_keywords() -> list[str]:
    """Load the persistent editable inventory; fall back to bundled defaults."""
    data = _load_payload()
    values = data.get("keywords", [])
    keywords = clean_keywords(values if isinstance(values, list) else [])
    return keywords or list(DEFAULT_SEARCH_KEYWORDS)


def load_active_keywords() -> list[str]:
    """Load keywords selected for search; newly added keywords default to enabled."""
    keywords = load_search_keywords()
    data = _load_payload()
    values = data.get("active_keywords")
    if not isinstance(values, list):
        return list(keywords)
    active = clean_keywords(values)
    return [kw for kw in active if kw in keywords]


def save_search_keywords(values: Iterable[str], active_values: Iterable[str] | None = None) -> list[str]:
    """Persist both the editable tag inventory and the default selected state."""
    keywords = clean_keywords(values)
    if not keywords:
        raise ValueError("至少保留一个检索关键词。")
    active = clean_keywords(active_values if active_values is not None else keywords)
    active = [kw for kw in active if kw in keywords]
    SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"keywords": keywords, "active_keywords": active}
    KEYWORDS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return keywords
