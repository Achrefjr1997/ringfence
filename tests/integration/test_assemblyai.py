"""Live AssemblyAI streaming check.  Runs only with a real key:

    pytest tests/integration/test_assemblyai.py -q -m needs_key

Skips cleanly when ``ASSEMBLYAI_API_KEY`` is unset (env var or repo .env).
"""

import asyncio
import os
from pathlib import Path

import pytest
import soundfile as sf

from packages.asr.assemblyai import AssemblyAIStreaming
from packages.asr.provider import StreamSpec
from packages.contracts.transcript import Turn

REPO = Path(__file__).resolve().parents[2]
WAV = REPO / "corpus" / "fixtures" / "audio" / "hello_16k.wav"

pytestmark = pytest.mark.needs_key


def _api_key() -> str | None:
    key = os.environ.get("ASSEMBLYAI_API_KEY")
    if key:
        return key
    env = REPO / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("ASSEMBLYAI_API_KEY="):
                return line.split("=", 1)[1].strip() or None
    return None


def _pcm16_chunks(path: Path, chunk_ms: int = 50, sample_rate: int = 16_000) -> list[bytes]:
    data, sr = sf.read(path, dtype="int16")
    assert sr == sample_rate, f"fixture must be {sample_rate} Hz, got {sr}"
    assert data.ndim == 1, "fixture must be mono"
    raw = data.tobytes()
    step = int(sample_rate * chunk_ms / 1000) * 2  # PCM16 -> 2 bytes/sample
    return [raw[i : i + step] for i in range(0, len(raw), step)]


async def test_streams_wav_and_gets_a_non_empty_final_transcript() -> None:
    key = _api_key()
    if not key:
        pytest.skip("ASSEMBLYAI_API_KEY not set")
    assert WAV.exists(), f"missing fixture audio: {WAV}"

    provider = AssemblyAIStreaming(key)
    stream = await provider.open(StreamSpec(session_id="it-1", leg_id="far", language="en"))

    collected: list[Turn] = []

    async def _collect() -> None:
        async for turn in stream.turns():
            collected.append(turn)

    reader = asyncio.create_task(_collect())
    try:
        for chunk in _pcm16_chunks(WAV):
            await stream.feed(chunk)
            await asyncio.sleep(0.05)  # roughly real time
        await asyncio.sleep(4.0)  # let the server finalise the last turn
    finally:
        await stream.close()
        await asyncio.wait_for(reader, timeout=10.0)

    assert collected, "no turns received from AssemblyAI"
    assert all(isinstance(t, Turn) for t in collected)
    finals = [t for t in collected if t.is_final and t.text.strip()]
    assert finals, f"no non-empty final transcript; got {[t.text for t in collected]}"
    assert stream.dropped_frames == 0
