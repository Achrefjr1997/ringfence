"""The trusted directory -- who the verification agent is allowed to contact.

The scammer controls the words that reach the classifier. This file is what
stops those words from also choosing *who gets telephoned*. Three properties
make that structural rather than a matter of care:

1. Nothing in ``packages/verify`` takes a free-form destination. A caller can
   only ever obtain an :class:`Institution`, and the only way to obtain one
   is :meth:`Directory.resolve`, whose entire output space is this file.
2. ``packages/verify`` contains no number or address parser at all -- there
   is a test in ``tests/unit/test_verify_directory.py`` that greps for one.
3. Anything malformed is refused at **load**, not discovered mid-call. An
   alias claimed by two institutions is a coin-flip about who gets contacted,
   so it fails at startup.

Shaped after ``packages/billing/plans.py``: pydantic models, a file model,
an ``@lru_cache`` accessor.
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator

_CONFIG = Path(__file__).resolve().parents[2] / "config" / "verify" / "directory.yaml"

# Everything that is not a letter, a number or a space becomes a space before
# matching, so "AMAZON, Account Security!" and "amazon account security" are
# the same string. Deliberately not a parser -- it recognises nothing, it only
# flattens.
_NOT_WORDISH = re.compile(r"[^\w\s]", re.UNICODE)
_RUNS = re.compile(r"\s+")


def normalise(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text).casefold()
    return _RUNS.sub(" ", _NOT_WORDISH.sub(" ", folded)).strip()


class Institution(BaseModel):
    id: str
    display_name: str
    desk_id: str  # which verification desk answers for this institution
    line_label: str  # what the agent says it is calling about
    aliases: list[str] = Field(min_length=1)

    @field_validator("display_name", "line_label", "id", "desk_id")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must not be blank")
        return v

    @field_validator("aliases")
    @classmethod
    def _normalised(cls, v: list[str]) -> list[str]:
        out = [normalise(a) for a in v]
        if any(not a for a in out):
            raise ValueError("aliases must not be blank")
        return out


class Directory:
    def __init__(self, institutions: list[Institution]) -> None:
        by_id: dict[str, Institution] = {}
        by_alias: dict[str, Institution] = {}
        for inst in institutions:
            if inst.id in by_id:
                raise ValueError(f"duplicate institution id {inst.id!r}")
            by_id[inst.id] = inst
            for alias in inst.aliases:
                owner = by_alias.get(alias)
                if owner is not None:
                    raise ValueError(
                        f"alias {alias!r} is claimed by both {owner.id!r} and {inst.id!r} "
                        "-- an alias must name exactly one institution"
                    )
                by_alias[alias] = inst
        self._by_id = by_id
        # Longest first: "amazon pay fraud team" must beat "amazon" regardless
        # of the order institutions happen to appear in the file.
        self._aliases = sorted(by_alias, key=len, reverse=True)
        self._by_alias = by_alias

    def resolve(self, caller_text: str) -> Institution | None:
        """The institution this text claims to be from, or ``None``.

        ``None`` means no verification happens. There is deliberately no
        default and no fuzzy fallback: contacting the wrong institution is
        worse than contacting none.
        """
        haystack = normalise(caller_text)
        if not haystack:
            return None
        for alias in self._aliases:
            if alias in haystack:
                return self._by_alias[alias]
        return None

    def get(self, institution_id: str) -> Institution | None:
        return self._by_id.get(institution_id)

    def all(self) -> list[Institution]:
        return list(self._by_id.values())

    def desk_ids(self) -> frozenset[str]:
        """Every desk this directory may route to -- the boundary assertion
        anything holding an Institution can check against."""
        return frozenset(i.desk_id for i in self._by_id.values())


class _DirectoryFile(BaseModel):
    version: int
    institutions: list[Institution]


def load_directory(path: Path | str | None = None) -> Directory:
    raw = yaml.safe_load(Path(path or _CONFIG).read_text(encoding="utf-8"))
    parsed = _DirectoryFile.model_validate(raw)
    return Directory(parsed.institutions)


@lru_cache
def get_directory() -> Directory:
    return load_directory()
