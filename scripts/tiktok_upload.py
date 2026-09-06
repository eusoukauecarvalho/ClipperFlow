#!/usr/bin/env python3
"""Publicação no TikTok via Content Posting API (Direct Post).

Fluxo: consultar creator_info → iniciar o post → subir o arquivo em
fragmentos → acompanhar o status.

Duas coisas que a API impõe e que valem saber antes de usar:

1. Enquanto o app não passa pela AUDITORIA do TikTok, todo vídeo publicado é
   forçado a privado. Não é rascunho: está publicado e invisível. Por isso o
   nível de privacidade nunca é chutado — vem de `privacy_level_options`, que
   o creator_info devolve já refletindo a situação real da conta.

2. `total_chunk_count` é o piso da divisão, não o teto: um arquivo de 200 MB
   com fragmentos de 64 MB tem 3 partes (64, 64, 72), e a última absorve o
   resto. Usar arredondamento para cima cria um fragmento a mais e o upload
   falha.

Uso:
    python3 tiktok_upload.py --queue fila.json --clips-dir legendados \
        --client-key XXX --client-secret YYY --token tiktok.token.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import tiktok_auth

CREATOR_INFO_URL = "https://open.tiktokapis.com/v2/post/publish/creator_info/query/"
INIT_URL = "https://open.tiktokapis.com/v2/post/publish/video/init/"
STATUS_URL = "https://open.tiktokapis.com/v2/post/publish/status/fetch/"

MB = 1024 * 1024
CHUNK_MIN = 5 * MB
CHUNK_MAX = 64 * MB
CHUNK_ULTIMO_MAX = 128 * MB
CHUNKS_MAX = 1000

# A API aceita 6 chamadas por minuto por token; 11s entre posts deixa folga
# para as 3 chamadas de cada publicação (creator_info, init, status).
INTERVALO_ENTRE_POSTS_S = 11
TIMEOUT_UPLOAD_S = 1800
STATUS_TENTATIVAS = 20
STATUS_INTERVALO_S = 6

LEGENDA_MAX = 2200


class TikTokError(RuntimeError):
    """Falha ao publicar no TikTok."""


class TikTokRateLimit(TikTokError):
    """A conta atingiu o limite de publicações da API."""


@dataclass(frozen=True)
class PlanoUpload:
    chunk_size: int
    total_chunk_count: int
    faixas: tuple[tuple[int, int], ...]  # (primeiro_byte, ultimo_byte) inclusivos


def planejar_chunks(video_size: int) -> PlanoUpload:
    """Divide o arquivo conforme as regras do TikTok.

    - Até 64 MB vai inteiro, num fragmento só.
    - Acima disso, `total_chunk_count = floor(video_size / chunk_size)` e o
      ÚLTIMO fragmento leva o resto (pode passar de chunk_size, até 128 MB).
    """
    if video_size <= 0:
        raise TikTokError("Arquivo de vídeo vazio")

    if video_size <= CHUNK_MAX:
        return PlanoUpload(video_size, 1, ((0, video_size - 1),))

    chunk_size = CHUNK_MAX
    total = video_size // chunk_size
    if total > CHUNKS_MAX:
        raise TikTokError(
            f"Vídeo exigiria {total} fragmentos (máximo {CHUNKS_MAX}) — arquivo grande demais"
        )

    faixas = []
    for indice in range(total):
        inicio = indice * chunk_size
        fim = video_size - 1 if indice == total - 1 else inicio + chunk_size - 1
        faixas.append((inicio, fim))

    ultimo = faixas[-1][1] - faixas[-1][0] + 1
    if ultimo > CHUNK_ULTIMO_MAX:
        raise TikTokError(f"Último fragmento de {ultimo} bytes passa do limite de 128 MB")
    return PlanoUpload(chunk_size, total, tuple(faixas))


# --- chamadas à API ------------------------------------------------------


def _post_json(url: str, token: str, corpo: dict) -> dict:
    requisicao = urllib.request.Request(
        url,
        data=json.dumps(corpo).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=UTF-8",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(requisicao, timeout=60) as resposta:
            payload = json.loads(resposta.read())
    except urllib.error.HTTPError as erro:
        texto = erro.read().decode("utf-8", "replace")[:400]
        if erro.code == 429 or "rate_limit" in texto:
            raise TikTokRateLimit(f"Limite da API atingido: {texto}") from erro
        raise TikTokError(f"HTTP {erro.code}: {texto}") from erro
    except OSError as erro:
        raise TikTokError(f"Falha de rede: {erro}") from erro

    erro_api = payload.get("error") or {}
    codigo = erro_api.get("code", "ok")
    if codigo not in ("ok", ""):
        mensagem = erro_api.get("message", "sem detalhe")
        if "rate_limit" in codigo or "quota" in codigo:
            raise TikTokRateLimit(f"{codigo}: {mensagem}")
        raise TikTokError(f"{codigo}: {mensagem}")
    return payload.get("data", {})


def consultar_criador(token: str) -> dict:
    """Obrigatório antes de publicar: traz as opções de privacidade reais."""
    return _post_json(CREATOR_INFO_URL, token, {})


def escolher_privacidade(info_criador: dict, desejada: str) -> str:
    """Usa a privacidade pedida só se a conta a oferece.

    App não auditado não recebe PUBLIC_TO_EVERYONE na lista; forçar o valor
    faria a chamada falhar. Cair para a opção disponível é o comportamento
    correto — e o chamador é avisado.
    """
    opcoes = info_criador.get("privacy_level_options") or []
    if not opcoes:
        raise TikTokError("creator_info não trouxe privacy_level_options")
    if desejada in opcoes:
        return desejada
    for alternativa in ("SELF_ONLY", "FOLLOWER_OF_CREATOR", "MUTUAL_FOLLOW_FRIENDS"):
        if alternativa in opcoes:
            return alternativa
    return opcoes[0]


def iniciar_post(token: str, video_size: int, plano: PlanoUpload, post_info: dict) -> tuple[str, str]:
    dados = _post_json(INIT_URL, token, {
        "post_info": post_info,
        "source_info": {
            "source": "FILE_UPLOAD",
            "video_size": video_size,
            "chunk_size": plano.chunk_size,
            "total_chunk_count": plano.total_chunk_count,
        },
    })
    publish_id = dados.get("publish_id")
    upload_url = dados.get("upload_url")
    if not publish_id or not upload_url:
        raise TikTokError("init não devolveu publish_id/upload_url")
    return publish_id, upload_url


def enviar_arquivo(upload_url: str, caminho: Path, plano: PlanoUpload) -> None:
    tamanho = caminho.stat().st_size
    with caminho.open("rb") as arquivo:
        for indice, (inicio, fim) in enumerate(plano.faixas, start=1):
            arquivo.seek(inicio)
            bloco = arquivo.read(fim - inicio + 1)
            requisicao = urllib.request.Request(
                upload_url,
                data=bloco,
                headers={
                    "Content-Type": "video/mp4",
                    "Content-Length": str(len(bloco)),
                    "Content-Range": f"bytes {inicio}-{fim}/{tamanho}",
                },
                method="PUT",
            )
            try:
                with urllib.request.urlopen(requisicao, timeout=TIMEOUT_UPLOAD_S):
                    pass
            except urllib.error.HTTPError as erro:
                corpo = erro.read().decode("utf-8", "replace")[:300]
                raise TikTokError(
                    f"Fragmento {indice}/{plano.total_chunk_count} falhou (HTTP {erro.code}): {corpo}"
                ) from erro
            except OSError as erro:
                raise TikTokError(f"Fragmento {indice} falhou: {erro}") from erro

            if plano.total_chunk_count > 1:
                print(f"[tiktok]   fragmento {indice}/{plano.total_chunk_count}", flush=True)


def acompanhar_status(token: str, publish_id: str) -> str:
    """Espera o processamento terminar. Devolve o status final."""
    for _ in range(STATUS_TENTATIVAS):
        dados = _post_json(STATUS_URL, token, {"publish_id": publish_id})
        status = dados.get("status", "")
        if status in ("PUBLISH_COMPLETE", "SEND_TO_USER_INBOX"):
            return status
        if status == "FAILED":
            razao = dados.get("fail_reason", "sem motivo informado")
            raise TikTokError(f"TikTok recusou o vídeo: {razao}")
        time.sleep(STATUS_INTERVALO_S)
    return "PROCESSING"  # ainda processando: não é erro, só demora


def montar_legenda(item: dict) -> str:
    """Título + hashtags, dentro do limite de 2200 caracteres."""
    partes = [item.get("titulo", "").strip()]
    if item.get("descricao"):
        partes.append(item["descricao"].strip())
    if item.get("tags"):
        partes.append(" ".join(f"#{t.replace(' ', '')}" for t in item["tags"]))
    return "\n\n".join(p for p in partes if p)[:LEGENDA_MAX]


def publicar(
    caminho_video: Path, token: str, item: dict, privacidade: str, info_criador: dict
) -> tuple[str, str]:
    tamanho = caminho_video.stat().st_size
    plano = planejar_chunks(tamanho)
    nivel = escolher_privacidade(info_criador, privacidade)

    publish_id, upload_url = iniciar_post(token, tamanho, plano, {
        "title": montar_legenda(item),
        "privacy_level": nivel,
        "disable_duet": False,
        "disable_stitch": False,
        "disable_comment": False,
    })
    enviar_arquivo(upload_url, caminho_video, plano)
    return publish_id, nivel


# --- lote ----------------------------------------------------------------


def rodar_lote(
    fila: Path, clips_dir: Path, token: str, maximo: int, privacidade: str
) -> dict:
    from youtube_upload import load_queue, prioritized, save_queue

    itens = load_queue(fila)
    pendentes = [i for i in prioritized(itens) if i.get("tiktok_status") != "publicado"]
    a_fazer = pendentes[:maximo]

    if not pendentes:
        print("[tiktok] nada pendente na fila")
        return {"enviados": 0, "parou_por_limite": False}
    if not a_fazer:
        print(f"[tiktok] {len(pendentes)} pendente(s), mas max=0 — nada a fazer")
        return {"enviados": 0, "parou_por_limite": False}

    info_criador = consultar_criador(token)
    nome = info_criador.get("creator_nickname") or info_criador.get("creator_username", "?")
    opcoes = info_criador.get("privacy_level_options", [])
    print(f"[tiktok] conta: {nome} | privacidade disponível: {', '.join(opcoes)}", flush=True)
    if "PUBLIC_TO_EVERYONE" not in opcoes:
        print(
            "[tiktok] AVISO: a conta não oferece publicação pública pela API. "
            "Isso é esperado enquanto o app não passa pela auditoria do TikTok — "
            "os vídeos vão subir, mas ficam privados.",
            file=sys.stderr,
        )

    por_id = {i["id"]: i for i in itens}
    enviados = 0
    parou = False

    for posicao, item in enumerate(a_fazer):
        caminho = clips_dir / item["arquivo"]
        if not caminho.exists():
            print(f"[tiktok] arquivo ausente: {caminho}", file=sys.stderr)
            continue

        print(f"[tiktok] publicando {item['id']}: {item['titulo'][:56]}", flush=True)
        try:
            publish_id, nivel = publicar(caminho, token, item, privacidade, info_criador)
            status = acompanhar_status(token, publish_id)
        except TikTokRateLimit as erro:
            print(f"[tiktok] LIMITE ATINGIDO — parando. {erro}", file=sys.stderr)
            parou = True
            break
        except TikTokError as erro:
            print(f"[tiktok] falha em {item['id']}: {erro}", file=sys.stderr)
            por_id[item["id"]]["tiktok_status"] = "erro"
            por_id[item["id"]]["tiktok_erro"] = str(erro)
            save_queue(fila, itens)
            continue

        por_id[item["id"]]["tiktok_status"] = "publicado"
        por_id[item["id"]]["tiktok_publish_id"] = publish_id
        por_id[item["id"]]["tiktok_privacidade"] = nivel
        por_id[item["id"]]["tiktok_published_at"] = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
        )
        por_id[item["id"]].pop("tiktok_erro", None)
        save_queue(fila, itens)
        enviados += 1
        print(f"[tiktok]   ok ({status}, {nivel})", flush=True)

        if posicao < len(a_fazer) - 1:
            time.sleep(INTERVALO_ENTRE_POSTS_S)

    return {"enviados": enviados, "parou_por_limite": parou}


def main() -> int:
    parser = argparse.ArgumentParser(description="Publica clipes no TikTok.")
    parser.add_argument("--queue", required=True)
    parser.add_argument("--clips-dir", required=True)
    parser.add_argument("--client-key", required=True)
    parser.add_argument("--client-secret", required=True)
    parser.add_argument("--token", required=True, help="tiktok.token.json")
    parser.add_argument("--max-uploads", type=int, default=3)
    parser.add_argument(
        "--privacy", default="PUBLIC_TO_EVERYONE",
        choices=("PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "FOLLOWER_OF_CREATOR", "SELF_ONLY"),
        help="Pedido; cai para o que a conta oferecer se não estiver disponível",
    )
    args = parser.parse_args()

    try:
        tokens = tiktok_auth.token_valido(
            Path(args.token).expanduser(), args.client_key, args.client_secret
        )
        resultado = rodar_lote(
            Path(args.queue), Path(args.clips_dir), tokens.access_token,
            args.max_uploads, args.privacy,
        )
    except (TikTokError, tiktok_auth.TikTokAuthError) as erro:
        print(f"[tiktok] ERRO: {erro}", file=sys.stderr)
        return 1

    sufixo = " (parou por limite)" if resultado["parou_por_limite"] else ""
    print(f"[tiktok] lote concluído: {resultado['enviados']} enviados{sufixo}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
