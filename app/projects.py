"""Descoberta de projetos e visão consolidada de cada clip.

Um "projeto" é qualquer pasta com um metadata.json (a saída do clipper.py). Este
módulo junta metadata.json + titulos.json + renomeacao.json + fila_upload.json — hoje
espalhados em quatro arquivos que eu lia um por um — numa única lista pronta para tela.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

SEARCH_ROOTS = (Path.home() / "Documents",)
MAX_SEARCH_DEPTH = 6
METADATA_FILENAME = "metadata.json"


@dataclass(frozen=True)
class Project:
    name: str
    path: str  # absoluto, usado como identificador nas rotas
    clip_count: int
    total_duration_min: float


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def discover_projects() -> tuple[Project, ...]:
    """Varre as pastas do usuário em busca de metadata.json — sem exigir configuração."""
    found: list[Project] = []
    for root in SEARCH_ROOTS:
        if not root.exists():
            continue
        for metadata_path in root.rglob(METADATA_FILENAME):
            if len(metadata_path.relative_to(root).parts) > MAX_SEARCH_DEPTH:
                continue
            data = _read_json(metadata_path)
            clips = data.get("clips", [])
            if not clips:
                continue
            duration_min = sum(
                float(str(c.get("final_duration", "0")).rstrip("s")) for c in clips
            ) / 60
            found.append(
                Project(
                    name=metadata_path.parent.name,
                    path=str(metadata_path.parent),
                    clip_count=len(clips),
                    total_duration_min=round(duration_min, 1),
                )
            )
    return tuple(sorted(found, key=lambda p: p.name))


def load_clips(project_path: str) -> list[dict]:
    """Um registro por clip, juntando metadata + título + status de upload real."""
    base = Path(project_path)
    metadata = _read_json(base / METADATA_FILENAME)
    titles = _read_json(base / "titulos.json").get("clips", {})
    rename_map = _read_json(base / "renomeacao.json")
    queue = {item["id"]: item for item in _read_json(base / "fila_upload.json") or []}
    legendados_dir = base / "legendados"

    clips: list[dict] = []
    for clip in metadata.get("clips", []):
        clip_id = clip["id"]
        title_info = titles.get(clip_id, {})
        queue_info = queue.get(clip_id, {})
        published_name = rename_map.get(clip_id, "")
        video_path = legendados_dir / published_name if published_name else None

        clips.append(
            {
                "id": clip_id,
                "titulo": title_info.get("titulo", clip_id),
                "descricao": title_info.get("descricao", ""),
                "tags": title_info.get("tags", []),
                "estrela": bool(title_info.get("estrela")),
                "start": clip.get("start", ""),
                "end": clip.get("end", ""),
                "duracao": clip.get("final_duration", ""),
                "score": clip.get("importance_score", 0),
                "transcricao": clip.get("transcription", ""),
                "arquivo": published_name,
                "video_existe": bool(video_path and video_path.exists()),
                "upload_status": queue_info.get("status", "nao_enfileirado"),
                "publish_at": queue_info.get("publish_at", ""),
                "video_id": queue_info.get("video_id", ""),
            }
        )
    return clips


def load_queue_summary(project_path: str) -> dict:
    queue = _read_json(Path(project_path) / "fila_upload.json") or []
    from collections import Counter

    counts = Counter(item.get("status", "desconhecido") for item in queue)
    return {"total": len(queue), "por_status": dict(counts)}
