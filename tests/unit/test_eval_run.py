import json

import pytest

from packages.eval.run import main, run_report


def test_report_covers_every_fixture_with_the_needed_fields() -> None:
    rep = run_report()
    assert rep["asr"] == "null" and rep["pack"] == "default@1"
    ids = {i["id"] for i in rep["items"]}
    assert len(ids) == 8
    for it in rep["items"]:
        assert it["label"] in {"fraud", "benign"}
        assert it["language"] in {"en", "fr", "ar_tn"}
        assert set(it) >= {
            "peak_state",
            "peak_score",
            "first_alert_t",
            "first_intervene_t",
            "transfer_request_t",
            "decisions",
            "turns",
        }
        for turn in it["turns"]:
            assert set(turn) >= {"observed_signals", "expected_signals", "role", "state"}


def test_label_filter() -> None:
    benign = run_report(label="benign")
    assert {i["label"] for i in benign["items"]} == {"benign"}
    assert len(benign["items"]) == 4


def test_fraud_items_alert_by_transfer_and_benign_never_intervene() -> None:
    rep = run_report()
    for it in rep["items"]:
        if it["label"] == "fraud":
            assert it["first_alert_t"] is not None
            assert it["first_alert_t"] <= it["transfer_request_t"]
        else:
            assert it["peak_state"] != "INTERVENE"


def test_cli_writes_a_json_report(tmp_path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "r.json"
    rc = main(["--all", "--report", str(out)])
    assert rc == 0
    data = json.loads(out.read_text())
    assert len(data["items"]) == 8
    assert "8 items: 4 fraud, 4 benign" in capsys.readouterr().out
