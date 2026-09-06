"""Dispara o pipeline (clipper.py) a partir do vídeo bruto e acompanha o progresso.

Transcrever e renderizar levam de minutos a dezenas de minutos — isso roda em segundo
plano (subprocess.Popen, não .run) com log em arquivo, e o painel consulta o progresso
por polling em vez de travar esperando.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
JOBS_DIR = Path(__file__).resolve().parent / ".cache" / "jobs"
JOBS_DIR.mkdir(parents=True, exist_ok=True)

SEARCH_ROOTS = (Path.home() / "Documents", Path.home() / "Movies", Path.home() / "Desktop")
VIDEO_EXTENSIONS = (".mov", ".mp4", ".m4v", ".mkv")
MIN_SOURCE_SIZE_MB = 50  # descarta clipes pequenos; um bruto de podcast é grande
EXCLUDED_PARTS = ("clips", "legendados", ".work", ".cache")

# processos em segundo plano vivem em memória: o painel só faz sentido com o
# servidor aberto, então não precisa sobreviver a um restart.
_active_jobs: dict[str, dict] = {}


def find_source_videos() -> list[dict]:
    """Vídeos brutos candidatos: grandes, fora de pastas que já são saída de um corte."""
    seen: set[Path] = set()
    candidates: list[dict] = []
    for root in SEARCH_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.suffix.lower() not in VIDEO_EXTENSIONS:
                continue
            if any(part in EXCLUDED_PARTS for part in path.parts):
                continue
            if path in seen:
                continue
            try:
                size_mb = path.stat().st_size / (1024 * 1024)
            except OSError:
                continue
            if size_mb < MIN_SOURCE_SIZE_MB:
                continue
            seen.add(path)
            candidates.append(
                {"path": str(path), "name": path.name, "size_mb": round(size_mb)}
            )
    return sorted(candidates, key=lambda c: -c["size_mb"])[:30]


def suggest_output_dir(video_path: str) -> str:
    source = Path(video_path)
    stem = source.stem.replace(" ", "_")
    return str(source.parent / f"CORTES_{stem}")


STATE_FILES = ("metadata.json", "fila_upload.json", "titulos.json", "renomeacao.json")


def backup_existing_state(output_dir: str) -> list[str]:
    """Copia o estado atual antes de qualquer dry-run/render tocar a pasta.

    Bug real (2026-09): um dry-run de teste apontado para um projeto já finalizado
    sobrescreveu o metadata.json de 45 clips (com filtro de persona) por uma versão
    crua de 60 — sem isso, essa classe de erro perde dado de verdade, não só re-roda.
    """
    target = Path(output_dir)
    existing = [name for name in STATE_FILES if (target / name).exists()]
    if not existing:
        return []

    backups_dir = target / ".backups"
    backups_dir.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    saved = []
    for name in existing:
        destination = backups_dir / f"{stamp}_{name}"
        shutil.copy2(target / name, destination)
        saved.append(str(destination))
    return saved


def _start_job(command: list[str], kind: str, output_dir: str) -> str:
    backup_existing_state(output_dir)
    job_id = uuid.uuid4().hex[:10]
    log_path = JOBS_DIR / f"{job_id}.log"
    log_file = log_path.open("w", encoding="utf-8")

    process = subprocess.Popen(
        command, stdout=log_file, stderr=subprocess.STDOUT, cwd=str(SCRIPTS_DIR)
    )
    _active_jobs[job_id] = {
        "process": process,
        "log_path": log_path,
        "log_file": log_file,
        "kind": kind,
        "output_dir": output_dir,
    }
    return job_id


def start_dry_run(video_path: str, output_dir: str, options: dict) -> str:
    """Transcreve e propõe cortes, sem renderizar — a etapa de revisão vem antes do render."""
    command = [
        sys.executable, str(SCRIPTS_DIR / "clipper.py"),
        "--source", video_path,
        "--output-dir", output_dir,
        "--dry-run", "--keep-workdir",
        "--language", options.get("language", "pt"),
        "--max-clip-duration", str(options.get("max_clip_duration", 90)),
        "--min-clip-duration", str(options.get("min_clip_duration", 2)),
    ]
    if options.get("word_timestamps"):
        command += ["--subtitle-style", "pop"]  # liga word_timestamps sozinho
    return _start_job(command, "dry_run", output_dir)


def start_render(output_dir: str, style_config, options: dict) -> str:
    """Renderiza de verdade, reusando a transcrição já em cache do dry-run."""
    video_path = options["video_path"]
    command = [
        sys.executable, str(SCRIPTS_DIR / "clipper.py"),
        "--source", video_path,
        "--output-dir", output_dir,
        "--max-clip-duration", str(options.get("max_clip_duration", 90)),
        "--min-clip-duration", str(options.get("min_clip_duration", 2)),
        "--language", options.get("language", "pt"),
        "--jobs", "3",
        "--burn-subtitles",
        "--subtitle-style", style_config.subtitle_style,
        "--subtitle-size", str(style_config.subtitle_size_ratio),
        "--signature-size", str(style_config.signature_size_ratio),
    ]
    if style_config.signature_path:
        command += ["--signature", style_config.signature_path]
    if style_config.zoom_enabled:
        command += ["--zoom", str(style_config.zoom_amplitude)]
        command += ["--zoom-transition", str(style_config.zoom_transition_s)]
        if not style_config.zoom_hold_auto:
            command += ["--zoom-hold", str(style_config.zoom_hold_s)]
    else:
        command += ["--zoom", "0"]
    return _start_job(command, "render", output_dir)


def get_job_status(job_id: str) -> dict | None:
    job = _active_jobs.get(job_id)
    if not job:
        return None

    process: subprocess.Popen = job["process"]
    running = process.poll() is None
    if not running:
        job["log_file"].close()

    log_text = job["log_path"].read_text(encoding="utf-8", errors="replace")
    return {
        "job_id": job_id,
        "kind": job["kind"],
        "output_dir": job["output_dir"],
        "running": running,
        "returncode": None if running else process.returncode,
        "log_tail": log_text.splitlines()[-60:],
    }
