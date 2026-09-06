"""Detecção de silêncios via filtro `silencedetect` do ffmpeg."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from ffmpeg_utils import run_capturing_stderr

SILENCE_START_PATTERN = re.compile(r"silence_start:\s*(-?[\d.]+)")
SILENCE_END_PATTERN = re.compile(r"silence_end:\s*(-?[\d.]+)")


@dataclass(frozen=True)
class Silence:
    start: float
    end: float

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def overlaps(self, start: float, end: float) -> bool:
        return self.start < end and self.end > start


def detect_silences(
    audio_path: Path,
    *,
    db_threshold: float,
    min_duration_s: float,
    total_duration_s: float,
) -> tuple[Silence, ...]:
    """Retorna os silêncios >= `min_duration_s` encontrados no áudio."""
    stderr = run_capturing_stderr(
        [
            "ffmpeg", "-hide_banner", "-nostats",
            "-i", str(audio_path),
            "-af", f"silencedetect=noise={db_threshold}dB:d={min_duration_s}",
            "-f", "null", "-",
        ]
    )
    return _parse_silencedetect(stderr, total_duration_s)


def _parse_silencedetect(stderr: str, total_duration_s: float) -> tuple[Silence, ...]:
    silences: list[Silence] = []
    pending_start: float | None = None

    for line in stderr.splitlines():
        start_match = SILENCE_START_PATTERN.search(line)
        if start_match:
            pending_start = max(0.0, float(start_match.group(1)))
            continue

        end_match = SILENCE_END_PATTERN.search(line)
        if end_match and pending_start is not None:
            end = min(total_duration_s, float(end_match.group(1)))
            if end > pending_start:
                silences.append(Silence(pending_start, end))
            pending_start = None

    # Silêncio aberto até o fim do arquivo.
    if pending_start is not None and total_duration_s > pending_start:
        silences.append(Silence(pending_start, total_duration_s))

    return tuple(silences)


def silences_within(
    silences: Sequence[Silence], start: float, end: float, min_duration_s: float
) -> tuple[Silence, ...]:
    """Silêncios internos a uma janela, recortados aos limites dela."""
    clipped: list[Silence] = []
    for silence in silences:
        if not silence.overlaps(start, end):
            continue
        window = Silence(max(silence.start, start), min(silence.end, end))
        if window.duration >= min_duration_s:
            clipped.append(window)
    return tuple(clipped)
