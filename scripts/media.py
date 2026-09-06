"""Normalização de vídeo de entrada e extração de áudio para transcrição."""

from __future__ import annotations

from pathlib import Path

from config import (
    AUDIO_CHANNELS,
    AUDIO_SAMPLE_RATE_HZ,
    VIDEO_CODEC,
    VIDEO_CRF,
    VIDEO_PRESET,
)
from ffmpeg_utils import FFmpegError, has_video_stream, run


NORMALIZED_CONTAINERS = (".mp4", ".m4v", ".mov")


def normalize_video(source: Path, workdir: Path) -> Path:
    """Converte a origem para MP4 quando necessário (HEIC/HEVC/MKV/etc.).

    Retorna o caminho utilizável no restante do pipeline. Nunca sobrescreve a origem.
    """
    if source.suffix.lower() in NORMALIZED_CONTAINERS and has_video_stream(source):
        return source

    target = workdir / f"{source.stem}_normalized.mp4"
    if target.exists():
        return target

    try:
        run(
            [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-i", str(source),
                "-c:v", VIDEO_CODEC, "-crf", VIDEO_CRF, "-preset", VIDEO_PRESET,
                "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "192k",
                "-movflags", "+faststart",
                str(target),
            ]
        )
    except FFmpegError as error:
        raise FFmpegError(
            f"Falha ao converter '{source.name}' para MP4. "
            "Se for .HEIC de foto (não vídeo), o arquivo não contém stream de vídeo."
        ) from error

    if not has_video_stream(target):
        raise FFmpegError(f"'{source.name}' não contém stream de vídeo utilizável")
    return target


def extract_audio(video_path: Path, workdir: Path) -> Path:
    """Extrai WAV mono 16 kHz — formato esperado por Whisper/Meetily."""
    target = workdir / f"{video_path.stem}_audio.wav"
    if target.exists():
        return target

    run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(video_path),
            "-vn",
            "-ac", str(AUDIO_CHANNELS),
            "-ar", str(AUDIO_SAMPLE_RATE_HZ),
            "-c:a", "pcm_s16le",
            str(target),
        ]
    )
    return target
