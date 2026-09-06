"""Cache em disco da transcrição — a etapa cara do pipeline.

Transcrever 69 min de áudio leva ~40 min. Sem cache, cada ajuste de parâmetro de
corte (que leva segundos) pagaria esse custo de novo. O cache vive no diretório de
saída, não no workdir temporário, para sobreviver entre execuções.
"""

from __future__ import annotations

import json
from pathlib import Path

from transcribe import Transcript, Utterance, Word

CACHE_FILENAME = "transcript.json"
CACHE_VERSION = 2  # v2 guarda também os tempos por palavra
# v1 continua utilizável quando não se precisa de tempo por palavra: descartá-lo
# custaria uma re-transcrição inteira só por causa do número da versão.
SUPPORTED_VERSIONS = (1, 2)


def cache_path(output_dir: Path) -> Path:
    return output_dir / CACHE_FILENAME


def load(
    path: Path, *, language: str, model: str, require_words: bool = False
) -> Transcript | None:
    """Devolve a transcrição salva quando compatível; None quando ausente ou obsoleta."""
    if not path.exists():
        return None

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    version = payload.get("cache_version")
    if version not in SUPPORTED_VERSIONS:
        return None
    if payload.get("language") != language or payload.get("model") != model:
        return None

    utterances = tuple(
        Utterance(
            float(u["start"]),
            float(u["end"]),
            str(u["text"]),
            tuple(
                Word(float(w["start"]), float(w["end"]), str(w["text"]))
                for w in u.get("words", ())
            ),
        )
        for u in payload.get("utterances", [])
        if str(u.get("text", "")).strip()
    )
    if not utterances:
        return None
    if require_words and not any(u.words for u in utterances):
        return None  # cache antigo, sem tempos por palavra

    return Transcript(
        engine=str(payload.get("engine", "cache")),
        language=language,
        utterances=utterances,
    )


def save(path: Path, transcript: Transcript, *, model: str) -> None:
    payload = {
        "cache_version": CACHE_VERSION,
        "engine": transcript.engine,
        "language": transcript.language,
        "model": model,
        "utterances": [
            {
                "start": round(u.start, 3),
                "end": round(u.end, 3),
                "text": u.text,
                "words": [
                    {"start": round(w.start, 3), "end": round(w.end, 3), "text": w.text}
                    for w in u.words
                ],
            }
            for u in transcript.utterances
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
