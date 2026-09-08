"""Generate a ~5-minute two-party scam call as one 16 kHz mono WAV.

CALLER (fake bank/Amazon fraud dept) is band-limited to the telephone band
so RingFence's acoustic role classifier can separate it from the CALLEE
(full-band, room mic). Voices: Windows SAPI 'Zira'; the caller is
pitch-shifted down for human distinctness.

    python gen_call.py out.wav
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import butter, sosfiltfilt, resample_poly

RATE = 16_000
GAP = 0.45  # base silence between turns
TURN_GAP = 0.9  # extra pause when the speaker changes

# (speaker, text). CALLER = the fraudster, CALLEE = the target.
SCRIPT: list[tuple[str, str]] = [
    (
        "CALLER",
        "Hello, good afternoon. Am I speaking with the account holder? This is Daniel calling from the Amazon account security team.",
    ),
    ("CALLEE", "Yes, this is me. What is this about?"),
    (
        "CALLER",
        "I'm calling because our fraud systems flagged some unusual activity on your account in the last hour. Do you have a moment? This is fairly urgent.",
    ),
    ("CALLEE", "Okay. What kind of activity?"),
    (
        "CALLER",
        "We're seeing an attempted purchase of eight hundred and forty-nine dollars for electronics, shipping to an address in another state. Did you make that order?",
    ),
    ("CALLEE", "No. No, I definitely did not order anything like that."),
    (
        "CALLER",
        "Right, that's what we suspected. It looks like someone has gained access to your account. I need to act quickly to stop the charge before it settles, which could be as soon as today.",
    ),
    ("CALLEE", "Oh no. What do I need to do?"),
    (
        "CALLER",
        "First, the most important thing: please do not hang up this call, and do not log in to your account while we're speaking, because the attacker may still be in there and could see what you do.",
    ),
    ("CALLEE", "Alright, I won't touch it."),
    (
        "CALLER",
        "Also, do not call the number on the back of your card just yet. Their fraud line is completely overloaded right now and you'll be on hold for an hour. I can handle all of this for you directly from here.",
    ),
    ("CALLEE", "Okay, if you think that's faster."),
    (
        "CALLER",
        "It is. Now, to confirm I'm speaking with the real account holder and not the attacker, I need to verify your identity. Can you confirm the full name on the account and your date of birth?",
    ),
    ("CALLEE", "It's under my name, and my birthday is the fourth of June, nineteen fifty-one."),
    (
        "CALLER",
        "Thank you. Now, we've just sent a six digit security code to your phone by text message. This is how we know it's really you. Can you read that code back to me?",
    ),
    (
        "CALLEE",
        "Hold on. Um, it says three, nine, one... it says do not share this code with anyone.",
    ),
    (
        "CALLER",
        "That message is automated, it's fine, I'm on the security team, I'm the one who generated it. Please continue, the last three digits?",
    ),
    ("CALLEE", "I'm not sure I should be reading this out."),
    (
        "CALLER",
        "I understand your caution, that's good, that means you're careful. But right now every minute matters. If we don't verify you in the next few minutes the charge goes through and the money leaves your account. The rest of the code, please.",
    ),
    ("CALLEE", "Okay. Four, two, seven."),
    (
        "CALLER",
        "Perfect, you're verified. Thank you for your patience. Now here's what we're going to do to protect your funds. I'm going to move your available balance into a secure holding account that the attacker cannot reach, and then move it back once your card is reissued.",
    ),
    ("CALLEE", "You're going to move my money?"),
    (
        "CALLER",
        "Only temporarily, and only into an account controlled by our fraud department. It's the standard protective step. But the system won't let me initiate that transfer on a compromised profile. So instead, the fastest way to lock the funds today is with prepaid cards.",
    ),
    ("CALLEE", "Prepaid cards?"),
    (
        "CALLER",
        "Yes. I need you to go to a pharmacy or a supermarket and purchase store gift cards totalling five hundred dollars. Once you have them, you read me the numbers on the back, and that value is held safely in escrow under your case number until this is resolved.",
    ),
    ("CALLEE", "That sounds really strange. Why would my bank want gift cards?"),
    (
        "CALLER",
        "I know it sounds unusual, and normally you'd be right to question it. But this is a fraud containment procedure, not a normal transaction. The gift card network settles instantly, which is the only way to beat the charge that's pending on your account right now.",
    ),
    ("CALLEE", "I don't know about this."),
    (
        "CALLER",
        "Let me be very direct with you, because I don't want you to lose this money. If you hang up now, or if you wait, the eight hundred dollars is gone and there is nothing anyone can do to get it back. I'm trying to help you. Can you get to a store in the next fifteen minutes?",
    ),
    ("CALLEE", "There's a pharmacy just down the road."),
    (
        "CALLER",
        "That's perfect. Take your phone with you and keep me on the line the entire time. Do not tell the cashier what the cards are for, because sometimes they interfere and that delay is exactly what the attacker is counting on.",
    ),
    (
        "CALLEE",
        "Okay. I'm getting my coat. So five hundred in gift cards, and I read you the numbers.",
    ),
    (
        "CALLER",
        "Exactly right. Buy them in one hundred dollar denominations if you can, five cards. As soon as you're at the counter, tell me and I'll stay with you. You're doing the right thing to protect yourself.",
    ),
    ("CALLEE", "Alright. I'm walking out the door now."),
    (
        "CALLER",
        "Good. Stay on the line. Do not call anyone else, do not check your account, and don't worry, once I have those numbers your money is completely safe.",
    ),
    ("CALLEE", "Okay. I'll be at the pharmacy in about five minutes."),
]

PITCH = 0.82  # caller playback-rate for pitch-down; tempo restored after
TELE_LP = 3400.0  # caller lowpass (telephone band)


def synth(text: str, wav_out: Path) -> None:
    ps = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "$s.SelectVoice('Microsoft Zira Desktop'); "
        "$s.Rate = 0; "
        f"$s.SetOutputToWaveFile('{wav_out}'); "
        f"$s.Speak(@'\n{text}\n'@); $s.Dispose()"
    )
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", ps], check=True, capture_output=True
    )


def load_mono16k(path: Path) -> np.ndarray:
    data, sr = sf.read(path, dtype="float64", always_2d=False)
    if data.ndim > 1:
        data = data.mean(axis=1)
    if sr != RATE:
        from math import gcd

        g = gcd(int(sr), RATE)
        data = resample_poly(data, RATE // g, int(sr) // g)
    return data.astype(np.float64)


def caller_fx(x: np.ndarray) -> np.ndarray:
    # pitch down (resample then time-stretch back via simple decimation-ish),
    # then telephone lowpass + mild level trim
    stretched = resample_poly(x, int(1000 * PITCH), 1000)  # slower + lower
    n = int(len(stretched) / PITCH)
    idx = np.linspace(0, len(stretched) - 1, n).astype(int)
    y = stretched[idx]  # back to original duration, pitch stays low
    sos = butter(6, TELE_LP, btype="low", fs=RATE, output="sos")
    y = sosfiltfilt(sos, y)
    return 0.9 * y


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "demo-scam-call.wav")
    tmp = Path(tempfile.mkdtemp())
    segs: list[tuple[str, np.ndarray]] = []
    for i, (who, text) in enumerate(SCRIPT):
        w = tmp / f"{i:02d}.wav"
        synth(text, w)
        a = load_mono16k(w)
        if who == "CALLER":
            a = caller_fx(a)
        # normalise each turn to a consistent peak
        peak = np.max(np.abs(a)) or 1.0
        a = a * (0.62 / peak)
        segs.append((who, a))
        print(f'  turn {i:02d} {who:6} {len(a) / RATE:5.1f}s  "{text[:48]}..."')

    # assemble on a timeline with gaps
    parts: list[np.ndarray] = [np.zeros(int(RATE * 0.6))]
    prev = None
    for who, a in segs:
        if prev is not None:
            g = GAP + (TURN_GAP if who != prev else 0.0)
            parts.append(np.zeros(int(RATE * g)))
        parts.append(a)
        prev = who
    parts.append(np.zeros(int(RATE * 1.0)))
    mix = np.concatenate(parts)
    mix = np.clip(mix + 0.0008 * np.random.standard_normal(len(mix)), -1.0, 1.0)

    sf.write(out, (mix * 32767).astype("<i2"), RATE, subtype="PCM_16")
    print(
        f"\nwrote {out}  {len(mix) / RATE / 60:.1f} min  {out.stat().st_size // 1024} KB  16 kHz mono"
    )


if __name__ == "__main__":
    main()
