from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from packages.contracts.transcript import Role

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "corpus" / "fixtures"

Label = Literal["fraud", "benign"]
Language = Literal["en", "fr", "ar_tn"]
FinalState = Literal["CALM", "WATCH", "ALERT", "INTERVENE", "RESOLVED"]


class FixtureTurn(BaseModel):
    t_start: float
    t_end: float
    role: Role
    text: str
    expect_signals: list[str] = []


class Fixture(BaseModel):
    id: str
    language: Language
    label: Label
    scam_family: str
    transfer_request_t: float | None = None
    turns: list[FixtureTurn]
    expect_final_state: FinalState = "CALM"
    expect_alert_before_t: float | None = None


def load_fixture(fixture_id: str) -> Fixture:
    path = FIXTURE_DIR / f"{fixture_id}.json"
    return Fixture.model_validate_json(path.read_text(encoding="utf-8"))


def iter_fixtures(label: str | None = None) -> list[Fixture]:
    fixtures = [load_fixture(p.stem) for p in sorted(FIXTURE_DIR.glob("*.json"))]
    if label is None:
        return fixtures
    return [f for f in fixtures if f.label == label]
