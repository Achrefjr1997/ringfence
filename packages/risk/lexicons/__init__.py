from pathlib import Path

import yaml

from packages.policy.pack import KNOWN_SIGNAL_IDS

LEXICON_DIR = Path(__file__).resolve().parent


def available_languages() -> frozenset[str]:
    """Languages we actually ship a lexicon for.

    Derived from the directory rather than hard-coded, so a new lexicon is
    usable the moment it lands and a resolver can never hand the pipeline a
    language it will fail to load.
    """
    return frozenset(p.stem for p in LEXICON_DIR.glob("*.yaml"))


def load_lexicons(language: str) -> dict[str, list[str]]:
    path = LEXICON_DIR / f"{language}.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{language}: expected a mapping of signal id -> terms")
    unknown = set(data) - KNOWN_SIGNAL_IDS
    if unknown:
        raise ValueError(f"{language}: unknown signal ids: {sorted(unknown)}")
    lexicons: dict[str, list[str]] = {}
    for signal_id, terms in data.items():
        if not isinstance(terms, list) or not all(isinstance(t, str) for t in terms):
            raise ValueError(f"{language}/{signal_id}: expected a list of strings")
        lexicons[str(signal_id)] = [t for t in terms if isinstance(t, str)]
    return lexicons
