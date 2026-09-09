"""Object store for call recordings (oversight console P7).

A tiny blob interface -- ``put`` / ``get`` / ``read_range`` / ``delete`` --
so the audio subsystem does not care where bytes live. The only
implementation today is :class:`LocalFsObjectStore` (a mounted volume in
prod); an S3 client can slot in behind the same Protocol later.

Keys are ``tenant/YYYY-MM/<session>.opus`` style relative paths; the local
store rejects anything that escapes its root.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol


class ObjectStore(Protocol):
    def put(self, key: str, data: bytes) -> None: ...

    def get(self, key: str) -> bytes | None: ...

    def size(self, key: str) -> int | None: ...

    def read_range(self, key: str, start: int, length: int) -> bytes: ...

    def delete(self, key: str) -> None: ...

    def exists(self, key: str) -> bool: ...


def _clean_key(key: str) -> str:
    key = key.strip().lstrip("/")
    parts = [p for p in key.replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        raise ValueError(f"unsafe object key: {key!r}")
    return "/".join(parts)


class LocalFsObjectStore:
    """Blobs under ``root``; each key is a relative path below it."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self._root = Path(root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        p = (self._root / _clean_key(key)).resolve()
        if not p.is_relative_to(self._root):  # extra belt for symlinks
            raise ValueError(f"unsafe object key: {key!r}")
        return p

    def put(self, key: str, data: bytes) -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".part")
        tmp.write_bytes(data)
        tmp.replace(p)

    def get(self, key: str) -> bytes | None:
        p = self._path(key)
        return p.read_bytes() if p.is_file() else None

    def size(self, key: str) -> int | None:
        p = self._path(key)
        return p.stat().st_size if p.is_file() else None

    def read_range(self, key: str, start: int, length: int) -> bytes:
        with self._path(key).open("rb") as fh:
            fh.seek(max(0, start))
            return fh.read(max(0, length))

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()
