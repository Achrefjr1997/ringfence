"""External benchmark loader — BothBosu/multi-agent-scam-conversation.

A vendored, offline snapshot of a **synthetic** (Llama-3-70B generated)
English scam-call dataset, used to measure how the Tier-1 rule engine
generalises beyond the eight hand-written fixtures.  Not a CI gate, not a
policy target — see ``docs/EXTERNAL_BENCHMARK.md``.

The snapshot (``corpus/external/multi_agent_scam_conversation.jsonl.gz``,
one JSON object per line) carries only segmented turns:

    {"id", "split", "label", "source_type", "turns": [{"role", "text"}, ...]}

Timings are absent upstream, so we synthesise a deterministic timeline from
a plain speaking-rate model (below) and expose each dialogue as an
``eval.fixtures.Fixture`` — the exact shape ``replay_fixture`` consumes.
``expect_signals`` is empty for every turn: the labels are call-level
(scam / not), there is no per-turn signal ground truth.
"""

from __future__ import annotations

import gzip
import json
from collections.abc import Iterator
from pathlib import Path
from typing import cast

from packages.contracts.transcript import Role
from packages.eval.fixtures import Fixture, FixtureTurn, Label

EXTERNAL_DIR = Path(__file__).resolve().parents[2] / "corpus" / "external"
SNAPSHOT = EXTERNAL_DIR / "multi_agent_scam_conversation.jsonl.gz"
DATASET_NAME = "BothBosu/multi-agent-scam-conversation"

# --- synthetic timeline ---------------------------------------------------
# Upstream dialogues have no timestamps, and their "turns" are LLM
# paragraphs, not measured speech.  We lay them on a synthetic timeline so
# the evidence window's decay (90 s half-life) and the combo time-windows
# behave.  Two models, both documented in docs/EXTERNAL_BENCHMARK.md:
#
#   words model (default): dur = clip(words / 3.0, 1.5 s, 12 s), + 1 s gap.
#       ~180 wpm, but a long paragraph is capped at 12 s — it stands in for
#       a stretch of dialogue, not one uninterrupted minute of monologue.
#   beat model (``seconds_per_turn=N``): every turn is exactly N s.  A
#       pacing-independent "one conversational beat per turn" view.
#
# TTD figures are "seconds of synthetic call time", not measured latency;
# recall is sensitive to this choice, which is why the benchmark is
# supplementary.
_WORDS_PER_SECOND = 3.0
_INTER_TURN_GAP_S = 1.0
_MIN_TURN_S = 1.5
_MAX_TURN_S = 12.0


def _timeline(
    raw_turns: list[dict[str, str]], *, seconds_per_turn: float | None
) -> list[FixtureTurn]:
    turns: list[FixtureTurn] = []
    t = 0.0
    for rt in raw_turns:
        text = rt["text"]
        if seconds_per_turn is not None:
            dur = seconds_per_turn
            gap = 0.0
        else:
            dur = min(_MAX_TURN_S, max(_MIN_TURN_S, len(text.split()) / _WORDS_PER_SECOND))
            gap = _INTER_TURN_GAP_S
        turns.append(
            FixtureTurn(
                t_start=round(t, 3),
                t_end=round(t + dur, 3),
                role=cast(Role, rt["role"]),  # snapshot stores "CALLER" / "CALLEE"
                text=text,
                expect_signals=[],
            )
        )
        t += dur + gap
    return turns


def _to_fixture(rec: dict[str, object], *, seconds_per_turn: float | None) -> Fixture:
    turns = _timeline(rec["turns"], seconds_per_turn=seconds_per_turn)  # type: ignore[arg-type]
    last_caller = [ft for ft in turns if ft.role == "CALLER"]
    return Fixture(
        id=str(rec["id"]),
        language="en",
        label=rec["label"],  # type: ignore[arg-type]
        scam_family=f"external/{rec['source_type']}",
        transfer_request_t=last_caller[-1].t_end if last_caller else None,
        turns=turns,
        expect_final_state="CALM",
        expect_call_level_signals=[],
    )


def iter_external(
    *,
    split: str | None = None,
    label: Label | None = None,
    limit: int | None = None,
    seconds_per_turn: float | None = None,
) -> Iterator[Fixture]:
    """Yield each vendored dialogue as a :class:`Fixture`.

    ``split`` filters ``"train"`` / ``"test"``; ``label`` filters
    ``"fraud"`` / ``"benign"``; ``limit`` caps the count (after filtering).
    ``seconds_per_turn`` switches from the words timeline model to a fixed
    beat of that many seconds per turn.
    """
    if not SNAPSHOT.exists():  # pragma: no cover - guard for a missing vendored file
        raise FileNotFoundError(
            f"{SNAPSHOT} is missing — regenerate it with "
            f"`python corpus/external/build_snapshot.py`"
        )
    n = 0
    with gzip.open(SNAPSHOT, "rt", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if split is not None and rec["split"] != split:
                continue
            if label is not None and rec["label"] != label:
                continue
            yield _to_fixture(rec, seconds_per_turn=seconds_per_turn)
            n += 1
            if limit is not None and n >= limit:
                return
