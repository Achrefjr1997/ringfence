"""T-7.4 -- at-rest column encryption."""

from __future__ import annotations

import pytest

from packages.db.crypto import ColumnCipher

pytest.importorskip("cryptography")
from cryptography.fernet import Fernet  # noqa: E402

K1 = Fernet.generate_key().decode()
K2 = Fernet.generate_key().decode()


def test_round_trip_and_prefix() -> None:
    c = ColumnCipher(K1)
    token = c.encrypt("operator note: caller very persistent")
    assert token is not None and token.startswith("enc:")
    assert "operator note" not in token
    assert c.decrypt(token) == "operator note: caller very persistent"


def test_none_and_empty_pass_through() -> None:
    c = ColumnCipher(K1)
    assert c.encrypt(None) is None
    assert c.decrypt(None) is None
    assert c.decrypt(c.encrypt("")) == ""


def test_no_key_is_a_passthrough() -> None:
    c = ColumnCipher(None)
    assert c.active is False
    assert c.encrypt("x") == "x"
    assert c.decrypt("x") == "x"


def test_plaintext_row_reads_back_after_a_key_is_added() -> None:
    # a value written before RF_DATA_ENCRYPTION_KEY existed has no enc: prefix
    assert ColumnCipher(K1).decrypt("legacy clear value") == "legacy clear value"


def test_reading_encrypted_data_without_a_key_raises() -> None:
    token = ColumnCipher(K1).encrypt("secret")
    with pytest.raises(RuntimeError, match="RF_DATA_ENCRYPTION_KEY"):
        ColumnCipher(None).decrypt(token)


def test_key_rotation_old_decrypts_new_encrypts() -> None:
    old = ColumnCipher(K1)
    token = old.encrypt("rotate me")

    rotated = ColumnCipher(f"{K2},{K1}")  # new first, old still accepted
    assert rotated.decrypt(token) == "rotate me"  # old key still reads

    fresh = rotated.rotate(token)
    assert fresh is not None and fresh != token
    assert ColumnCipher(K2).decrypt(fresh) == "rotate me"  # now readable with new alone
    assert old.rotate is not None  # sanity


def test_rotate_is_a_noop_when_already_current() -> None:
    c = ColumnCipher(f"{K2},{K1}")
    token = c.encrypt("already current")
    assert c.rotate(token) is None


def test_rotate_brings_plaintext_under_the_key() -> None:
    c = ColumnCipher(K1)
    fresh = c.rotate("legacy clear")
    assert fresh is not None and fresh.startswith("enc:")
    assert c.decrypt(fresh) == "legacy clear"
    assert ColumnCipher(None).rotate("legacy clear") is None  # nothing to do without a key
