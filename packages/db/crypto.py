"""At-rest encryption for sensitive columns (T-7.4).

Wraps Fernet (AES-128-CBC + HMAC) so a leak of the database alone does not
expose operator feedback notes or a retained transcript.  ``MultiFernet``
underneath: ``RF_DATA_ENCRYPTION_KEY`` may be a comma-separated list --
new value first for encryption, every value tried for decryption -- so the
key can be rotated without a migration (see docs/RUNBOOKS.md).

Ciphertext is stored with an ``enc:`` prefix, so rows written before a key
was configured still read back untouched.  With no key set the cipher is a
pass-through: acceptable for local work, not for production (the gateway
logs a warning once).
"""

from __future__ import annotations

import logging

from packages.contracts.settings import get_settings

log = logging.getLogger("ringfence.crypto")

_PREFIX = "enc:"
_warned = False


class ColumnCipher:
    def __init__(self, keys: str | None) -> None:
        self._fernet = None
        self._primary = None
        if keys and keys.strip():
            from cryptography.fernet import Fernet, MultiFernet  # noqa: PLC0415 - 'db' extra

            parts = [k.strip() for k in keys.split(",") if k.strip()]
            self._fernet = MultiFernet([Fernet(p) for p in parts])
            self._primary = Fernet(parts[0])

    @property
    def active(self) -> bool:
        return self._fernet is not None

    def encrypt(self, plaintext: str | None) -> str | None:
        if plaintext is None:
            return None
        if self._fernet is None:
            return plaintext
        return _PREFIX + self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, stored: str | None) -> str | None:
        if stored is None:
            return None
        if not stored.startswith(_PREFIX):
            return stored  # written before a key was configured
        if self._fernet is None:
            raise RuntimeError("RF_DATA_ENCRYPTION_KEY is required to read encrypted columns")
        return self._fernet.decrypt(stored[len(_PREFIX) :].encode()).decode()

    def rotate(self, stored: str | None) -> str | None:
        """Re-encrypt ``stored`` under the current (first) key, or return
        ``None`` if it is already current or there is nothing to do."""
        if not stored:
            return None
        if not stored.startswith(_PREFIX):
            # plaintext row from before a key existed: bring it under the key
            return self.encrypt(stored) if self.active else None
        raw = stored[len(_PREFIX) :].encode()
        if self._primary is not None:
            from cryptography.fernet import InvalidToken  # noqa: PLC0415

            try:
                self._primary.decrypt(raw)
                return None  # already at the current key
            except InvalidToken:
                pass
        assert self._fernet is not None
        return _PREFIX + self._fernet.encrypt(self._fernet.decrypt(raw)).decode()


def column_cipher() -> ColumnCipher:
    global _warned
    cipher = ColumnCipher(get_settings().data_encryption_key)
    if not cipher.active and not _warned:
        _warned = True
        log.warning("RF_DATA_ENCRYPTION_KEY unset - sensitive columns stored in clear")
    return cipher
