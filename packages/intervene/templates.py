from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from packages.policy.pack import KNOWN_SIGNAL_IDS

_PATH = Path(__file__).resolve().parent / "templates.yaml"
LANGUAGES = ("en", "fr", "ar_tn")


@lru_cache
def load_templates() -> dict[str, dict[str, str]]:
    data = yaml.safe_load(_PATH.read_text(encoding="utf-8"))
    templates: dict[str, dict[str, str]] = data["templates"]
    ids = set(templates) - {"DEFAULT"}
    missing = KNOWN_SIGNAL_IDS - ids
    if missing:
        raise ValueError(f"templates.yaml missing signals: {sorted(missing)}")
    if "DEFAULT" not in templates:
        raise ValueError("templates.yaml missing DEFAULT")
    for tid, by_lang in templates.items():
        gap = set(LANGUAGES) - set(by_lang)
        if gap:
            raise ValueError(f"template {tid} missing languages: {sorted(gap)}")
    return templates


def select_template(signal_id: str | None, language: str) -> tuple[str, str]:
    """(template_id, text).  Falls back to DEFAULT, and to English within a
    template if the language is absent."""
    templates = load_templates()
    tid = signal_id if signal_id in templates else "DEFAULT"
    by_lang = templates[tid]
    return tid, by_lang.get(language) or by_lang["en"]
