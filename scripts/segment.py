"""Constrói clips a partir das pausas naturais e da transcrição."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from config import (
    HOOK_MARKERS,
    SILENCE_EDGE_PADDING_S,
    IDEAL_CLIP_DURATION_S,
    IDEAL_WORDS_PER_SECOND,
    SCORE_BASE,
    SCORE_MAX,
    SCORE_MIN,
    ClipperConfig,
)
from cutpoints import CutPoint, best_cut_point, build_cut_points
from silence import Silence, silences_within
from trimming import keep_ranges
from transcribe import Utterance

MIN_TEXT_OVERLAP_RATIO = 0.35


@dataclass(frozen=True)
class Clip:
    index: int
    start: float
    end: float
    text: str
    score: float
    removable_silences: tuple[Silence, ...]
    edge_padding_s: float = SILENCE_EDGE_PADDING_S

    @property
    def clip_id(self) -> str:
        return f"clip_{self.index:03d}"

    @property
    def original_duration(self) -> float:
        return self.end - self.start

    @property
    def keep_ranges(self) -> tuple[tuple[float, float], ...]:
        return keep_ranges(self.start, self.end, self.removable_silences, self.edge_padding_s)

    @property
    def final_duration(self) -> float:
        """Soma dos trechos preservados — exatamente o que o ffmpeg concatena."""
        return sum(end - start for start, end in self.keep_ranges)

    @property
    def removed_silence_duration(self) -> float:
        """O que sai de fato: menos que a soma dos silêncios, pois o padding volta."""
        return max(0.0, self.original_duration - self.final_duration)


def build_clips(
    utterances: Sequence[Utterance],
    silences: Sequence[Silence],
    total_duration_s: float,
    config: ClipperConfig,
) -> tuple[Clip, ...]:
    """Fatia o vídeo nas pausas longas e devolve os clips válidos, ordenados por score."""
    boundaries = _split_boundaries(silences, total_duration_s, config)
    windows = _windows_from_boundaries(boundaries, total_duration_s)
    cut_points = build_cut_points(utterances, silences)

    sized: list[tuple[float, float]] = []
    for start, end in windows:
        sized.extend(_enforce_max_duration(start, end, cut_points, config))

    clips: list[Clip] = []
    for start, end in sized:
        duration = end - start
        if duration < config.min_clip_duration_s:
            continue
        text = text_for_window(utterances, start, end)
        if not text:
            continue
        removable = silences_within(
            silences, start, end, config.silence_removal_threshold_s
        )
        clips.append(
            Clip(
                index=0,  # reindexado após a ordenação
                start=start,
                end=end,
                text=text,
                score=score_clip(text, duration),
                removable_silences=removable,
                edge_padding_s=config.silence_edge_padding_s,
            )
        )

    ranked = sorted(clips, key=lambda c: (-c.score, c.start))[: config.max_clips]
    chronological = sorted(ranked, key=lambda c: c.start)
    return tuple(
        Clip(
            index=position,
            start=clip.start,
            end=clip.end,
            text=clip.text,
            score=clip.score,
            removable_silences=clip.removable_silences,
            edge_padding_s=clip.edge_padding_s,
        )
        for position, clip in enumerate(chronological, start=1)
    )


def _split_boundaries(
    silences: Sequence[Silence], total_duration_s: float, config: ClipperConfig
) -> tuple[Silence, ...]:
    return tuple(
        s
        for s in silences
        if s.duration >= config.silence_split_threshold_s
        and 0.0 < s.start < total_duration_s
    )


def _windows_from_boundaries(
    boundaries: Sequence[Silence], total_duration_s: float
) -> tuple[tuple[float, float], ...]:
    """Regiões faladas entre as pausas longas."""
    windows: list[tuple[float, float]] = []
    cursor = 0.0
    for boundary in boundaries:
        if boundary.start > cursor:
            windows.append((cursor, boundary.start))
        cursor = boundary.end
    if total_duration_s > cursor:
        windows.append((cursor, total_duration_s))
    return tuple(windows)


def _enforce_max_duration(
    start: float, end: float, cut_points: Sequence[CutPoint], config: ClipperConfig
) -> tuple[tuple[float, float], ...]:
    """Subdivide janelas longas no melhor fim de frase; corta duro só em último caso."""
    if end - start <= config.max_clip_duration_s:
        return ((start, end),)

    earliest = start + config.min_clip_duration_s
    latest = min(start + config.max_clip_duration_s, end - config.min_clip_duration_s)
    pivot = best_cut_point(cut_points, earliest, latest) if latest > earliest else None

    cut = pivot.time if pivot else start + config.max_clip_duration_s
    return ((start, cut),) + _enforce_max_duration(cut, end, cut_points, config)


def text_for_window(utterances: Sequence[Utterance], start: float, end: float) -> str:
    """Junta falas cuja maior parte cai dentro da janela."""
    parts: list[str] = []
    for utterance in utterances:
        span = utterance.end - utterance.start
        if span <= 0:
            continue
        overlap = min(utterance.end, end) - max(utterance.start, start)
        if overlap / span >= MIN_TEXT_OVERLAP_RATIO:
            parts.append(utterance.text)
    return " ".join(parts).strip()


def score_clip(text: str, duration_s: float) -> float:
    """Heurística determinística de relevância (0–10).

    Combina densidade de fala, proximidade da duração ideal e marcadores de gancho.
    """
    words = len(text.split())
    if words == 0 or duration_s <= 0:
        return SCORE_MIN

    density = words / duration_s
    density_penalty = abs(density - IDEAL_WORDS_PER_SECOND) * 1.2
    duration_penalty = abs(duration_s - IDEAL_CLIP_DURATION_S) / IDEAL_CLIP_DURATION_S * 1.5

    lowered = text.lower()
    hook_bonus = min(2.0, sum(0.5 for marker in HOOK_MARKERS if marker in lowered))
    length_bonus = min(1.5, words / 60.0)

    raw = SCORE_BASE + hook_bonus + length_bonus - density_penalty - duration_penalty
    return round(min(SCORE_MAX, max(SCORE_MIN, raw)), 1)
