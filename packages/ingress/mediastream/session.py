"""One forked call: two legs, one shared gain."""

from __future__ import annotations

from packages.media.normalise import AudioNormaliser, SessionNormaliser

_SRC_RATE = 8_000


class StreamSession:
    """Per-leg resampling with **one** ``SessionNormaliser`` across the call.

    Per-leg gain is forbidden -- ``packages/media/normalise.py`` explains why
    at length.  Role inference reads the *relative* level between the two
    parties, and normalising each leg independently erases exactly that.
    """

    def __init__(self, session_id: str, *, src_rate: int = _SRC_RATE) -> None:
        self.session_id = session_id
        self._src_rate = src_rate
        self._gain = SessionNormaliser()
        self._legs: dict[str, AudioNormaliser] = {}

    def frames(self, leg: str, pcm_bytes: bytes) -> list[bytes]:
        norm = self._legs.get(leg)
        if norm is None:
            norm = AudioNormaliser(src_rate=self._src_rate, session=self._gain)
            self._legs[leg] = norm
        return norm.process(pcm_bytes)
