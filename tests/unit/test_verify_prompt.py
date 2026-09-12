"""prompt.py: what the agent is allowed to know before it speaks to a stranger."""

from __future__ import annotations

from packages.verify.directory import Institution
from packages.verify.prompt import build_prompt

_INST = Institution.model_validate(
    {
        "id": "amazon",
        "display_name": "Amazon",
        "desk_id": "demo_desk",
        "line_label": "account security",
        "aliases": ["amazon"],
    }
)


def test_with_no_amount_the_prompt_contains_no_digit_at_all() -> None:
    """The worst failure mode is the agent stating a fabricated figure to a
    real institution. With no amount extracted, there must be no digit
    anywhere it could anchor on -- not even an innocent "30 seconds"."""
    system_prompt, greeting = build_prompt(_INST, None)
    assert not any(ch.isdigit() for ch in system_prompt + greeting)


def test_with_no_amount_the_agent_is_told_not_to_invent_one() -> None:
    system_prompt, _ = build_prompt(_INST, None)
    assert "never invent" in system_prompt.lower()


def test_an_extracted_amount_is_passed_through_verbatim() -> None:
    system_prompt, _ = build_prompt(_INST, "$500")
    assert "$500" in system_prompt


def test_the_greeting_always_self_identifies_as_automated() -> None:
    for amount in (None, "$500"):
        _, greeting = build_prompt(_INST, amount)
        assert "automated" in greeting.lower()


def test_the_prompt_names_the_institution_and_its_desk() -> None:
    system_prompt, greeting = build_prompt(_INST, None)
    assert "Amazon" in system_prompt and "account security" in system_prompt
    assert "Amazon" in greeting


def test_the_prompt_instructs_the_report_tool() -> None:
    system_prompt, _ = build_prompt(_INST, None)
    assert "report_verification" in system_prompt


def test_the_agent_is_told_it_knows_nothing_about_the_customer() -> None:
    system_prompt, _ = build_prompt(_INST, None)
    assert "never ask for or offer personal" in system_prompt.lower()
