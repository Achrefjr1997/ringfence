"""Oversight console P6 -- in-memory per-call transcript store."""

from __future__ import annotations

from packages.calls.transcripts import InMemoryTranscriptStore


def test_save_and_get_round_trip() -> None:
    s = InMemoryTranscriptStore()
    turns = [("CALLER", "hello", 1.0), ("CALLEE", "hi", 2.5)]
    s.save("s1", "acme", turns)
    assert s.get("s1") == turns
    assert s.get("s1") is not turns  # a copy, not the caller's list
    assert s.get("missing") == []


def test_empty_save_is_a_noop() -> None:
    s = InMemoryTranscriptStore()
    s.save("s1", "acme", [])
    assert s.get("s1") == []


def test_save_replaces_the_prior_transcript() -> None:
    s = InMemoryTranscriptStore()
    s.save("s1", "acme", [("CALLER", "v1", 1.0)])
    s.save("s1", "acme", [("CALLER", "v2", 1.0), ("CALLEE", "ok", 2.0)])
    assert [t[1] for t in s.get("s1")] == ["v2", "ok"]
