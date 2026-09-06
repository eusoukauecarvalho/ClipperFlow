"""Filtro de persona: mantém no clip só a fala de quem está na câmera.

Cada arquivo de podcast é a câmera de UMA pessoa. Os turnos dos outros locutores são
tratados como trechos removíveis — exatamente o mecanismo da remoção de silêncio —
então o render concatena apenas os momentos em que a persona fala.

Interjeições curtas do outro locutor ("Uhum", "Caramba") ficam: removê-las criaria
um corte seco a cada reação e o clip viraria uma metralhadora de jump cuts.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from silence import Silence

MIN_REMOVABLE_TURN_S = 1.6   # turno alheio mais curto que isso fica (reação, não fala)
TURN_EDGE_PADDING_S = 0.12   # respiro nas bordas para não engolir o ataque da fala


class PersonaError(ValueError):
    """speakers.json inválido ou persona inexistente."""


@dataclass(frozen=True)
class SpeakerMap:
    turns: tuple[tuple[float, float, str], ...]
    totals_s: dict[str, float]
    samples: dict[str, list[str]]

    @property
    def speakers(self) -> tuple[str, ...]:
        return tuple(sorted(self.totals_s))


def load_speakers(path: Path) -> SpeakerMap:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PersonaError(f"Não foi possível ler {path}: {error}") from error

    turns = tuple(
        (float(t["start"]), float(t["end"]), str(t["speaker"]))
        for t in payload.get("turns", [])
        if float(t["end"]) > float(t["start"])
    )
    if not turns:
        raise PersonaError("speakers.json sem turnos — rode diarize.py primeiro")

    return SpeakerMap(
        turns=turns,
        totals_s={k: float(v) for k, v in payload.get("totals_s", {}).items()},
        samples=payload.get("samples", {}),
    )


def foreign_turns(
    speakers: SpeakerMap,
    persona: str,
    window_start: float,
    window_end: float,
    *,
    min_turn_s: float = MIN_REMOVABLE_TURN_S,
) -> tuple[Silence, ...]:
    """Turnos de OUTROS locutores dentro da janela, prontos para remoção.

    Devolvidos como `Silence` porque o render já sabe recortar silêncios — o filtro de
    persona pega esse mecanismo emprestado.
    """
    if persona not in speakers.speakers:
        raise PersonaError(
            f"Persona '{persona}' não existe. Locutores: {', '.join(speakers.speakers)}"
        )

    # Primeiro juntar turnos vizinhos, depois padding e filtro de tamanho — na ordem
    # inversa, o padding alarga o vão entre turnos e impede o merge.
    raw: list[Silence] = []
    for start, end, speaker in speakers.turns:
        if speaker == persona:
            continue
        clipped_start = max(start, window_start)
        clipped_end = min(end, window_end)
        if clipped_end > clipped_start:
            raw.append(Silence(clipped_start, clipped_end))

    cuts: list[Silence] = []
    for merged in _merge_adjacent(raw):
        padded_start = merged.start + TURN_EDGE_PADDING_S
        padded_end = merged.end - TURN_EDGE_PADDING_S
        if padded_end - padded_start >= min_turn_s:
            cuts.append(Silence(padded_start, padded_end))
    return tuple(cuts)


def _merge_adjacent(cuts: Sequence[Silence], gap_s: float = 0.4) -> tuple[Silence, ...]:
    """Cortes vizinhos viram um só — menos emendas no vídeo final."""
    merged: list[Silence] = []
    for cut in sorted(cuts, key=lambda c: c.start):
        if merged and cut.start - merged[-1].end <= gap_s:
            merged[-1] = Silence(merged[-1].start, max(merged[-1].end, cut.end))
            continue
        merged.append(cut)
    return tuple(merged)


def window_breakdown(
    speakers: SpeakerMap, window_start: float, window_end: float
) -> dict[str, float]:
    """Segundos falados por cada locutor dentro da janela."""
    totals: dict[str, float] = {}
    for start, end, speaker in speakers.turns:
        overlap = min(end, window_end) - max(start, window_start)
        if overlap > 0:
            totals[speaker] = totals.get(speaker, 0.0) + overlap
    return totals


# --- whitelist: manter só o que é comprovadamente da persona ------------------

KEEP_JOIN_GAP_S = 0.5     # falas da persona separadas por menos que isso não geram emenda
KEEP_EDGE_TRIM_S = 0.10   # come a borda que encosta em outro locutor (mata o vazamento)


def persona_keep_spans(
    speakers: SpeakerMap, persona: str, window_start: float, window_end: float
) -> tuple[tuple[float, float], ...]:
    """Trechos da janela onde SÓ a persona fala — tudo mais cai.

    Whitelist em vez de blacklist: com fronteiras imperfeitas, "remover o outro" deixa
    vazar o que foi mal rotulado; "manter só a persona" derruba também o incerto. As
    bordas que encostam em outro locutor são aparadas para engolir o vazamento residual.
    """
    if persona not in speakers.speakers:
        raise PersonaError(
            f"Persona '{persona}' não existe. Locutores: {', '.join(speakers.speakers)}"
        )

    spans: list[list[float]] = []
    for start, end, speaker in speakers.turns:
        if speaker != persona:
            continue
        s = max(start, window_start)
        e = min(end, window_end)
        if e <= s:
            continue
        if spans and s - spans[-1][1] <= KEEP_JOIN_GAP_S:
            spans[-1][1] = max(spans[-1][1], e)
            continue
        spans.append([s, e])

    trimmed: list[tuple[float, float]] = []
    for s, e in spans:
        if s > window_start:
            s += KEEP_EDGE_TRIM_S
        if e < window_end:
            e -= KEEP_EDGE_TRIM_S
        if e - s >= 0.3:
            trimmed.append((s, e))
    return tuple(trimmed)


def removable_from_keep_spans(
    keep: Sequence[tuple[float, float]], window_start: float, window_end: float
) -> tuple[Silence, ...]:
    """Complemento dos trechos mantidos — no formato que o render já entende."""
    cuts: list[Silence] = []
    cursor = window_start
    for s, e in keep:
        if s - cursor > 0.05:
            cuts.append(Silence(cursor, s))
        cursor = max(cursor, e)
    if window_end - cursor > 0.05:
        cuts.append(Silence(cursor, window_end))
    return tuple(cuts)
