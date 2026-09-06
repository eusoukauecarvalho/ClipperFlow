"""Configuração persistente do painel: estilo, zoom, assinatura e agendamento.

Antes desta peça, esses valores eram só flags de CLI que eu (Claude) digitava a cada
sessão — nada sobrevivia entre execuções. Este módulo dá a eles um lar em disco, com
um valor padrão sensato caso o arquivo ainda não exista.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent.parent / "panel_config.json"

VALID_SUBTITLE_STYLES = ("block", "karaoke", "pop")


@dataclass
class PanelConfig:
    subtitle_style: str = "pop"
    subtitle_size_ratio: float = 0.0726
    signature_path: str = ""
    signature_size_ratio: float = 0.52
    zoom_enabled: bool = True
    zoom_amplitude: float = 0.12
    zoom_transition_s: float = 4.0
    zoom_hold_auto: bool = True  # True: pausa deriva da duração do clip (30s->5s, senão 20s)
    zoom_hold_s: float = 10.0  # usado só quando zoom_hold_auto é False
    schedule_slots: list[str] = None  # type: ignore[assignment]
    youtube_credential_path: str = ""
    youtube_token_path: str = ""

    def __post_init__(self) -> None:
        if self.schedule_slots is None:
            self.schedule_slots = ["06:00", "13:00", "18:00", "02:00"]

    def validate(self) -> list[str]:
        """Problemas encontrados (vazio = tudo certo). Não levanta — o painel reporta."""
        issues = []
        if self.subtitle_style not in VALID_SUBTITLE_STYLES:
            issues.append(f"subtitle_style inválido: {self.subtitle_style}")
        if not 0.02 <= self.subtitle_size_ratio <= 0.15:
            issues.append("subtitle_size_ratio fora da faixa razoável (0.02–0.15)")
        if not 0.2 <= self.signature_size_ratio <= 1.0:
            issues.append("signature_size_ratio fora da faixa razoável (0.2–1.0)")
        if not 0.0 <= self.zoom_amplitude <= 0.3:
            issues.append("zoom_amplitude deve ficar entre 0 e 0.3")
        if self.zoom_transition_s <= 0:
            issues.append("zoom_transition_s deve ser maior que zero")
        if self.zoom_hold_s < 0:
            issues.append("zoom_hold_s não pode ser negativo")
        if self.signature_path and not Path(self.signature_path).exists():
            issues.append(f"imagem de assinatura não encontrada: {self.signature_path}")
        for slot in self.schedule_slots or []:
            parts = slot.split(":")
            if len(parts) != 2 or not all(p.isdigit() for p in parts):
                issues.append(f"horário inválido: {slot!r} (use HH:MM)")
        return issues


def load() -> PanelConfig:
    if not CONFIG_PATH.exists():
        return PanelConfig()
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return PanelConfig()
    defaults = asdict(PanelConfig())
    return PanelConfig(**{**defaults, **{k: v for k, v in raw.items() if k in defaults}})


def save(config: PanelConfig) -> None:
    CONFIG_PATH.write_text(
        json.dumps(asdict(config), ensure_ascii=False, indent=2), encoding="utf-8"
    )
