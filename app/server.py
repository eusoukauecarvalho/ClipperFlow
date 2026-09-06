#!/usr/bin/env python3
"""Painel local do podcast-clipper: um servidor FastAPI sobre os módulos existentes.

Não reimplementa nada do pipeline — só dá uma tela para o que já existia como
scripts de linha de comando. Rodar com: python3 server.py (abre em localhost:8420).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import panel_config
import processing
import projects

APP_DIR = Path(__file__).resolve().parent
THUMBNAIL_CACHE = APP_DIR / ".cache" / "thumbnails"
THUMBNAIL_CACHE.mkdir(parents=True, exist_ok=True)
SIGNATURE_UPLOAD_DIR = APP_DIR / ".cache" / "signatures"
SIGNATURE_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Podcast Clipper — Painel")


# --- config ------------------------------------------------------------------


class ConfigUpdate(BaseModel):
    subtitle_style: str | None = None
    subtitle_size_ratio: float | None = None
    signature_size_ratio: float | None = None
    zoom_enabled: bool | None = None
    zoom_amplitude: float | None = None
    zoom_transition_s: float | None = None
    zoom_hold_auto: bool | None = None
    zoom_hold_s: float | None = None
    schedule_slots: list[str] | None = None


@app.get("/api/config")
def get_config():
    cfg = panel_config.load()
    return {"config": cfg.__dict__, "issues": cfg.validate()}


@app.put("/api/config")
def update_config(update: ConfigUpdate):
    cfg = panel_config.load()
    for field, value in update.model_dump(exclude_unset=True).items():
        setattr(cfg, field, value)

    issues = cfg.validate()
    if issues:
        raise HTTPException(422, detail={"issues": issues})

    panel_config.save(cfg)
    return {"config": cfg.__dict__, "issues": []}


@app.post("/api/config/signature")
async def upload_signature(file: UploadFile):
    if not file.filename or not file.filename.lower().endswith((".png",)):
        raise HTTPException(400, "Envie um PNG (idealmente com fundo transparente)")

    target = SIGNATURE_UPLOAD_DIR / "signature.png"
    target.write_bytes(await file.read())

    cfg = panel_config.load()
    cfg.signature_path = str(target)
    panel_config.save(cfg)
    return {"signature_path": str(target)}


@app.get("/api/config/signature/preview")
def get_signature_preview():
    cfg = panel_config.load()
    if not cfg.signature_path or not Path(cfg.signature_path).exists():
        raise HTTPException(404, "Nenhuma assinatura configurada")
    return FileResponse(cfg.signature_path, media_type="image/png")


# --- processar um novo vídeo bruto --------------------------------------------


@app.get("/api/source-videos")
def get_source_videos():
    return processing.find_source_videos()


@app.get("/api/source-videos/suggest-output")
def suggest_output(video_path: str):
    return {"output_dir": processing.suggest_output_dir(video_path)}


class DryRunRequest(BaseModel):
    video_path: str
    output_dir: str
    language: str = "pt"
    max_clip_duration: float = 90
    min_clip_duration: float = 2
    word_timestamps: bool = True


@app.post("/api/process/dry-run")
def process_dry_run(request: DryRunRequest):
    if not Path(request.video_path).exists():
        raise HTTPException(404, f"Vídeo não encontrado: {request.video_path}")
    job_id = processing.start_dry_run(request.video_path, request.output_dir, request.model_dump())
    return {"job_id": job_id}


class RenderRequest(BaseModel):
    video_path: str
    output_dir: str
    language: str = "pt"
    max_clip_duration: float = 90
    min_clip_duration: float = 2


@app.post("/api/process/render")
def process_render(request: RenderRequest):
    cfg = panel_config.load()
    issues = cfg.validate()
    if issues:
        raise HTTPException(422, detail={"issues": issues})
    job_id = processing.start_render(request.output_dir, cfg, request.model_dump())
    return {"job_id": job_id}


@app.get("/api/process/status")
def process_status(job_id: str):
    status = processing.get_job_status(job_id)
    if status is None:
        raise HTTPException(404, "Job não encontrado (ou o servidor reiniciou desde então)")
    return status


# --- projetos e clips ----------------------------------------------------------


@app.get("/api/projects")
def list_projects():
    return [p.__dict__ for p in projects.discover_projects()]


def _resolve_project(path: str) -> Path:
    resolved = Path(path)
    if not resolved.is_dir() or not (resolved / "metadata.json").exists():
        raise HTTPException(404, f"Projeto não encontrado: {path}")
    return resolved


@app.get("/api/projects/clips")
def get_clips(path: str):
    _resolve_project(path)
    return projects.load_clips(path)


@app.get("/api/projects/queue-summary")
def get_queue_summary(path: str):
    _resolve_project(path)
    return projects.load_queue_summary(path)


@app.get("/api/projects/thumbnail")
def get_thumbnail(path: str, clip_id: str):
    project = _resolve_project(path)
    clips = {c["id"]: c for c in projects.load_clips(str(project))}
    clip = clips.get(clip_id)
    if not clip or not clip["arquivo"]:
        raise HTTPException(404, "Clip sem vídeo publicado")

    video_path = project / "legendados" / clip["arquivo"]
    cache_key = f"{project.name}_{clip_id}.jpg"
    thumb_path = THUMBNAIL_CACHE / cache_key

    if not thumb_path.exists():
        subprocess.run(
            [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-ss", "1.0", "-i", str(video_path),
                "-frames:v", "1", "-vf", "scale=270:-2",
                str(thumb_path),
            ],
            check=False,
            timeout=30,
        )
    if not thumb_path.exists():
        raise HTTPException(500, "Não foi possível gerar a miniatura")
    return FileResponse(thumb_path, media_type="image/jpeg")


@app.get("/api/projects/video")
def get_video(path: str, clip_id: str):
    project = _resolve_project(path)
    clips = {c["id"]: c for c in projects.load_clips(str(project))}
    clip = clips.get(clip_id)
    if not clip or not clip["arquivo"]:
        raise HTTPException(404, "Clip sem vídeo publicado")
    video_path = project / "legendados" / clip["arquivo"]
    if not video_path.exists():
        raise HTTPException(404, "Arquivo de vídeo não encontrado no disco")
    return FileResponse(video_path, media_type="video/mp4")


# --- upload / agendamento -----------------------------------------------------


class PublishRequest(BaseModel):
    project_path: str
    max_uploads: int = 4
    schedule: bool = True


@app.post("/api/publish")
def publish_batch(request: PublishRequest):
    project = _resolve_project(request.project_path)
    cfg = panel_config.load()

    if not cfg.youtube_credential_path or not cfg.youtube_token_path:
        raise HTTPException(422, "Configure as credenciais do YouTube antes de publicar")

    script = Path(__file__).resolve().parent.parent / "scripts" / "youtube_upload.py"
    command = [
        sys.executable, str(script),
        "--queue", str(project / "fila_upload.json"),
        "--clips-dir", str(project / "legendados"),
        "--credential", cfg.youtube_credential_path,
        "--token", cfg.youtube_token_path,
        "--max-uploads", str(request.max_uploads),
    ]
    if request.schedule:
        command += ["--schedule", "--slots", ",".join(cfg.schedule_slots)]

    result = subprocess.run(command, capture_output=True, text=True, timeout=3600)
    return {
        "returncode": result.returncode,
        "stdout": result.stdout.strip().splitlines()[-30:],
        "stderr": result.stderr.strip().splitlines()[-10:],
    }


# --- conectores ----------------------------------------------------------------


@app.get("/api/connectors/youtube")
def youtube_connector_status():
    """Testa a conexão de verdade (chamada real à API), não só se os arquivos existem."""
    cfg = panel_config.load()
    if not cfg.youtube_credential_path or not cfg.youtube_token_path:
        return {"connected": False, "reason": "Credenciais não configuradas no painel"}

    credential_path = Path(cfg.youtube_credential_path)
    token_path = Path(cfg.youtube_token_path)
    if not credential_path.exists() or not token_path.exists():
        return {"connected": False, "reason": "Arquivo de credencial ou token não encontrado no disco"}

    import os

    env = os.environ.copy()
    env["YUTU_CREDENTIAL"] = credential_path.read_text(encoding="utf-8")
    env["YUTU_CACHE_TOKEN"] = token_path.read_text(encoding="utf-8")

    try:
        result = subprocess.run(
            ["yutu", "channel", "list", "--for", "mine", "--parts", "snippet,statistics", "--output", "json"],
            env=env, capture_output=True, text=True, timeout=20,
        )
    except FileNotFoundError:
        return {"connected": False, "reason": "binário 'yutu' não encontrado no PATH"}
    except subprocess.TimeoutExpired:
        return {"connected": False, "reason": "tempo esgotado ao consultar a API do YouTube"}

    if result.returncode != 0:
        return {"connected": False, "reason": (result.stderr or result.stdout).strip().splitlines()[-1:]}

    try:
        channels = json.loads(result.stdout)
        channel = channels[0]
    except (json.JSONDecodeError, IndexError, KeyError):
        return {"connected": False, "reason": "resposta inesperada da API"}

    return {
        "connected": True,
        "channel_title": channel.get("snippet", {}).get("title", "?"),
        "channel_id": channel.get("id", "?"),
        "subscriber_count": channel.get("statistics", {}).get("subscriberCount", "?"),
        "video_count": channel.get("statistics", {}).get("videoCount", "?"),
        "credential_path": str(credential_path),
        "token_path": str(token_path),
    }


MCP_REGISTRATION_CWD = Path.home() / "Documents" / "KAUE CARVALHO" / "VIDEOS"


@app.get("/api/connectors/mcp")
def mcp_connector_status():
    """Status do registro do MCP no Claude Code.

    O escopo "local" do `claude mcp add` é amarrado ao diretório de trabalho de onde
    foi registrado — checar de outro cwd sempre dá "não encontrado", mesmo conectado.
    """
    try:
        result = subprocess.run(
            ["claude", "mcp", "get", "yutu"],
            capture_output=True, text=True, timeout=10,
            cwd=str(MCP_REGISTRATION_CWD) if MCP_REGISTRATION_CWD.exists() else None,
        )
        registered = result.returncode == 0
        return {
            "registered": registered,
            "detail": result.stdout.strip() if registered else result.stderr.strip(),
        }
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {"registered": None, "detail": "não foi possível checar (CLI 'claude' indisponível aqui)"}


# --- frontend ------------------------------------------------------------------

app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")


@app.get("/", response_class=HTMLResponse)
def index():
    return (APP_DIR / "static" / "index.html").read_text(encoding="utf-8")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8420)
