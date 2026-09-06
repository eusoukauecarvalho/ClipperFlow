"""Ajustes editoriais manuais sobre os cortes gerados automaticamente.

A montagem automática acerta as fronteiras acústicas, mas não sabe se um trecho é um
pensamento completo. Este módulo aplica correções humanas (ou de um modelo que leu a
transcrição) por cima do resultado: estender, encurtar, absorver o clip seguinte ou
descartar. Todo limite é encaixado na fronteira de fala mais próxima, para que nenhum
ajuste caia no meio de uma frase.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from silence import Silence, silences_within
from transcribe import Utterance

SNAP_TOLERANCE_S = 2.5
DEFAULT_CONTEXT_LEAD_S = 5.0
DEFAULT_CONTEXT_TAIL_S = 3.0


class AdjustmentError(ValueError):
    """Arquivo de ajustes inválido."""


@dataclass(frozen=True)
class ClipAdjustment:
    clip_id: str
    start: float | None = None
    end: float | None = None
    remove: bool = False
    absorb: tuple[str, ...] = ()
    note: str = ""


@dataclass(frozen=True)
class AdjustmentPlan:
    context_lead_s: float
    context_tail_s: float
    clips: dict[str, ClipAdjustment]
    fingerprint: str = ""
    snap: bool = True

    @property
    def removed_ids(self) -> frozenset[str]:
        absorbed = {cid for a in self.clips.values() for cid in a.absorb}
        removed = {a.clip_id for a in self.clips.values() if a.remove}
        return frozenset(absorbed | removed)


def load_plan(path: Path) -> AdjustmentPlan:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AdjustmentError(f"Não foi possível ler os ajustes em {path}: {error}") from error

    entries = payload.get("clips")
    if not isinstance(entries, dict):
        raise AdjustmentError("O arquivo de ajustes precisa de um objeto 'clips'")

    clips = {
        clip_id: ClipAdjustment(
            clip_id=clip_id,
            start=_parse_time(entry.get("start")),
            end=_parse_time(entry.get("end")),
            remove=bool(entry.get("remove", False)),
            absorb=tuple(entry.get("absorb", ())),
            note=str(entry.get("note", "")),
        )
        for clip_id, entry in entries.items()
    }
    return AdjustmentPlan(
        context_lead_s=float(payload.get("context_lead_s", DEFAULT_CONTEXT_LEAD_S)),
        context_tail_s=float(payload.get("context_tail_s", DEFAULT_CONTEXT_TAIL_S)),
        clips=clips,
        fingerprint=str(payload.get("source_fingerprint", "")),
        snap=bool(payload.get("snap", True)),
    )


def fingerprint_clips(clips: Sequence) -> str:
    """Identidade do conjunto de cortes automáticos sobre o qual os ajustes foram feitos.

    Os ajustes são indexados por `clip_id`, que é posicional. Se a transcrição mudar
    (outro modelo, word_timestamps, outro idioma), as fronteiras automáticas mudam e o
    mesmo id passa a apontar para outro trecho — os ajustes cairiam em conteúdo errado
    sem nenhum erro visível. Esta impressão digital transforma isso em falha explícita.
    """
    digest = hashlib.sha256()
    for clip in clips:
        digest.update(f"{clip.clip_id}:{clip.start:.2f}:{clip.end:.2f};".encode())
    return digest.hexdigest()[:16]


def verify_fingerprint(plan: AdjustmentPlan, clips: Sequence) -> None:
    """Falha se os ajustes foram escritos para outro conjunto de cortes."""
    if not plan.fingerprint:
        return  # arquivo antigo, sem impressão digital — segue sem verificar

    current = fingerprint_clips(clips)
    if current != plan.fingerprint:
        raise AdjustmentError(
            "Os ajustes foram feitos para outro conjunto de cortes "
            f"(esperado {plan.fingerprint}, atual {current}).\n"
            "A transcrição mudou e as fronteiras automáticas se deslocaram, então os "
            "ids apontam para trechos diferentes.\n"
            "Refaça os ajustes sobre os cortes atuais, ou fixe start/end explícitos "
            "em todos eles."
        )


def _parse_time(value) -> float | None:
    """Aceita segundos (90.5) ou timecode ('01:30', '01:02:03')."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)

    parts = str(value).split(":")
    try:
        numbers = [float(p) for p in parts]
    except ValueError as error:
        raise AdjustmentError(f"Tempo inválido: {value!r}") from error

    if len(numbers) == 1:
        return numbers[0]
    if len(numbers) == 2:
        return numbers[0] * 60 + numbers[1]
    if len(numbers) == 3:
        return numbers[0] * 3600 + numbers[1] * 60 + numbers[2]
    raise AdjustmentError(f"Tempo inválido: {value!r}")


def snap_to_speech(
    time: float, utterances: Sequence[Utterance], *, to_start: bool
) -> float:
    """Encaixa um instante na fronteira de fala mais próxima, dentro da tolerância.

    Evita que um ajuste manual em números redondos caia no meio de uma palavra.
    """
    boundaries = [u.start for u in utterances] if to_start else [u.end for u in utterances]
    if not boundaries:
        return time

    nearest = min(boundaries, key=lambda b: abs(b - time))
    return nearest if abs(nearest - time) <= SNAP_TOLERANCE_S else time


def resolve_window(
    adjustment: ClipAdjustment,
    current: tuple[float, float],
    absorbed_ends: Sequence[float],
    utterances: Sequence[Utterance],
    plan: AdjustmentPlan,
) -> tuple[float, float]:
    """Janela final do clip: ajuste explícito, absorções e margem mínima de contexto."""
    start = adjustment.start if adjustment.start is not None else current[0]
    end = adjustment.end if adjustment.end is not None else current[1]

    if absorbed_ends:
        end = max(end, *absorbed_ends)

    # Margem mínima só quando o ajuste não definiu o limite explicitamente.
    if adjustment.start is None:
        start -= plan.context_lead_s
    if adjustment.end is None and not absorbed_ends:
        end += plan.context_tail_s

    # O encaixe na fala existe para corrigir tempo escrito à mão em número redondo.
    # Num plano congelado os tempos JÁ são finais: reencaixá-los contra outra
    # transcrição os deslocaria até SNAP_TOLERANCE_S e mudaria o corte.
    if plan.snap:
        start = snap_to_speech(start, utterances, to_start=True)
        end = snap_to_speech(end, utterances, to_start=False)

    start = max(0.0, start)
    return (start, max(start + 1.0, end))


def rebuild_silences(
    start: float, end: float, silences: Sequence[Silence], removal_threshold_s: float
) -> tuple[Silence, ...]:
    """Silêncios removíveis dentro da janela nova."""
    return silences_within(silences, start, end, removal_threshold_s)
