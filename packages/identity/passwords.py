"""Password hashing — stdlib ``hashlib.scrypt``, no dependency (T-7.1a).

Stored form (``User.password_hash``)::

    scrypt$<n>$<r>$<p>$<salt_b64>$<hash_b64>

``verify_password`` re-derives with the parameters embedded in the stored
string, so the cost factors can be raised later without invalidating
existing hashes.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

_N = 2**14  # ~16 MiB working set at r=8
_R = 8
_P = 1
_DKLEN = 32
_SALT_BYTES = 16
_MAXMEM = 64 * 1024 * 1024  # headroom over 128*n*r, portable across OpenSSL builds


def hash_password(password: str) -> str:
    if not password:
        raise ValueError("password must not be empty")
    salt = secrets.token_bytes(_SALT_BYTES)
    dk = hashlib.scrypt(
        password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=_DKLEN, maxmem=_MAXMEM
    )
    return "$".join(
        [
            "scrypt",
            str(_N),
            str(_R),
            str(_P),
            base64.b64encode(salt).decode(),
            base64.b64encode(dk).decode(),
        ]
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_b64, hash_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        dk = hashlib.scrypt(
            password.encode(),
            salt=salt,
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
            maxmem=_MAXMEM,
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(dk, expected)
