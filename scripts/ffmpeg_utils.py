"""Wrappers finos sobre ffmpeg/ffprobe com erros explícitos."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Sequence

from config import SUBPROCESS_TIMEOUT_S


class FFmpegError(RuntimeError):
    """Falha em uma chamada de ffmpeg/ffprobe."""


def require_binary(name: str, install_hint: str) -> str:
    path = shutil.which(name)
    if not path:
        raise FFmpegError(f"'{name}' não encontrado no PATH. Instale com: {install_hint}")
    return path


def require_ffmpeg() -> None:
    require_binary("ffmpeg", "brew install ffmpeg (macOS) / apt-get install ffmpeg (Linux)")
    require_binary("ffprobe", "brew install ffmpeg (macOS) / apt-get install ffmpeg (Linux)")


def run(command: Sequence[str], *, capture_stderr: bool = False) -> str:
    """Executa um comando e devolve stdout (ou stderr quando pedido)."""
    try:
        result = subprocess.run(
            list(command),
            check=False,
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_S,
        )
    except FileNotFoundError as error:
        raise FFmpegError(f"Binário ausente: {command[0]}") from error
    except subprocess.TimeoutExpired as error:
        raise FFmpegError(f"Timeout ao executar: {' '.join(command[:4])}...") from error

    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "").strip().splitlines()[-8:]
        raise FFmpegError(
            f"Comando falhou ({result.returncode}): {' '.join(command[:6])}...\n"
            + "\n".join(tail)
        )
    return result.stderr if capture_stderr else result.stdout


def run_capturing_stderr(command: Sequence[str]) -> str:
    """Alguns filtros (silencedetect) reportam em stderr mesmo em sucesso."""
    try:
        result = subprocess.run(
            list(command),
            check=False,
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_S,
        )
    except FileNotFoundError as error:
        raise FFmpegError(f"Binário ausente: {command[0]}") from error
    except subprocess.TimeoutExpired as error:
        raise FFmpegError(f"Timeout ao executar: {' '.join(command[:4])}...") from error

    if result.returncode != 0:
        tail = (result.stderr or "").strip().splitlines()[-8:]
        raise FFmpegError(f"ffmpeg falhou ({result.returncode}):\n" + "\n".join(tail))
    return result.stderr or ""


def probe_duration_s(media_path: Path) -> float:
    output = run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "json", str(media_path),
        ]
    )
    try:
        payload = json.loads(output)
        return float(payload["format"]["duration"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        raise FFmpegError(f"Não foi possível ler a duração de {media_path.name}") from error


def probe_dimensions(media_path: Path) -> tuple[int, int]:
    """Largura e altura do vídeo já com a rotação aplicada."""
    output = run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=p=0", str(media_path),
        ]
    ).strip().splitlines()[0]
    try:
        width, height = (int(v) for v in output.split(",")[:2])
    except ValueError as error:
        raise FFmpegError(f"Não foi possível ler as dimensões de {media_path.name}") from error
    return width, height


def probe_fps(media_path: Path) -> float:
    """Quadros por segundo do vídeo. O zoompan precisa disso ou altera a cadência."""
    output = run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=r_frame_rate",
            "-of", "csv=p=0", str(media_path),
        ]
    ).strip()
    try:
        numerator, _, denominator = output.partition("/")
        return float(numerator) / float(denominator or 1)
    except (ValueError, ZeroDivisionError) as error:
        raise FFmpegError(f"Não foi possível ler o fps de {media_path.name}") from error


def has_video_stream(media_path: Path) -> bool:
    output = run(
        [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=codec_type",
            "-of", "csv=p=0", str(media_path),
        ]
    )
    return "video" in output


def available_encoders() -> frozenset[str]:
    """Encoders compilados no ffmpeg local (para detectar aceleração de hardware)."""
    output = run(["ffmpeg", "-hide_banner", "-encoders"], capture_stderr=False)
    names = {
        line.split()[1]
        for line in output.splitlines()
        if line.startswith(" ") and len(line.split()) > 1
    }
    return frozenset(names)


def format_timecode(seconds: float) -> str:
    """Segundos -> HH:MM:SS (ou MM:SS quando abaixo de uma hora)."""
    total = max(0, int(round(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"
