"""Ingress configuration, and one specific way it gets pasted wrong.

Both CPaaS ingresses need two unrelated secrets:

* the **provider's** credential, which verifies the inbound webhook
  (Twilio's auth token, Telnyx's Ed25519 public key);
* a **RingFence** API key, which authenticates us *outbound* to
  ``/ws/capture``.

These were both called ``RF_<PROVIDER>_API_KEY``, which reads as "the API
key for <provider>" and is the opposite of what it holds.  Anyone setting
this up is holding a provider API key at that exact moment -- the portal
page that shows it is the page you were just on -- so the name has to say
whose key it wants, and the loader has to catch the paste when it doesn't.
"""

from __future__ import annotations

import pytest

from apps.telnyx.app import Config as TelnyxConfig
from apps.twilio.app import Config as TwilioConfig
from packages.ingress.capture_uplink import read_gateway_key


def test_the_gateway_key_is_named_for_whose_key_it_is() -> None:
    cfg = TelnyxConfig.from_env(
        {
            "RF_TELNYX_PUBLIC_KEY": "cHVibGlj",
            "RF_TELNYX_GATEWAY_KEY": "rf-tenant-key",
        }
    )
    assert cfg.api_key == "rf-tenant-key"
    assert cfg.public_key == "cHVibGlj"


def test_twilio_reads_the_same_pair_under_its_own_prefix() -> None:
    cfg = TwilioConfig.from_env(
        {"RF_TWILIO_AUTH_TOKEN": "twilio-token", "RF_TWILIO_GATEWAY_KEY": "rf-tenant-key"}
    )
    assert cfg.auth_token == "twilio-token"
    assert cfg.api_key == "rf-tenant-key"


def test_a_telnyx_api_key_pasted_into_the_gateway_slot_is_refused() -> None:
    """Telnyx API keys are ``KEY`` + hex + ``_`` + secret, and a RingFence
    key never looks like that.  Catching it here turns a puzzling 401 from
    our own gateway -- mid-demo, with a live call up -- into a message that
    names both variables."""
    with pytest.raises(ValueError, match="RF_TELNYX_GATEWAY_KEY"):
        read_gateway_key(
            {"RF_TELNYX_GATEWAY_KEY": "KEY01A0900345987C6128C46AE934F5FCD8_cPgBOZmx4u6T6d3"},
            "RF_TELNYX_GATEWAY_KEY",
        )


def test_a_twilio_auth_token_pasted_into_the_gateway_slot_is_refused() -> None:
    """Twilio auth tokens are 32 lowercase hex.  Same failure, same fix."""
    with pytest.raises(ValueError, match="RF_TWILIO_GATEWAY_KEY"):
        read_gateway_key({"RF_TWILIO_GATEWAY_KEY": "a" * 32}, "RF_TWILIO_GATEWAY_KEY")


def test_an_sid_pasted_into_the_gateway_slot_is_refused() -> None:
    with pytest.raises(ValueError, match="RF_TWILIO_GATEWAY_KEY"):
        read_gateway_key({"RF_TWILIO_GATEWAY_KEY": "AC" + "0" * 32}, "RF_TWILIO_GATEWAY_KEY")


@pytest.mark.parametrize(
    "value",
    [
        "rf-tenant-key",
        "kBqPz3xhs2VxJ0nE7LcQmA",  # token_urlsafe(16), what /api-keys issues
        "",  # absent is a different error, raised by __main__, not here
    ],
)
def test_a_ringfence_key_passes_through_untouched(value: str) -> None:
    assert read_gateway_key({"RF_TELNYX_GATEWAY_KEY": value}, "RF_TELNYX_GATEWAY_KEY") == value


def test_the_guard_does_not_fire_on_a_key_that_merely_starts_with_key() -> None:
    """``key-...`` is a plausible RingFence key name and must not trip the
    Telnyx check, which wants ``KEY`` followed by uppercase hex."""
    assert read_gateway_key({"K": "key-for-acme-corp"}, "K") == "key-for-acme-corp"
