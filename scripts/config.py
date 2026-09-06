"""Parâmetros e configuração imutável do pipeline podcast-clipper."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

# --- Limites de clip -------------------------------------------------------
MIN_CLIP_DURATION_S = 2.0
MAX_CLIP_DURATION_S = 60.0
MAX_CLIPS = 60

# --- Detecção de silêncio --------------------------------------------------
SILENCE_SPLIT_THRESHOLD_S = 2.0      # pausa que marca fronteira de corte
SILENCE_DB_THRESHOLD = -40.0         # nível considerado silêncio
SILENCE_REMOVAL_THRESHOLD_S = 0.8    # pausa removida DENTRO do clip
SILENCE_DETECTION_FLOOR_S = 0.4      # granularidade da detecção (não é critério de corte)
SILENCE_EDGE_PADDING_S = 0.15        # respiro mantido nas bordas do corte

# --- Áudio / transcrição ---------------------------------------------------
AUDIO_SAMPLE_RATE_HZ = 16000
AUDIO_CHANNELS = 1
DEFAULT_LANGUAGE = "pt"
DEFAULT_WHISPER_MODEL = "medium"

# --- Encoding --------------------------------------------------------------
VIDEO_CODEC = "libx264"
VIDEO_ENCODER_AUTO = "auto"
VIDEOTOOLBOX_H264_ENCODER = "h264_videotoolbox"
VIDEOTOOLBOX_BITRATE = "8M"        # VideoToolbox não aceita -q:v; só bitrate alvo
SCALE_SHORT_SIDE = 1080            # lado MENOR do vídeo; 0 desativa o redimensionamento

# --- Paralelismo -----------------------------------------------------------
CPU_FRACTION_PER_JOB = 3           # cada ffmpeg já usa várias threads
MAX_PARALLEL_JOBS = 4
SCALE_DISABLED = 0
VIDEO_CRF = "20"
VIDEO_PRESET = "veryfast"
AUDIO_CODEC = "aac"
AUDIO_BITRATE = "192k"

# --- Entrada ---------------------------------------------------------------
VIDEO_EXTENSIONS = (".mp4", ".mov", ".heic", ".m4v", ".mkv", ".webm", ".avi", ".mts")
SUBPROCESS_TIMEOUT_S = 60 * 60 * 4

# --- Scoring ---------------------------------------------------------------
SCORE_MIN = 0.0
SCORE_MAX = 10.0
SCORE_BASE = 5.0
IDEAL_CLIP_DURATION_S = 35.0
IDEAL_WORDS_PER_SECOND = 2.6

HOOK_MARKERS = (
    "o segredo", "a verdade", "ninguém", "nunca", "sempre", "porque",
    "o problema", "a chave", "descobri", "aprendi", "errei", "o erro",
    "primeira vez", "dica", "importante", "o ponto",
)


@dataclass(frozen=True)
class ClipperConfig:
    """Configuração de um run. Nunca mutar — use `with_overrides`."""

    source: Path
    output_dir: Path
    min_clip_duration_s: float = MIN_CLIP_DURATION_S
    max_clip_duration_s: float = MAX_CLIP_DURATION_S
    silence_split_threshold_s: float = SILENCE_SPLIT_THRESHOLD_S
    silence_db_threshold: float = SILENCE_DB_THRESHOLD
    silence_removal_threshold_s: float = SILENCE_REMOVAL_THRESHOLD_S
    silence_edge_padding_s: float = SILENCE_EDGE_PADDING_S
    scale_short_side: int = SCALE_SHORT_SIDE
    video_encoder: str = VIDEO_ENCODER_AUTO
    subtitles: bool = True
    jobs: int = 0  # 0 = derivar dos núcleos disponíveis
    skip_existing: bool = False
    adjustments: Path | None = None
    burn_subtitles: bool = False
    subtitle_font: str | None = None
    word_timestamps: bool = False
    subtitle_style: str = "block"
    signature: Path | None = None
    zoom_amplitude: float = 0.12
    zoom_transition_s: float = 4.0
    zoom_hold_s: float = 0.0  # 0 = escolher pela duração do clip
    subtitle_size_ratio: float = 0.0726
    signature_size_ratio: float = 0.52
    speakers_file: Path | None = None
    persona: str = ""
    max_clips: int = MAX_CLIPS
    language: str = DEFAULT_LANGUAGE
    engine: str = "auto"
    whisper_model: str = DEFAULT_WHISPER_MODEL
    keep_workdir: bool = False
    dry_run: bool = False

    def with_overrides(self, **changes) -> "ClipperConfig":
        return replace(self, **changes)

    def validate(self) -> None:
        """Falha rápido e com mensagem clara em configuração inválida."""
        if not self.source.exists():
            raise ValueError(f"Vídeo de origem não encontrado: {self.source}")
        if self.source.suffix.lower() not in VIDEO_EXTENSIONS:
            supported = ", ".join(VIDEO_EXTENSIONS)
            raise ValueError(
                f"Extensão não suportada '{self.source.suffix}'. Suportadas: {supported}"
            )
        if self.min_clip_duration_s <= 0:
            raise ValueError("min_clip_duration deve ser maior que zero")
        if self.max_clip_duration_s < self.min_clip_duration_s:
            raise ValueError("max_clip_duration deve ser >= min_clip_duration")
        if self.silence_split_threshold_s <= 0:
            raise ValueError("silence_threshold deve ser maior que zero")
        if self.silence_db_threshold >= 0:
            raise ValueError("silence_db_threshold deve ser negativo (ex.: -40)")
        if self.max_clips <= 0:
            raise ValueError("max_clips deve ser maior que zero")
        if self.scale_short_side < 0:
            raise ValueError("scale_short_side deve ser >= 0 (0 mantém a resolução original)")
        if self.scale_short_side and self.scale_short_side % 2 != 0:
            raise ValueError("scale_short_side deve ser par (exigência do H.264)")
        if not 0.0 <= self.zoom_amplitude <= 0.3:
            raise ValueError("zoom deve ficar entre 0 e 0.3 (30%); acima disso enjoa")
        if self.zoom_transition_s <= 0:
            raise ValueError("zoom-transition deve ser maior que zero")
        if self.zoom_hold_s < 0:
            raise ValueError("zoom-hold não pode ser negativo")
        if bool(self.speakers_file) != bool(self.persona):
            raise ValueError("--speakers e --persona andam juntos: informe os dois ou nenhum")
        if self.speakers_file and not self.speakers_file.exists():
            raise ValueError(f"Arquivo de locutores não encontrado: {self.speakers_file}")
        if self.signature and not self.signature.exists():
            raise ValueError(f"Imagem de assinatura não encontrada: {self.signature}")
        if self.adjustments and not self.adjustments.exists():
            raise ValueError(f"Arquivo de ajustes não encontrado: {self.adjustments}")
        if self.jobs < 0:
            raise ValueError("jobs deve ser >= 0 (0 deriva dos núcleos disponíveis)")
