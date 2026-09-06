"""Geração de legendas .srt por clip, a partir da transcrição já existente.

Os tempos precisam ser remapeados: o clip começa em outro instante do vídeo e pode ter
silêncios removidos no meio, o que desloca tudo que vem depois de cada corte.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from transcribe import Utterance

MAX_CHARS_PER_LINE = 40
MAX_LINES = 2
MAX_CHARS_PER_ENTRY = MAX_CHARS_PER_LINE * MAX_LINES
MIN_ENTRY_DURATION_S = 0.7
SUBTITLE_EXTENSION = ".srt"

# Palavras que não podem ficar penduradas no fim de uma legenda: quem lê fica esperando
# o complemento que só aparece no próximo cartão ("...para aquilo que a" / "gente quer viver").
FUNCTION_WORDS = frozenset(
    "a o as os um uma uns umas de da do das dos em na no nas nos para pra por pelo pela "
    "com que se e ou mas meu minha seu sua ao aos à às num numa".split()
)


@dataclass(frozen=True)
class SubtitleEntry:
    start: float
    end: float
    text: str


def write_srt(entries: Sequence[SubtitleEntry], path: Path) -> Path:
    blocks = [
        f"{position}\n{_srt_timestamp(e.start)} --> {_srt_timestamp(e.end)}\n{e.text}\n"
        for position, e in enumerate(entries, start=1)
    ]
    path.write_text("\n".join(blocks), encoding="utf-8")
    return path


def build_entries(
    utterances: Sequence[Utterance], keep_ranges: Sequence[tuple[float, float]]
) -> tuple[SubtitleEntry, ...]:
    """Converte as falas do trecho em legendas com tempo relativo ao clip."""
    entries: list[SubtitleEntry] = []

    for utterance in utterances:
        start = map_to_clip_time(utterance.start, keep_ranges)
        end = map_to_clip_time(utterance.end, keep_ranges)
        if start is None or end is None or end <= start:
            continue
        entries.extend(_split_long_utterance(utterance.text, start, end))

    return tuple(entries)


def map_to_clip_time(
    absolute_time: float, keep_ranges: Sequence[tuple[float, float]]
) -> float | None:
    """Tempo do vídeo original -> tempo dentro do clip renderizado.

    Devolve None quando o instante está fora do clip. Instantes que caem em um trecho
    removido colapsam para a borda do corte, que é onde eles passam a soar.
    """
    elapsed = 0.0
    for start, end in keep_ranges:
        if absolute_time < start:
            return elapsed
        if absolute_time <= end:
            return elapsed + (absolute_time - start)
        elapsed += end - start
    return None


def _split_long_utterance(text: str, start: float, end: float) -> tuple[SubtitleEntry, ...]:
    """Quebra falas longas em legendas legíveis, dividindo o tempo por comprimento."""
    cleaned = " ".join(text.split())
    if not cleaned:
        return ()

    chunks = _chunk_text(cleaned, MAX_CHARS_PER_ENTRY)
    total_chars = sum(len(c) for c in chunks) or 1
    duration = end - start

    entries: list[SubtitleEntry] = []
    cursor = start
    for chunk in chunks:
        share = duration * (len(chunk) / total_chars)
        chunk_end = min(end, cursor + max(MIN_ENTRY_DURATION_S, share))
        if chunk_end > cursor:
            entries.append(SubtitleEntry(cursor, chunk_end, _wrap_lines(chunk)))
        cursor = chunk_end

    return tuple(entries)


def _chunk_text(text: str, limit: int) -> tuple[str, ...]:
    """Divide o texto em cartões de legenda sem deixar palavra funcional pendurada."""
    chunks = _split_by_limit(text.split(), limit)
    return tuple(" ".join(words) for words in _push_dangling_words(chunks) if words)


def _split_by_limit(words: list[str], limit: int) -> list[list[str]]:
    chunks: list[list[str]] = []
    current: list[str] = []
    length = 0

    for word in words:
        extra = len(word) + (1 if current else 0)
        if current and length + extra > limit:
            chunks.append(current)
            current, length = [word], len(word)
            continue
        current.append(word)
        length += extra

    if current:
        chunks.append(current)
    return chunks


def _push_dangling_words(chunks: list[list[str]]) -> list[list[str]]:
    """Empurra para o cartão seguinte as palavras funcionais presas no fim de um cartão."""
    adjusted = [list(chunk) for chunk in chunks]
    for position in range(len(adjusted) - 1):
        while len(adjusted[position]) > 1 and _is_function_word(adjusted[position][-1]):
            adjusted[position + 1].insert(0, adjusted[position].pop())
    return adjusted


def _is_function_word(word: str) -> bool:
    return word.strip(".,;:!?…\"'()").lower() in FUNCTION_WORDS


def _wrap_lines(text: str) -> str:
    """Distribui o texto em até duas linhas equilibradas."""
    if len(text) <= MAX_CHARS_PER_LINE:
        return text

    words = text.split()
    split_at = _balanced_split_point(words)
    first = " ".join(words[:split_at])
    second = " ".join(words[split_at:])
    return f"{first}\n{second}" if second else first


def _balanced_split_point(words: list[str]) -> int:
    """Ponto de quebra mais próximo do meio que não deixe palavra funcional pendurada.

    Busca nos dois sentidos: recuar sempre deixaria a primeira linha curta demais quando
    há várias palavras funcionais seguidas ("para a gente quando a gente...").
    """
    middle = len(words) // 2
    for offset in range(middle + 1):
        for candidate in (middle - offset, middle + offset):
            if 1 <= candidate < len(words) and not _is_function_word(words[candidate - 1]):
                return candidate
    return middle


def _srt_timestamp(seconds: float) -> str:
    total_ms = max(0, int(round(seconds * 1000)))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"
