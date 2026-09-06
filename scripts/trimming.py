"""Quais trechos de um clip sobrevivem à remoção de silêncio.

Fonte de verdade única: o render concatena exatamente estes intervalos, o metadata
reporta a duração a partir deles e as legendas mapeiam seus tempos com eles. Quando
essa conta vivia em dois lugares, o metadata divergia do arquivo gerado.
"""

from __future__ import annotations

from typing import Sequence

from silence import Silence

MIN_KEEP_SEGMENT_S = 0.05


def keep_ranges(
    start: float, end: float, silences: Sequence[Silence], edge_padding_s: float
) -> tuple[tuple[float, float], ...]:
    """Trechos preservados entre `start` e `end` após remover os silêncios longos."""
    cuts = effective_cuts(silences, edge_padding_s)
    if not cuts:
        return ((start, end),)

    ranges: list[tuple[float, float]] = []
    cursor = start
    for cut in cuts:
        if cut.start - cursor >= MIN_KEEP_SEGMENT_S:
            ranges.append((cursor, cut.start))
        cursor = max(cursor, cut.end)
    if end - cursor >= MIN_KEEP_SEGMENT_S:
        ranges.append((cursor, end))

    return tuple(ranges) if ranges else ((start, end),)


def effective_cuts(silences: Sequence[Silence], edge_padding_s: float) -> tuple[Silence, ...]:
    """Encolhe cada silêncio pelo padding — o respiro que fica nas bordas do corte.

    É por isso que o tempo removido é MENOR que a soma dos silêncios: cada corte
    devolve 2 x padding ao clip.
    """
    cuts: list[Silence] = []
    for silence in sorted(silences, key=lambda s: s.start):
        start = silence.start + edge_padding_s
        end = silence.end - edge_padding_s
        if end - start >= MIN_KEEP_SEGMENT_S:
            cuts.append(Silence(start, end))
    return tuple(cuts)
