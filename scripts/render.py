"""Renderiza os clips finais: remove silêncios internos, redimensiona e codifica."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from config import (
    AUDIO_BITRATE,
    AUDIO_CODEC,
    SCALE_DISABLED,
    VIDEO_CODEC,
    VIDEO_CRF,
    VIDEO_ENCODER_AUTO,
    VIDEO_PRESET,
    VIDEOTOOLBOX_H264_ENCODER,
    VIDEOTOOLBOX_BITRATE,
    ClipperConfig,
)
from ffmpeg_utils import available_encoders, run
from segment import Clip
from trimming import keep_ranges

CONCAT_VIDEO_LABEL = "[outv]"
CONCAT_AUDIO_LABEL = "[outa]"
SCALED_VIDEO_LABEL = "[scaled]"


def render_clip(source: Path, clip: Clip, output_path: Path, config: ClipperConfig) -> Path:
    """Gera o MP4 do clip. Concatena os trechos falados quando há silêncio a remover."""
    keep_segments = keep_ranges(
        clip.start, clip.end, clip.removable_silences, config.silence_edge_padding_s
    )

    if len(keep_segments) <= 1:
        _render_single_range(source, clip.start, clip.end, output_path, config)
        return output_path

    _render_concatenated(source, keep_segments, output_path, config)
    return output_path


# --- encoding --------------------------------------------------------------


def resolve_encoder(config: ClipperConfig) -> str:
    """Escolhe o encoder de vídeo, validando que o pedido existe no ffmpeg local.

    `auto` resolve para software (libx264) de propósito: medindo 4K HEVC -> 1080p, o
    VideoToolbox não foi mais rápido (o gargalo é a DECODIFICAÇÃO do HEVC, que ambos
    pagam) e, por só aceitar bitrate alvo em vez de CRF, gerou arquivos ~10x maiores.
    """
    if config.video_encoder == VIDEO_ENCODER_AUTO:
        return VIDEO_CODEC

    if config.video_encoder not in available_encoders():
        raise ValueError(
            f"Encoder '{config.video_encoder}' não existe neste ffmpeg. "
            "Veja as opções com: ffmpeg -encoders"
        )
    return config.video_encoder


def encoder_args(encoder: str) -> tuple[str, ...]:
    """Flags de qualidade específicas do encoder escolhido."""
    if encoder == VIDEOTOOLBOX_H264_ENCODER:
        # Este encoder rejeita -q:v ("qscale not available"); só aceita bitrate alvo.
        return ("-c:v", encoder, "-b:v", VIDEOTOOLBOX_BITRATE)
    return ("-c:v", encoder, "-crf", VIDEO_CRF, "-preset", VIDEO_PRESET)


def scale_filter(scale_short_side: int) -> str:
    """Downscale pelo LADO MENOR, preservando proporção e orientação.

    Escalar pela altura quebra vídeo vertical: um 2160x3840 viraria 608x1080 em vez
    de 1080x1920, jogando fora resolução. `force_original_aspect_ratio=increase` sobre
    uma caixa quadrada faz o menor lado atingir o alvo, seja o vídeo retrato ou
    paisagem. Os `min(...)` impedem upscale de material menor que o alvo.
    """
    if scale_short_side == SCALE_DISABLED:
        return ""
    return (
        f"scale=w=min({scale_short_side}\\,iw):h=min({scale_short_side}\\,ih)"
        ":force_original_aspect_ratio=increase:force_divisible_by=2"
    )


def _output_args(config: ClipperConfig) -> tuple[str, ...]:
    encoder = resolve_encoder(config)
    return (
        *encoder_args(encoder),
        "-pix_fmt", "yuv420p",
        "-c:a", AUDIO_CODEC, "-b:a", AUDIO_BITRATE,
        "-movflags", "+faststart",
    )


# --- render ----------------------------------------------------------------


def _input_seek_args(start: float, end: float) -> tuple[str, ...]:
    """Seek no input: limita a decodificação ao trecho, em vez de varrer o arquivo.

    Sem isso, um clip aos 65 min de um vídeo de 69 min forçaria o ffmpeg a decodificar
    os 65 min anteriores. `-t` (duração) é usado em vez de `-to` porque, combinado com
    `-ss` de input, seu significado é inequívoco.
    """
    return ("-ss", f"{start:.3f}", "-t", f"{max(0.0, end - start):.3f}")


def _concat_seek_args(start: float, end: float) -> tuple[str, ...]:
    """Seek para o caminho concatenado, preservando os timestamps originais.

    `-ss` posiciona no keyframe anterior ao ponto pedido, então os PTS que chegam ao
    filtro NÃO começam em zero. Sem `-copyts`, um `trim` relativo captura desde esse
    keyframe e o clip sai mais longo (medimos 1,7s de excesso médio, até 4,6s).
    Com `-copyts` os PTS seguem absolutos e o `trim` usa os tempos reais do vídeo.
    """
    return ("-copyts", *_input_seek_args(start, end))


def _render_single_range(
    source: Path, start: float, end: float, output_path: Path, config: ClipperConfig
) -> None:
    scale = scale_filter(config.scale_short_side)
    filter_args = ("-vf", scale) if scale else ()

    run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            *_input_seek_args(start, end),
            "-i", str(source),
            *filter_args,
            *_output_args(config),
            str(output_path),
        ]
    )


def _render_concatenated(
    source: Path,
    ranges: Sequence[tuple[float, float]],
    output_path: Path,
    config: ClipperConfig,
) -> None:
    filter_complex, video_label = build_filter_complex(ranges, config.scale_short_side)

    run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            *_concat_seek_args(ranges[0][0], ranges[-1][1]),
            "-i", str(source),
            "-filter_complex", filter_complex,
            "-map", video_label, "-map", CONCAT_AUDIO_LABEL,
            *_output_args(config),
            str(output_path),
        ]
    )


def build_filter_complex(
    ranges: Sequence[tuple[float, float]], scale_short_side: int
) -> tuple[str, str]:
    """Monta o grafo de filtros e devolve (grafo, label do vídeo de saída).

    Os tempos são ABSOLUTOS (do vídeo original): o render usa `-copyts`, que preserva
    os PTS de origem. O downscale roda uma única vez, depois do concat.
    """
    steps: list[str] = []
    video_labels: list[str] = []
    audio_labels: list[str] = []

    for position, (start, end) in enumerate(ranges):
        video_label = f"[v{position}]"
        audio_label = f"[a{position}]"
        steps.append(
            f"[0:v]trim=start={start:.3f}:end={end:.3f},setpts=PTS-STARTPTS{video_label}"
        )
        steps.append(
            f"[0:a]atrim=start={start:.3f}:end={end:.3f},asetpts=PTS-STARTPTS{audio_label}"
        )
        video_labels.append(video_label)
        audio_labels.append(audio_label)

    concat_inputs = "".join(f"{v}{a}" for v, a in zip(video_labels, audio_labels))
    steps.append(
        f"{concat_inputs}concat=n={len(ranges)}:v=1:a=1{CONCAT_VIDEO_LABEL}{CONCAT_AUDIO_LABEL}"
    )

    scale = scale_filter(scale_short_side)
    if not scale:
        return ";".join(steps), CONCAT_VIDEO_LABEL

    steps.append(f"{CONCAT_VIDEO_LABEL}{scale}{SCALED_VIDEO_LABEL}")
    return ";".join(steps), SCALED_VIDEO_LABEL
