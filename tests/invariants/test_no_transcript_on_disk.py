"""Invariant #5 — transcripts are never written to disk (default settings).

`AGENTS.md`: "Transcripts are never written to disk unless
`RF_RETAIN_TRANSCRIPTS=true`." This asserts it two ways: the settings flag
defaults off, and the one artefact the engine actually writes — the
evaluation report — carries no transcript text, checked both in the file
and in every intercepted disk write.
"""

from __future__ import annotations

import pathlib
from collections.abc import Iterator

import pytest

from packages.contracts.settings import Settings
from packages.eval.fixtures import iter_fixtures, load_fixture
from packages.eval.run import main as run_eval

_MIN_NEEDLE = 20  # chars — long enough that a match is unambiguously the transcript


def _transcript_needles() -> Iterator[bytes]:
    for fx in iter_fixtures():
        for turn in load_fixture(fx.id).turns:
            needle = turn.text.strip().lower().encode()[:64]
            if len(needle) >= _MIN_NEEDLE:
                yield needle


@pytest.fixture
def disk_writes(monkeypatch: pytest.MonkeyPatch) -> list[bytes]:
    """Every ``Path`` write during the test, as raw bytes."""
    seen: list[bytes] = []
    real_wt = pathlib.Path.write_text
    real_wb = pathlib.Path.write_bytes
    real_open = pathlib.Path.open

    def wt(self: pathlib.Path, data: str, *a: object, **kw: object) -> int:
        seen.append(data.encode())
        return real_wt(self, data, *a, **kw)  # type: ignore[arg-type]

    def wb(self: pathlib.Path, data: object, *a: object, **kw: object) -> int:
        seen.append(bytes(data))  # type: ignore[arg-type]
        return real_wb(self, data, *a, **kw)  # type: ignore[arg-type]

    def opn(self: pathlib.Path, mode: str = "r", *a: object, **kw: object):  # noqa: ANN202
        handle = real_open(self, mode, *a, **kw)  # type: ignore[arg-type]
        if any(c in mode for c in "wax+"):
            orig = handle.write

            def tracked(chunk: object) -> int:
                seen.append(chunk.encode() if isinstance(chunk, str) else bytes(chunk))  # type: ignore[arg-type]
                return orig(chunk)  # type: ignore[arg-type,no-any-return]

            handle.write = tracked  # type: ignore[method-assign,assignment]
        return handle

    monkeypatch.setattr(pathlib.Path, "write_text", wt)
    monkeypatch.setattr(pathlib.Path, "write_bytes", wb)
    monkeypatch.setattr(pathlib.Path, "open", opn)
    return seen


@pytest.mark.invariant
def test_retain_transcripts_defaults_off() -> None:
    assert Settings().retain_transcripts is False


@pytest.mark.invariant
def test_eval_report_on_disk_carries_no_transcript(
    tmp_path: pathlib.Path, disk_writes: list[bytes]
) -> None:
    out = tmp_path / "report.json"
    assert run_eval(["--all", "--report", str(out)]) == 0
    assert disk_writes, "the write spy caught nothing — it is not wired"

    on_disk = out.read_bytes().lower()
    stream = b"\n".join(disk_writes).lower()
    for needle in _transcript_needles():
        assert needle not in on_disk, f"transcript in report file: {needle!r}"
        assert needle not in stream, f"transcript in a disk write: {needle!r}"
