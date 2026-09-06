"""Pontos de corte candidatos, ranqueados por qualidade editorial.

Cortar apenas pela duração do silêncio produz clips que terminam no meio da frase.
A transcrição sabe onde as frases acabam — combinar as duas fontes (pontuação + pausa)
dá cortes que soam intencionais em vez de interrompidos.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from silence import Silence
from transcribe import Utterance

SENTENCE_ENDINGS = (".", "!", "?", "…")
SENTENCE_BONUS = 3.0
MAX_PAUSE_BONUS = 2.0
PAUSE_MATCH_TOLERANCE_S = 0.6


@dataclass(frozen=True)
class CutPoint:
    time: float
    is_sentence_end: bool
    pause_duration: float

    @property
    def quality(self) -> float:
        """Fim de frase vale mais que pausa longa; os dois juntos valem mais ainda."""
        sentence = SENTENCE_BONUS if self.is_sentence_end else 0.0
        return sentence + min(MAX_PAUSE_BONUS, self.pause_duration)


def build_cut_points(
    utterances: Sequence[Utterance], silences: Sequence[Silence]
) -> tuple[CutPoint, ...]:
    """Um candidato por fim de fala, anotado com pontuação e pausa que o segue."""
    points = [
        CutPoint(
            time=utterance.end,
            is_sentence_end=utterance.text.rstrip().endswith(SENTENCE_ENDINGS),
            pause_duration=_pause_after(utterance.end, silences),
        )
        for utterance in utterances
    ]
    return tuple(sorted(points, key=lambda p: p.time))


def _pause_after(time: float, silences: Sequence[Silence]) -> float:
    """Duração do silêncio que começa aproximadamente neste instante."""
    for silence in silences:
        if abs(silence.start - time) <= PAUSE_MATCH_TOLERANCE_S:
            return silence.duration
        if silence.start > time + PAUSE_MATCH_TOLERANCE_S:
            break
    return 0.0


def best_cut_point(
    points: Sequence[CutPoint], earliest: float, latest: float
) -> CutPoint | None:
    """Melhor corte na janela; empate resolvido pelo mais tardio (clip mais longo)."""
    candidates = [p for p in points if earliest <= p.time <= latest]
    if not candidates:
        return None
    return max(candidates, key=lambda p: (p.quality, p.time))
