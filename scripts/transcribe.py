"""Transcrição com Meetily (whisper.cpp server) e fallbacks locais.

Ordem de tentativa quando `engine="auto"`:
  1. meetily      — servidor whisper.cpp local (Meetily backend)
  2. faster-whisper (pip install faster-whisper)
  3. openai-whisper (pip install openai-whisper)
  4. whisper-cli  — binário do whisper.cpp no PATH

Todos os engines devolvem a mesma estrutura: tupla de `Utterance`.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from ffmpeg_utils import run

MEETILY_SERVER_URL = os.environ.get("MEETILY_SERVER_URL", "http://localhost:8178")
MEETILY_INFERENCE_PATH = "/inference"
MEETILY_TIMEOUT_S = "1800"
PROGRESS_STEP_RATIO = 0.05  # reporta a cada 5% do áudio consumido


class TranscriptionError(RuntimeError):
    """Nenhum engine de transcrição conseguiu processar o áudio."""


@dataclass(frozen=True)
class Word:
    """Palavra isolada com o instante exato em que é falada (para karaokê)."""

    start: float
    end: float
    text: str


@dataclass(frozen=True)
class Utterance:
    start: float
    end: float
    text: str
    words: tuple[Word, ...] = ()


@dataclass(frozen=True)
class Transcript:
    engine: str
    language: str
    utterances: tuple[Utterance, ...]

    @property
    def full_text(self) -> str:
        return " ".join(u.text for u in self.utterances).strip()


def transcribe(
    audio_path: Path, *, language: str, engine: str, model: str, word_timestamps: bool = False
) -> Transcript:
    engines = _engine_chain(engine)
    failures: list[str] = []

    for name in engines:
        handler = _ENGINE_HANDLERS[name]
        try:
            utterances = handler(audio_path, language, model, word_timestamps)
        except Exception as error:  # noqa: BLE001 — fallback deliberado entre engines
            failures.append(f"  - {name}: {error}")
            continue
        if utterances:
            return Transcript(engine=name, language=language, utterances=utterances)
        failures.append(f"  - {name}: retornou transcrição vazia")

    raise TranscriptionError(
        "Nenhum engine de transcrição disponível.\n"
        + "\n".join(failures)
        + "\n\nInstale um deles: pip install faster-whisper  |  brew install whisper-cpp"
    )


def _engine_chain(engine: str) -> tuple[str, ...]:
    if engine == "auto":
        return ("meetily", "faster-whisper", "openai-whisper", "whisper-cli")
    if engine not in _ENGINE_HANDLERS:
        available = ", ".join(("auto", *_ENGINE_HANDLERS))
        raise ValueError(f"Engine desconhecido '{engine}'. Opções: {available}")
    return (engine,)


def _transcribe_meetily(
    audio_path: Path, language: str, _model: str, _words: bool = False
) -> tuple[Utterance, ...]:
    if not shutil.which("curl"):
        raise RuntimeError("curl ausente — necessário para falar com o servidor Meetily")

    url = f"{MEETILY_SERVER_URL.rstrip('/')}{MEETILY_INFERENCE_PATH}"
    output = run(
        [
            "curl", "-sS", "--fail", "--max-time", MEETILY_TIMEOUT_S,
            url,
            "-F", f"file=@{audio_path}",
            "-F", f"language={language}",
            "-F", "response_format=verbose_json",
            "-F", "temperature=0.0",
        ]
    )
    payload = json.loads(output)
    segments = payload.get("segments") or payload.get("transcription") or []
    return _normalize_server_segments(segments)


def _normalize_server_segments(segments: list) -> tuple[Utterance, ...]:
    utterances: list[Utterance] = []
    for segment in segments:
        text = (segment.get("text") or "").strip()
        if not text:
            continue
        offsets = segment.get("offsets")
        if offsets:  # whisper.cpp server usa milissegundos
            start = float(offsets.get("from", 0)) / 1000.0
            end = float(offsets.get("to", 0)) / 1000.0
        else:
            start = float(segment.get("start", 0.0))
            end = float(segment.get("end", start))
        if end > start:
            utterances.append(Utterance(start, end, text))
    return tuple(utterances)


def _transcribe_faster_whisper(
    audio_path: Path, language: str, model: str, word_timestamps: bool = False
) -> tuple[Utterance, ...]:
    from faster_whisper import WhisperModel  # import tardio: dependência opcional

    whisper = WhisperModel(model, device="auto", compute_type="int8")
    segments, info = whisper.transcribe(
        str(audio_path), language=language, vad_filter=False, word_timestamps=word_timestamps
    )
    return _collect_with_progress(segments, float(getattr(info, "duration", 0.0) or 0.0))


def _collect_with_progress(segments, total_duration_s: float) -> tuple[Utterance, ...]:
    """Consome o gerador de segmentos reportando progresso.

    faster-whisper só produz segmentos sob demanda; sem este log a transcrição de um
    arquivo longo fica silenciosa por dezenas de minutos, sem como estimar o que falta.
    """
    utterances: list[Utterance] = []
    step = total_duration_s * PROGRESS_STEP_RATIO
    next_report = step

    for segment in segments:
        text = (segment.text or "").strip()
        if text:
            utterances.append(
                Utterance(
                    float(segment.start), float(segment.end), text, _extract_words(segment)
                )
            )

        if step > 0 and segment.end >= next_report:
            percent = min(100.0, segment.end / total_duration_s * 100.0)
            print(
                f"[podcast-clipper] transcrição {percent:.0f}% "
                f"({segment.end:.0f}s/{total_duration_s:.0f}s)",
                flush=True,
            )
            next_report = segment.end + step

    return tuple(utterances)


def _extract_words(segment) -> tuple[Word, ...]:
    """Palavras do segmento, quando a transcrição foi feita com word_timestamps."""
    raw = getattr(segment, "words", None) or ()
    return tuple(
        Word(float(w.start), float(w.end), w.word.strip())
        for w in raw
        if getattr(w, "word", "").strip() and w.end > w.start
    )


def _transcribe_openai_whisper(
    audio_path: Path, language: str, model: str, _words: bool = False
) -> tuple[Utterance, ...]:
    import whisper  # import tardio: dependência opcional

    loaded = whisper.load_model(model)
    result = loaded.transcribe(str(audio_path), language=language, verbose=False)
    return tuple(
        Utterance(float(s["start"]), float(s["end"]), str(s["text"]).strip())
        for s in result.get("segments", [])
        if str(s.get("text", "")).strip()
    )


def _transcribe_whisper_cli(
    audio_path: Path, language: str, model: str, _words: bool = False
) -> tuple[Utterance, ...]:
    binary = shutil.which("whisper-cli") or shutil.which("whisper-cpp")
    if not binary:
        raise RuntimeError("whisper-cli/whisper-cpp ausente no PATH")

    model_path = os.environ.get("WHISPER_CPP_MODEL")
    if not model_path or not Path(model_path).exists():
        raise RuntimeError(
            "Defina WHISPER_CPP_MODEL com o caminho do .bin (ex.: ggml-medium.bin)"
        )

    output_prefix = audio_path.with_suffix("")
    run(
        [
            binary, "-m", model_path, "-f", str(audio_path),
            "-l", language, "-oj", "-of", str(output_prefix), "-np",
        ]
    )
    json_path = Path(f"{output_prefix}.json")
    if not json_path.exists():
        raise RuntimeError(f"whisper-cli não gerou {json_path.name}")

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    return _normalize_server_segments(payload.get("transcription", []))


_ENGINE_HANDLERS = {
    "meetily": _transcribe_meetily,
    "faster-whisper": _transcribe_faster_whisper,
    "openai-whisper": _transcribe_openai_whisper,
    "whisper-cli": _transcribe_whisper_cli,
}
