#!/usr/bin/env python3
"""Upload em lote para o YouTube via yutu CLI, respeitando a cota da conta.

O YouTube limita quantos uploads uma conta pode fazer por período (a cota é sobre
OPERAÇÕES de upload, não sobre vídeos existentes no canal — apagar vídeos não a
devolve). Este script sobe um lote pequeno, prioriza os marcados como "estrela", e
PARA imediatamente ao primeiro sinal de limite — insistir contra uma cota de
plataforma não resolve e pode prolongar o bloqueio.

Uso:
    python3 youtube_upload.py --queue out/fila_upload.json --clips-dir out/legendados \
        --credential ~/.claude/skills/podcast-clipper/.youtube/client_secret.json \
        --token ~/.claude/skills/podcast-clipper/.youtube/youtube.token.json \
        --max-uploads 8
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

QUOTA_MARKERS = ("uploadLimitExceeded", "quotaExceeded", "rateLimitExceeded")

# --- agendamento nativo do YouTube ------------------------------------------
# O YouTube publica sozinho no horário marcado: basta subir como "private" com
# publishAt no futuro. Mais robusto que um cron disparando o upload na hora exata —
# não depende de nada rodando localmente quando a hora chegar.
BRAZIL_TZ = ZoneInfo("America/Sao_Paulo")
DEFAULT_SLOTS_LOCAL = ("06:00", "13:00", "18:00", "02:00")  # manhã/tarde/noite/madrugada


def build_schedule(
    count: int, *, start_date: datetime, slots: tuple[str, ...] = DEFAULT_SLOTS_LOCAL
) -> list[str]:
    """Datetimes RFC3339 (UTC) para os próximos `count` itens, N por dia nos slots dados.

    Gera os candidatos (dia calendário × horário) e ordena cronologicamente em vez de
    tentar decidir "a que dia pertence cada slot" — essa segunda abordagem quebrava
    silenciosamente quando um slot cruzava a meia-noite (a madrugada podia cair no
    passado). Ordenar depois de gerar elimina a categoria inteira do bug.
    """
    parsed = [tuple(int(p) for p in slot.split(":")) for slot in slots]
    candidates: list[datetime] = []
    day = 0
    # Sobra de dias para garantir `count` candidatos após filtrar os que já passaram.
    while len(candidates) < count + len(parsed):
        for hour, minute in parsed:
            local = (start_date + timedelta(days=day)).replace(
                hour=hour, minute=minute, second=0, microsecond=0, tzinfo=BRAZIL_TZ
            )
            if local > start_date:
                candidates.append(local)
        day += 1

    candidates.sort()
    return [
        c.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") for c in candidates[:count]
    ]


class UploadError(RuntimeError):
    """Falha ao subir um vídeo — não relacionada a cota."""


class QuotaExceeded(RuntimeError):
    """A conta atingiu o limite de upload do YouTube."""


def load_queue(path: Path) -> list[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise UploadError(f"Não foi possível ler {path}: {error}") from error


def save_queue(path: Path, queue: list[dict]) -> None:
    path.write_text(json.dumps(queue, ensure_ascii=False, indent=2), encoding="utf-8")


def prioritized(queue: list[dict]) -> list[dict]:
    """Pendentes primeiro os marcados como estrela, mantendo a ordem original entre iguais."""
    pending = [item for item in queue if item.get("status") == "pendente"]
    return sorted(pending, key=lambda item: not item.get("estrela"))


def upload_one(
    item: dict, clips_dir: Path, credential: str, token: str, publish_at: str | None = None
) -> str:
    """Sobe um vídeo. Devolve o videoId. Levanta QuotaExceeded ou UploadError."""
    video_path = clips_dir / item["arquivo"]
    if not video_path.exists():
        raise UploadError(f"Arquivo não encontrado: {video_path}")

    description = item.get("descricao", "")
    if item.get("tags"):
        hashtags = " ".join(f"#{t.replace(' ', '')}" for t in item["tags"])
        description = f"{description}\n\n{hashtags}"

    # Agendado: SEMPRE private no upload — o YouTube exige isso para aceitar publishAt
    # e assume sozinho a publicação no horário marcado.
    privacy = "private" if publish_at else item.get("privacy", "private")
    command = [
        "yutu", "video", "insert",
        "--file", str(video_path),
        "--title", item["titulo"],
        "--description", description,
        "--tags", ",".join(item.get("tags", [])),
        "--categoryId", item.get("categoryId", "22"),
        "--privacy", privacy,
        "--yes",
        "--output", "json",
    ]
    if publish_at:
        command += ["--publishAt", publish_at]
    # Herdar o ambiente (não substituí-lo do zero): um processo Go sem HOME/LANG pode
    # emitir avisos no stdout antes do JSON, quebrando o parsing abaixo em silêncio —
    # foi exatamente o que aconteceu quando eu passava env={} só com as credenciais.
    env = os.environ.copy()
    env["YUTU_CREDENTIAL"] = credential
    env["YUTU_CACHE_TOKEN"] = token

    result = subprocess.run(
        command, cwd=clips_dir, env=env, capture_output=True, text=True, timeout=1800,
    )

    output = (result.stdout or "") + (result.stderr or "")
    if result.returncode != 0:
        if any(marker in output for marker in QUOTA_MARKERS):
            raise QuotaExceeded(output.strip().splitlines()[-1] if output.strip() else "cota excedida")
        raise UploadError(output.strip().splitlines()[-1] if output.strip() else "erro desconhecido")

    return _extract_video_id(result.stdout, item["id"])


def _extract_video_id(stdout: str, item_id: str) -> str:
    """Extrai o videoId do JSON de retorno. Nunca falha o upload por causa disso —
    o vídeo já foi criado; um parsing ruim aqui não deve mascarar isso como sucesso
    "incompleto" nem, pior, como falha."""
    try:
        payload = json.loads(stdout)
        video_id = payload[0]["id"] if isinstance(payload, list) else payload["id"]
        if video_id:
            return video_id
    except (json.JSONDecodeError, KeyError, IndexError, TypeError):
        pass
    print(
        f"[youtube_upload] aviso: {item_id} subiu mas não consegui ler o videoId "
        f"da resposta — confira manualmente no YouTube Studio",
        file=sys.stderr,
    )
    return ""


def run_batch(
    queue_path: Path,
    clips_dir: Path,
    credential: str,
    token: str,
    max_uploads: int,
    schedule: bool = False,
    slots: tuple[str, ...] = DEFAULT_SLOTS_LOCAL,
) -> dict:
    queue = load_queue(queue_path)
    pending = prioritized(queue)
    todo = pending[:max_uploads]

    if not pending:
        print("[youtube_upload] nada pendente na fila")
        return {"enviados": 0, "parou_por_cota": False}
    if not todo:
        print(f"[youtube_upload] {len(pending)} pendente(s), mas max_uploads=0 — nada a fazer")
        return {"enviados": 0, "parou_por_cota": False}

    publish_times = (
        build_schedule(len(todo), start_date=datetime.now(BRAZIL_TZ), slots=slots)
        if schedule
        else [None] * len(todo)
    )

    by_id = {item["id"]: item for item in queue}
    enviados = 0
    parou_por_cota = False

    for item, publish_at in zip(todo, publish_times):
        rotulo = f" -> publica em {publish_at}" if publish_at else ""
        print(f"[youtube_upload] subindo {item['id']}: {item['titulo'][:60]}{rotulo}", flush=True)
        try:
            video_id = upload_one(item, clips_dir, credential, token, publish_at)
        except QuotaExceeded as error:
            print(f"[youtube_upload] COTA ATINGIDA — parando. {error}", file=sys.stderr)
            parou_por_cota = True
            break
        except UploadError as error:
            print(f"[youtube_upload] falha em {item['id']}: {error}", file=sys.stderr)
            by_id[item["id"]]["status"] = "erro"
            by_id[item["id"]]["erro"] = str(error)
            save_queue(queue_path, queue)
            continue

        by_id[item["id"]]["status"] = "agendado" if publish_at else "publicado"
        by_id[item["id"]]["video_id"] = video_id
        if publish_at:
            by_id[item["id"]]["publish_at"] = publish_at
        by_id[item["id"]].pop("erro", None)
        enviados += 1
        save_queue(queue_path, queue)
        print(f"[youtube_upload]   ok -> https://youtu.be/{video_id}" if video_id else "[youtube_upload]   ok")

    return {"enviados": enviados, "parou_por_cota": parou_por_cota}


def main() -> int:
    parser = argparse.ArgumentParser(description="Upload em lote para o YouTube via yutu.")
    parser.add_argument("--queue", required=True, help="fila_upload.json")
    parser.add_argument("--clips-dir", required=True, help="Pasta com os vídeos finais")
    parser.add_argument("--credential", required=True, help="client_secret.json")
    parser.add_argument("--token", required=True, help="youtube.token.json")
    parser.add_argument("--max-uploads", type=int, default=8, help="Teto de uploads nesta chamada")
    parser.add_argument(
        "--schedule", action="store_true",
        help="Agendar publicação (native do YouTube) em vez de publicar/privar direto",
    )
    parser.add_argument(
        "--slots", default=",".join(DEFAULT_SLOTS_LOCAL),
        help="Horários locais (America/Sao_Paulo) separados por vírgula, ex.: 06:00,13:00,18:00,02:00",
    )
    args = parser.parse_args()

    credential = Path(args.credential).read_text(encoding="utf-8")
    token = Path(args.token).read_text(encoding="utf-8")
    slots = tuple(args.slots.split(","))

    result = run_batch(
        Path(args.queue), Path(args.clips_dir), credential, token, args.max_uploads,
        schedule=args.schedule, slots=slots,
    )
    print(f"[youtube_upload] lote concluído: {result['enviados']} enviados"
          f"{' (parou por cota)' if result['parou_por_cota'] else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
