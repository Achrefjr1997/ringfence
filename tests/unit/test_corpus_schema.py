import numpy as np
import pytest
import soundfile as sf

from packages.eval.corpus import (
    CORPUS_DIR,
    CorpusError,
    CorpusItem,
    iter_corpus,
    load_corpus_item,
    validate_corpus,
    validate_wav,
)


def test_example_item_loads_with_normalised_label() -> None:
    item = load_corpus_item("example_delivery_en_001")
    assert item.label == "benign"  # "legit" alias normalised
    assert item.language == "en"
    assert item.file == "fixtures/audio/hello_16k.wav"
    assert item.wav_path == CORPUS_DIR / "fixtures" / "audio" / "hello_16k.wav"
    assert item.transfer_line_t is None
    assert item.first_signal_t is None  # no signalled turns in the example


@pytest.mark.parametrize(
    "raw, norm", [("scam", "fraud"), ("legitimate", "benign"), ("fraud", "fraud")]
)
def test_label_aliases(raw: str, norm: str) -> None:
    item = CorpusItem(id="x", file="a.wav", language="fr", label=raw)  # type: ignore[arg-type]
    assert item.label == norm


def test_unknown_signal_id_is_rejected() -> None:
    with pytest.raises(ValueError):
        CorpusItem(
            id="x",
            file="a.wav",
            language="en",
            label="fraud",
            turns=[{"t_start": 0, "t_end": 1, "role": "CALLER", "signals": ["NOT_A_SIGNAL"]}],  # type: ignore[list-item]
        )


def test_validate_wav_accepts_the_example_and_whole_corpus() -> None:
    validate_wav(load_corpus_item("example_delivery_en_001").wav_path)
    assert validate_corpus() == []  # every referenced wav exists and is 16 kHz mono


def test_validate_wav_rejects_missing_wrong_rate_and_stereo(tmp_path) -> None:
    with pytest.raises(CorpusError):
        validate_wav(tmp_path / "nope.wav")

    eight_k = tmp_path / "lo.wav"
    sf.write(eight_k, np.zeros(8000, dtype="int16"), 8000)
    with pytest.raises(CorpusError, match="8000 Hz"):
        validate_wav(eight_k)

    stereo = tmp_path / "stereo.wav"
    sf.write(stereo, np.zeros((16000, 2), dtype="int16"), 16000)
    with pytest.raises(CorpusError, match="channels"):
        validate_wav(stereo)


def test_iter_corpus_filters_by_label() -> None:
    assert [i.id for i in iter_corpus(label="benign")] == ["example_delivery_en_001"]
    assert iter_corpus(label="fraud") == []
