from pathlib import Path
from typing import Self

import yaml
from pydantic import BaseModel, Field, model_validator

KNOWN_SIGNAL_IDS = frozenset(
    {
        "AUTH_CLAIM",
        "VERIF_INVERT",
        "RAIL_UNUSUAL",
        "REMOTE_ACCESS",
        "URGENCY",
        "SECRECY",
        "CALLBACK_SUPPRESS",
        "EMOTION_LEVER",
        "SCRIPT_RIGIDITY",
        "ESCALATION",
        "REFUSE_SECRETS",
        "OFFER_CALLBACK",
        "NO_ACTION_ASKED",
        "BRANCH_REFERRAL",
    }
)

KNOWN_COMBO_IDS = frozenset({"COMBO_CRITICAL", "COMBO_ISOLATION", "COMBO_CLASSIC"})


class Metadata(BaseModel):
    tenant: str = "default"
    version: int = 1
    parent: int | None = None
    author: str = "unknown"
    signed_by: str | None = None


class Thresholds(BaseModel):
    watch: float
    alert: float
    intervene: float
    sustain_turns: int = 2
    decay_half_life_s: float = 90.0
    watch_margin: float = 10.0
    watch_quiet_s: float = 45.0
    alert_margin: float = 15.0
    alert_quiet_s: float = 30.0


class SignalSpec(BaseModel):
    weight: float
    extra_terms: list[str] = Field(default_factory=list)


class ComboSpec(BaseModel):
    bonus: float
    window_s: float


class JudgeConfig(BaseModel):
    enabled: bool = True
    model: str = "ring-judge-2026-08"
    max_adjustment: int = 30
    trigger_score: float = 35.0
    max_calls_per_session: int = 12


class SemanticConfig(BaseModel):
    """Zero-shot NLI extractor (T-1.7). Off by default: the invariant
    suite must stay deterministic and offline."""

    enabled: bool = False
    model: str = "MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-nli-2mil7"
    threshold: float = 0.7
    scale_weight: bool = True


class InterventionsConfig(BaseModel):
    channels: list[str] = Field(default_factory=list)
    cooldown_s: float = 60.0
    quiet_hours: str | None = None


class PolicyPack(BaseModel):
    api_version: str = "ringfence/v1"
    kind: str = "PolicyPack"
    metadata: Metadata = Field(default_factory=Metadata)
    languages: list[str] = Field(default_factory=lambda: ["ar_tn", "fr", "en"])
    thresholds: Thresholds
    signals: dict[str, SignalSpec]
    combos: dict[str, ComboSpec] = Field(default_factory=dict)
    judge: JudgeConfig = Field(default_factory=JudgeConfig)
    semantic: SemanticConfig = Field(default_factory=SemanticConfig)
    interventions: InterventionsConfig = Field(default_factory=InterventionsConfig)

    @model_validator(mode="after")
    def _validate(self) -> Self:
        unknown_signals = set(self.signals) - KNOWN_SIGNAL_IDS
        if unknown_signals:
            raise ValueError(f"unknown signal ids: {sorted(unknown_signals)}")
        unknown_combos = set(self.combos) - KNOWN_COMBO_IDS
        if unknown_combos:
            raise ValueError(f"unknown combo ids: {sorted(unknown_combos)}")
        w, a, i = (
            self.thresholds.watch,
            self.thresholds.alert,
            self.thresholds.intervene,
        )
        if not (w < a < i):
            raise ValueError(
                f"thresholds must satisfy watch < alert < intervene; got {w}, {a}, {i}"
            )
        for combo_id, combo in self.combos.items():
            if combo.window_s < 0:
                raise ValueError(f"combo {combo_id} has negative window {combo.window_s}")
        if self.judge.max_adjustment > 40:
            raise ValueError(f"judge.max_adjustment {self.judge.max_adjustment} > 40")
        return self


def load_pack(path: str | Path) -> PolicyPack:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return PolicyPack.model_validate(data)
