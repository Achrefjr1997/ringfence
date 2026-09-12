"""The only place text reaches the verification agent.

The model is about to speak, unprompted, to a stranger at an institution. So
this module decides exactly what it knows, and it knows almost nothing:

* the institution's display name and which desk it is calling;
* the amount at stake **if and only if** one was actually extracted.

No customer name, no account digits, no transcript, no session id. And when no
amount was extracted the prompt says nothing about money at all, and tells the
agent not to invent any -- stating a fabricated "$849 charge" to a real
institution is this feature's worst failure mode, and there is a test that the
prompt contains no digit whatsoever in that case.

The greeting is fixed and self-identifies as automated. It is deliberately not
configurable: an operator must not be able to make this pass for a human.
"""

from __future__ import annotations

from packages.verify.directory import Institution


def build_prompt(institution: Institution, amount: str | None) -> tuple[str, str]:
    """``(system_prompt, greeting)``."""
    name = institution.display_name
    desk = institution.line_label

    if amount:
        subject = f"a call a customer received about a charge of {amount.strip()}"
        about_money = f"You may mention the amount, {amount.strip()}, exactly as given."
    else:
        subject = "a call a customer just received"
        about_money = (
            "No amount is known. Do not mention any amount, figure, date, or number, "
            "and never invent one."
        )

    system_prompt = (
        f"You are an automated verification assistant working for RingFence, a fraud "
        f"protection service. You are speaking to the {desk} desk at {name}. A customer "
        f"is on another call right now with someone claiming to be from {name}'s {desk} "
        f"team, and the customer may be about to lose money.\n\n"
        f"Your only goal: find out whether {name} placed {subject}.\n\n"
        f"Rules:\n"
        f"- Ask one clear question. Be brief and polite; the whole exchange should take "
        f"under half a minute.\n"
        f"- {about_money}\n"
        f"- You know nothing about the customer. Never ask for or offer personal "
        f"details, account information, or codes.\n"
        f"- If asked, always say plainly that you are an automated assistant.\n"
        f"- As soon as they give a clear answer, or say they cannot answer, call the "
        f"report_verification tool exactly once. Set verified to true only if they "
        f"confirm {name} placed the call. Quote what they actually said in reason.\n"
        f"- After reporting, thank them in one short sentence and stop talking."
    )
    greeting = (
        f"Hello, this is an automated verification assistant calling on behalf of a "
        f"{name} customer. I have one quick question for the {desk} team."
    )
    return system_prompt, greeting
