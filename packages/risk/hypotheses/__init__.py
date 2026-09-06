from pathlib import Path

import yaml

from packages.policy.pack import KNOWN_SIGNAL_IDS

HYPOTHESIS_DIR = Path(__file__).resolve().parent


def load_hypotheses(language: str) -> dict[str, list[str]]:
    """Per-signal zero-shot NLI hypotheses for ``language`` (``en`` / ``fr``).

    Same shape and validation as ``risk.lexicons.load_lexicons`` — a mapping
    of known signal id to a non-empty list of natural-language statements.
    """
    path = HYPOTHESIS_DIR / f"{language}.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{language}: expected a mapping of signal id -> hypotheses")
    unknown = set(data) - KNOWN_SIGNAL_IDS
    if unknown:
        raise ValueError(f"{language}: unknown signal ids: {sorted(unknown)}")
    out: dict[str, list[str]] = {}
    for signal_id, hyps in data.items():
        if not isinstance(hyps, list) or not hyps or not all(isinstance(h, str) for h in hyps):
            raise ValueError(f"{language}/{signal_id}: expected a non-empty list of strings")
        out[str(signal_id)] = [h for h in hyps if isinstance(h, str)]
    return out
