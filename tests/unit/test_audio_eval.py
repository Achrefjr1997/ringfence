"""T-5.1 / T-5.2 -- the real-recording eval harness (null-ASR wiring)."""

from __future__ import annotations

import json

import pytest

from packages.eval import audio_eval
from packages.eval.corpus import iter_corpus, load_corpus_item, validate_corpus


def test_the_example_corpus_item_loads_and_its_wav_is_valid() -> None:
    assert validate_corpus() == []  # every label's wav is 16k mono
    item = load_corpus_item("example_delivery_en_001")
    assert item.label == "benign"  # "legit" alias normalised
    assert item.wav_path.exists()


def test_evaluate_item_runs_a_pipeline_and_scores_against_the_label() -> None:
    item = load_corpus_item("example_delivery_en_001")
    r = audio_eval.evaluate_item(item, provider=audio_eval._provider("null"), pack=None)  # type: ignore[arg-type]
    assert r["id"] == "example_delivery_en_001"
    assert r["label"] == "benign"
    assert r["peak_state"] == "CALM" and r["alerted"] is False  # null ASR -> no signals


def test_run_report_shape() -> None:
    report = audio_eval.run(asr="null")
    a = report["aggregate"]
    assert report["asr"] == "null"
    assert set(a) >= {
        "items",
        "fraud",
        "benign",
        "detection_rate",
        "detection_before_transfer",
        "false_alarm_rate",
        "median_alert_t",
    }
    assert a["items"] == len(iter_corpus())
    assert a["false_alarm_rate"] == 0.0  # the one benign item stays CALM


def test_rate_helper() -> None:
    assert audio_eval._rate(1, 2) == 0.5
    assert audio_eval._rate(0, 0) is None


def test_markdown_and_cli(capsys: pytest.CaptureFixture[str]) -> None:
    md = audio_eval.render_markdown(audio_eval.run(asr="null"))
    assert "Audio-path eval" in md and "detection rate" in md

    assert audio_eval.main([]) == 0
    json.loads(capsys.readouterr().out)  # default output is JSON

    # gate passes when there are no fraud items to fail on
    assert audio_eval.main(["--gate"]) == 0
    capsys.readouterr()
