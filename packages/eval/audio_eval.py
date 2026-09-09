"""Real-recording evaluation (T-5.1 / T-5.2, audio path).

The hand-written ``corpus/fixtures/*.json`` carry a transcript and drive
the offline engine through ``NullASR``.  This harness is the other half:
it takes an actual 16 kHz mono recording plus its label
(``corpus/labels/*.json``), runs it through the live :class:`Pipeline`
with a real ASR provider, and scores the decision chain against the label.

    python -m packages.eval.audio_eval --asr assemblyai --markdown
    python -m packages.eval.audio_eval --asr assemblyai --gate   # CI

``--asr null`` runs the wiring end to end but produces no transcript, so
every item scores CALM -- use it only to check the harness itself. A real
number needs ``--asr assemblyai`` and ``ASSEMBLYAI_API_KEY``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

from packages.asr.provider import ASRProvider
from packages.contracts.audio import Frame, LegSpec, Mode, RoleHint, SessionDescriptor
from packages.eval.corpus import CorpusItem, iter_corpus
from packages.pipeline.pipeline import Pipeline
from packages.policy.pack import PolicyPack, load_pack

_RATE = 16_000
_FRAME = 320  # 20 ms
_RANK = {"CALM": 0, "WATCH": 1, "ALERT": 2, "INTERVENE": 3, "RESOLVED": 0}

# detection / false-alarm gate (audio path)
_MIN_DETECTION = 0.7
_MAX_FALSE_ALARM = 0.1


def _wav_bytes(path: Path) -> bytes:
    import numpy as np
    import soundfile as sf

    data, rate = sf.read(path, dtype="int16", always_2d=False)
    if rate != _RATE:
        raise ValueError(f"{path.name}: {rate} Hz, expected {_RATE}")
    return np.asarray(data, dtype="<i2").tobytes()


async def _drive(item: CorpusItem, provider: ASRProvider, pack: PolicyPack) -> Pipeline:
    audio = _wav_bytes(item.wav_path)
    pipe = Pipeline(provider, pack=pack)
    await pipe.start(
        SessionDescriptor(
            session_id=item.id,
            tenant_id="audio-eval",
            mode=Mode.SDK,
            legs=(LegSpec(leg_id="mixed", role_hint=RoleHint.MIXED, sample_rate=_RATE),),
            started_at=0.0,
            language=item.language,
        )
    )
    for i in range(0, len(audio), _FRAME * 2):
        await pipe.feed(
            Frame(
                session_id=item.id,
                leg_id="mixed",
                pcm=audio[i : i + _FRAME * 2],
                sample_rate=_RATE,
                seq=i,
                captured_at=0.0,
            )
        )
    await pipe.end(item.id)
    return pipe


def evaluate_item(item: CorpusItem, *, provider: ASRProvider, pack: PolicyPack) -> dict[str, Any]:
    pipe = asyncio.run(_drive(item, provider, pack))

    label_signals = {s for t in item.turns for s in t.signals}
    observed = {c.id for d in pipe._decisions for c in d.contributions if c.source == "signal"}
    tp = len(label_signals & observed)
    alerted = _RANK[pipe._peak_state] >= _RANK["ALERT"]
    before_transfer = (
        item.transfer_line_t is not None
        and pipe._first_alert_t is not None
        and pipe._first_alert_t <= item.transfer_line_t
    )
    return {
        "id": item.id,
        "label": item.label,
        "language": item.language,
        "scam_family": item.scam_family,
        "peak_state": pipe._peak_state,
        "peak_score": round(pipe._peak_score, 2),
        "first_alert_t": pipe._first_alert_t,
        "transfer_line_t": item.transfer_line_t,
        "alerted": alerted,
        "alerted_before_transfer": before_transfer,
        "signal_recall": round(tp / len(label_signals), 3) if label_signals else None,
        "signal_precision": round(tp / len(observed), 3) if observed else None,
        "turns_seen": pipe._turns_seen,
    }


def run(*, asr: str = "null", pack: PolicyPack | None = None) -> dict[str, Any]:
    the_pack = pack or load_pack("config/policy/default.yaml")
    provider = _provider(asr)
    items = [evaluate_item(it, provider=provider, pack=the_pack) for it in iter_corpus()]

    fraud = [r for r in items if r["label"] == "fraud"]
    benign = [r for r in items if r["label"] == "benign"]
    latencies = [r["first_alert_t"] for r in fraud if r["first_alert_t"] is not None]
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "asr": asr,
        "pack": f"{the_pack.metadata.tenant}@{the_pack.metadata.version}",
        "aggregate": {
            "items": len(items),
            "fraud": len(fraud),
            "benign": len(benign),
            "detection_rate": _rate(sum(r["alerted"] for r in fraud), len(fraud)),
            "detection_before_transfer": _rate(
                sum(r["alerted_before_transfer"] for r in fraud), len(fraud)
            ),
            "false_alarm_rate": _rate(sum(r["alerted"] for r in benign), len(benign)),
            "median_alert_t": round(statistics.median(latencies), 2) if latencies else None,
        },
        "items": items,
    }


def _rate(n: int, d: int) -> float | None:
    return round(n / d, 3) if d else None


def _provider(asr: str) -> ASRProvider:
    if asr == "assemblyai":
        import os

        from packages.asr.assemblyai import AssemblyAIStreaming

        key = os.environ.get("ASSEMBLYAI_API_KEY")
        if not key:
            raise SystemExit("ASSEMBLYAI_API_KEY is required for --asr assemblyai")
        return AssemblyAIStreaming(key)
    from packages.asr.null import NullASR

    return NullASR([])


def render_markdown(report: dict[str, Any]) -> str:
    a = report["aggregate"]
    lines = [
        f"# Audio-path eval ({report['asr']} ASR)",
        "",
        f"- items: {a['items']}  (fraud {a['fraud']} / benign {a['benign']})",
        f"- detection rate: **{a['detection_rate']}**  (before transfer: {a['detection_before_transfer']})",
        f"- false-alarm rate: **{a['false_alarm_rate']}**",
        f"- median alert t: {a['median_alert_t']}s",
        "",
        "| id | label | peak | alert t | before transfer | signal recall |",
        "|---|---|---|---|---|---|",
    ]
    for r in report["items"]:
        lines.append(
            f"| {r['id']} | {r['label']} | {r['peak_state']} | {r['first_alert_t']} "
            f"| {r['alerted_before_transfer']} | {r['signal_recall']} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m packages.eval.audio_eval")
    ap.add_argument("--asr", choices=["null", "assemblyai"], default="null")
    ap.add_argument("--pack", type=Path, default=Path("config/policy/default.yaml"))
    ap.add_argument("--report", type=Path, default=None)
    ap.add_argument("--markdown", action="store_true")
    ap.add_argument("--gate", action="store_true", help="exit 1 if below the detection bar")
    args = ap.parse_args(argv)

    if not iter_corpus():
        print("no labelled recordings under corpus/labels/ -- see docs/CORPUS.md", file=sys.stderr)
        return 0

    report = run(asr=args.asr, pack=load_pack(str(args.pack)))
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(render_markdown(report) if args.markdown else json.dumps(report, indent=2))

    if args.gate:
        a = report["aggregate"]
        det, fa = a["detection_rate"], a["false_alarm_rate"]
        if (det is not None and det < _MIN_DETECTION) or (fa is not None and fa > _MAX_FALSE_ALARM):
            print(
                f"GATE FAIL: detection {det} < {_MIN_DETECTION} or false-alarm {fa} > {_MAX_FALSE_ALARM}",
                file=sys.stderr,
            )
            return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
