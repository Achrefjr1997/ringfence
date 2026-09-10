"""SIPREC recording metadata (RFC 7865, ``rs-metadata`` in the INVITE's
``application/rs-metadata+xml`` body).

We take three things from it:

* the recording **session id** — becomes RingFence's ``session=``;
* the **participants** and their AORs / display names — for employee
  attribution and (P4) signalling enrichment;
* the **participant <-> stream** associations — so an SDP ``a=label`` can be
  resolved to *caller* or *callee* once we know which AOR is the calling
  party.

If the calling party cannot be identified the role stays ``None`` and the
capture path falls back to acoustic attribution — degraded, never wrong
(``docs/DESIGN_PRODUCTION.md`` §3.1).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from xml.etree import ElementTree as ET


def _local(tag: str) -> str:
    return tag.rpartition("}")[2]


@dataclass(frozen=True, slots=True)
class Participant:
    participant_id: str
    aor: str | None
    name: str | None


@dataclass(frozen=True, slots=True)
class Stream:
    stream_id: str
    label: str | None
    session_id: str | None


@dataclass(frozen=True, slots=True)
class RecordingMetadata:
    session_id: str | None
    sip_session_id: str | None
    participants: tuple[Participant, ...]
    streams: tuple[Stream, ...]
    # participant_id -> (sent stream_ids, received stream_ids)
    associations: dict[str, tuple[frozenset[str], frozenset[str]]] = field(default_factory=dict)

    def _label_of(self, stream_id: str) -> str | None:
        for s in self.streams:
            if s.stream_id == stream_id:
                return s.label
        return None

    def _resolve_participant(
        self, *, aor: str | None = None, participant_id: str | None = None
    ) -> Participant | None:
        for p in self.participants:
            if participant_id is not None and p.participant_id == participant_id:
                return p
            if aor is not None and p.aor is not None and _same_aor(p.aor, aor):
                return p
        return None

    def other_participant(
        self, *, caller_aor: str | None = None, caller_participant_id: str | None = None
    ) -> Participant | None:
        """The participant that is *not* the caller — the called (protected)
        party.  Returns ``None`` if the caller is unknown or there aren't
        exactly two participants."""
        caller = self._resolve_participant(aor=caller_aor, participant_id=caller_participant_id)
        if caller is None or len(self.participants) != 2:
            return None
        return next(p for p in self.participants if p.participant_id != caller.participant_id)

    def caller_label(
        self, *, caller_aor: str | None = None, caller_participant_id: str | None = None
    ) -> str | None:
        """The SDP ``a=label`` of the stream carrying the calling party's own
        voice, or ``None`` if the caller cannot be identified.
        """
        who = self._resolve_participant(aor=caller_aor, participant_id=caller_participant_id)
        if who is None:
            return None
        sent, _recv = self.associations.get(who.participant_id, (frozenset(), frozenset()))
        for stream_id in sent:
            label = self._label_of(stream_id)
            if label is not None:
                return label
        return None

    def leg_for_label(
        self,
        label: str,
        *,
        caller_aor: str | None = None,
        caller_participant_id: str | None = None,
    ) -> str | None:
        """``"far"`` (caller), ``"near"`` (callee), or ``None`` when the
        calling party is unknown or the label is not in this metadata.
        """
        known = {s.label for s in self.streams if s.label is not None}
        if label not in known:
            return None
        caller = self.caller_label(
            caller_aor=caller_aor, caller_participant_id=caller_participant_id
        )
        if caller is None:
            return None
        return "far" if label == caller else "near"


def _same_aor(a: str, b: str) -> bool:
    return _norm_aor(a) == _norm_aor(b)


def _norm_aor(aor: str) -> str:
    aor = aor.strip().lower()
    if "<" in aor and ">" in aor:  # display-name <sip:user@host>
        aor = aor[aor.index("<") + 1 : aor.index(">")]
    for scheme in ("sip:", "sips:", "tel:"):
        if aor.startswith(scheme):
            aor = aor[len(scheme) :]
            break
    return aor.split(";")[0].strip()


def parse_recording_metadata(xml: str | bytes) -> RecordingMetadata:
    # The SRC is authenticated at the dialog layer (mTLS at the SBC, P4); this
    # body is not attacker-controlled.  Entity expansion is off by default in
    # ElementTree since 3.7.
    root = ET.fromstring(xml)
    if _local(root.tag) != "recording":
        raise ValueError(f"not an rs-metadata document: root <{_local(root.tag)}>")

    session_id: str | None = None
    sip_session_id: str | None = None
    participants: list[Participant] = []
    streams: list[Stream] = []
    assoc: dict[str, tuple[set[str], set[str]]] = {}

    for el in root:
        tag = _local(el.tag)
        if tag == "session":
            session_id = el.get("session_id") or session_id
            for child in el:
                if _local(child.tag) == "sipSessionID" and child.text:
                    sip_session_id = child.text.strip()
        elif tag == "participant":
            pid = el.get("participant_id") or ""
            aor: str | None = None
            name: str | None = None
            for child in el:
                if _local(child.tag) == "nameID":
                    aor = child.get("aor") or aor
                    for gc in child:
                        if _local(gc.tag) == "name" and gc.text:
                            name = gc.text.strip()
            participants.append(Participant(participant_id=pid, aor=aor, name=name))
        elif tag == "stream":
            sid = el.get("stream_id") or ""
            label: str | None = None
            for child in el:
                if _local(child.tag) == "label" and child.text:
                    label = child.text.strip()
            streams.append(Stream(stream_id=sid, label=label, session_id=el.get("session_id")))
        elif tag == "participantstreamassoc":
            pid = el.get("participant_id") or ""
            sent, recv = assoc.setdefault(pid, (set(), set()))
            for child in el:
                ctag = _local(child.tag)
                if child.text and ctag == "send":
                    sent.add(child.text.strip())
                elif child.text and ctag == "recv":
                    recv.add(child.text.strip())

    return RecordingMetadata(
        session_id=session_id,
        sip_session_id=sip_session_id,
        participants=tuple(participants),
        streams=tuple(streams),
        associations={
            pid: (frozenset(sent), frozenset(recv)) for pid, (sent, recv) in assoc.items()
        },
    )
