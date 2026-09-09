"""P7 -- LocalFsObjectStore."""

from __future__ import annotations

from pathlib import Path

import pytest

from packages.storage.objectstore import LocalFsObjectStore


def _store(tmp_path: Path) -> LocalFsObjectStore:
    return LocalFsObjectStore(tmp_path / "audio")


def test_put_get_size_and_exists(tmp_path: Path) -> None:
    s = _store(tmp_path)
    assert s.exists("t/2026-01/x.opus") is False
    assert s.get("t/2026-01/x.opus") is None
    s.put("t/2026-01/x.opus", b"hello world")
    assert s.exists("t/2026-01/x.opus") is True
    assert s.get("t/2026-01/x.opus") == b"hello world"
    assert s.size("t/2026-01/x.opus") == 11


def test_read_range(tmp_path: Path) -> None:
    s = _store(tmp_path)
    s.put("k", b"0123456789")
    assert s.read_range("k", 0, 4) == b"0123"
    assert s.read_range("k", 5, 100) == b"56789"
    assert s.read_range("k", 20, 4) == b""


def test_delete_is_idempotent(tmp_path: Path) -> None:
    s = _store(tmp_path)
    s.put("k", b"x")
    s.delete("k")
    s.delete("k")  # no error
    assert s.exists("k") is False


def test_put_is_atomic_no_part_left(tmp_path: Path) -> None:
    s = _store(tmp_path)
    s.put("a/b.opus", b"data")
    leftovers = list((tmp_path / "audio").rglob("*.part"))
    assert leftovers == []


@pytest.mark.parametrize("bad", ["../escape", "a/../../b", "..", ""])
def test_unsafe_keys_are_rejected(tmp_path: Path, bad: str) -> None:
    s = _store(tmp_path)
    with pytest.raises(ValueError):
        s.put(bad, b"x")


def test_a_leading_slash_is_normalised_not_an_escape(tmp_path: Path) -> None:
    s = _store(tmp_path)
    s.put("/abs/path.opus", b"x")
    assert s.get("abs/path.opus") == b"x"  # stayed under the root
