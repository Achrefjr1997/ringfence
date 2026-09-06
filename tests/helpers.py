from packages.contracts.transcript import AttributedTurn, Role, Turn, Word


def make_turn(
    text: str,
    role: Role = "CALLER",
    t_start: float = 0.0,
    t_end: float = 5.0,
    words: tuple[Word, ...] = (),
) -> AttributedTurn:
    return AttributedTurn(
        turn=Turn(
            session_id="s",
            leg_id="l",
            turn_order=0,
            text=text,
            is_final=True,
            is_formatted=True,
            t_start=t_start,
            t_end=t_end,
            words=words,
            confidence=1.0,
        ),
        role=role,
        role_confidence=1.0,
    )


def caller_turn(
    text: str,
    t_start: float = 0.0,
    t_end: float = 5.0,
    words: tuple[Word, ...] = (),
) -> AttributedTurn:
    return make_turn(text, "CALLER", t_start, t_end, words)


def callee_turn(
    text: str,
    t_start: float = 0.0,
    t_end: float = 5.0,
    words: tuple[Word, ...] = (),
) -> AttributedTurn:
    return make_turn(text, "CALLEE", t_start, t_end, words)
