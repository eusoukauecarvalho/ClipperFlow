#!/usr/bin/env python3
"""OAuth do TikTok: autorização, troca de código e renovação de token.

Diferença importante em relação ao YouTube: o access_token do TikTok dura
apenas 24 horas (o refresh_token, 365 dias). Um painel que roda todo dia
quebra sem renovação automática, então ela é parte do fluxo normal — não um
caminho de exceção.

Uso:
    python3 tiktok_auth.py --client-key XXX --client-secret YYY authorize
    python3 tiktok_auth.py --client-key XXX --client-secret YYY exchange --code CODE
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import secrets
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

AUTHORIZE_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
REVOKE_URL = "https://open.tiktokapis.com/v2/oauth/revoke/"

SCOPES = ("user.info.basic", "video.publish", "video.upload")
# Renova antes de expirar de fato: um upload longo não pode perder o token no meio.
REFRESH_MARGEM_S = 600


class TikTokAuthError(RuntimeError):
    """Falha no fluxo de autenticação do TikTok."""


@dataclass
class TikTokTokens:
    access_token: str
    refresh_token: str
    open_id: str
    scope: str
    expires_at: float  # epoch em que o access_token morre

    @property
    def expirado(self) -> bool:
        return time.time() >= self.expires_at - REFRESH_MARGEM_S


# --- PKCE ---------------------------------------------------------------


def gerar_code_verifier() -> str:
    """Segredo efêmero do PKCE. Obrigatório para app desktop."""
    return secrets.token_urlsafe(64)[:128]


def derivar_code_challenge(code_verifier: str) -> str:
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def montar_url_autorizacao(
    client_key: str,
    redirect_uri: str,
    code_challenge: str,
    state: str,
    scopes: tuple[str, ...] = SCOPES,
) -> str:
    parametros = {
        "client_key": client_key,
        "scope": ",".join(scopes),
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{AUTHORIZE_URL}?{urllib.parse.urlencode(parametros)}"


# --- token --------------------------------------------------------------


def _pedir_token(campos: dict) -> dict:
    dados = urllib.parse.urlencode(campos).encode("ascii")
    requisicao = urllib.request.Request(
        TOKEN_URL,
        data=dados,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(requisicao, timeout=30) as resposta:
            payload = json.loads(resposta.read())
    except urllib.error.HTTPError as erro:
        corpo = erro.read().decode("utf-8", "replace")[:400]
        raise TikTokAuthError(f"HTTP {erro.code} do TikTok: {corpo}") from erro
    except OSError as erro:
        raise TikTokAuthError(f"Falha de rede ao falar com o TikTok: {erro}") from erro

    if payload.get("error"):
        raise TikTokAuthError(
            f"{payload.get('error')}: {payload.get('error_description', 'sem detalhe')}"
        )
    return payload


def _para_tokens(payload: dict) -> TikTokTokens:
    faltando = [c for c in ("access_token", "refresh_token", "open_id") if not payload.get(c)]
    if faltando:
        raise TikTokAuthError(f"Resposta sem {', '.join(faltando)}")
    return TikTokTokens(
        access_token=payload["access_token"],
        refresh_token=payload["refresh_token"],
        open_id=payload["open_id"],
        scope=payload.get("scope", ""),
        expires_at=time.time() + float(payload.get("expires_in", 86400)),
    )


def trocar_codigo(
    client_key: str, client_secret: str, code: str, redirect_uri: str, code_verifier: str
) -> TikTokTokens:
    return _para_tokens(_pedir_token({
        "client_key": client_key,
        "client_secret": client_secret,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
        "code_verifier": code_verifier,
    }))


def renovar(client_key: str, client_secret: str, refresh_token: str) -> TikTokTokens:
    """Renova o access_token.

    O TikTok pode devolver um refresh_token NOVO — guardar o antigo faz a
    renovação seguinte falhar quando o anterior for invalidado.
    """
    return _para_tokens(_pedir_token({
        "client_key": client_key,
        "client_secret": client_secret,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }))


# --- persistência --------------------------------------------------------


def carregar(caminho: Path) -> TikTokTokens | None:
    try:
        dados = json.loads(caminho.read_text(encoding="utf-8"))
        return TikTokTokens(**{c: dados[c] for c in TikTokTokens.__dataclass_fields__})
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return None


def salvar(caminho: Path, tokens: TikTokTokens) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(json.dumps(asdict(tokens), indent=2), encoding="utf-8")
    caminho.chmod(0o600)


def token_valido(caminho: Path, client_key: str, client_secret: str) -> TikTokTokens:
    """Devolve um token utilizável, renovando e regravando quando necessário."""
    tokens = carregar(caminho)
    if tokens is None:
        raise TikTokAuthError(
            f"Sem token salvo em {caminho}. Rode `tiktok_auth.py authorize` primeiro."
        )
    if not tokens.expirado:
        return tokens

    renovado = renovar(client_key, client_secret, tokens.refresh_token)
    salvar(caminho, renovado)
    return renovado


# --- CLI -----------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="OAuth do TikTok.")
    parser.add_argument("--client-key", required=True)
    parser.add_argument("--client-secret", required=True)
    parser.add_argument("--redirect-uri", default="http://localhost:8421/tiktok/callback")
    parser.add_argument("--token-file", default="tiktok.token.json")
    sub = parser.add_subparsers(dest="acao", required=True)
    sub.add_parser("authorize", help="Imprime a URL de autorização e o code_verifier")
    trocar = sub.add_parser("exchange", help="Troca o código pelo token")
    trocar.add_argument("--code", required=True)
    trocar.add_argument("--code-verifier", required=True)
    sub.add_parser("refresh", help="Renova o token salvo")
    args = parser.parse_args()

    caminho = Path(args.token_file).expanduser()

    try:
        if args.acao == "authorize":
            verificador = gerar_code_verifier()
            url = montar_url_autorizacao(
                args.client_key, args.redirect_uri,
                derivar_code_challenge(verificador), secrets.token_urlsafe(16),
            )
            print("Abra no navegador e autorize:\n")
            print(url)
            print("\nGuarde este code_verifier para o passo `exchange`:\n")
            print(verificador)
            return 0

        if args.acao == "exchange":
            tokens = trocar_codigo(
                args.client_key, args.client_secret, args.code,
                args.redirect_uri, args.code_verifier,
            )
            salvar(caminho, tokens)
            print(f"[tiktok] token salvo em {caminho} (open_id {tokens.open_id})")
            return 0

        tokens = token_valido(caminho, args.client_key, args.client_secret)
        print(f"[tiktok] token válido até {time.ctime(tokens.expires_at)}")
        return 0
    except TikTokAuthError as erro:
        print(f"[tiktok] ERRO: {erro}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
