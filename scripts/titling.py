#!/usr/bin/env python3
"""Títulos de publicação e renomeação dos clips finais.

O julgamento (escrever o título a partir da transcrição) é feito por um modelo ou por
uma pessoa — este módulo define o CONTRATO do arquivo, valida o resultado e aplica a
renomeação com rastreabilidade.

Uso:
    python3 titling.py --titles out/titulos.json --clips-dir out/legendados
    python3 titling.py --titles out/titulos.json --clips-dir out/legendados --check
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

MAX_TITLE_CHARS = 70          # o Google trunca perto disso; o YouTube destaca ~60
RENAME_MAP_FILENAME = "renomeacao.json"

TITLES_CONTRACT = """\
Escreva um JSON com esta forma:

{
  "clips": {
    "clip_024": {
      "titulo": "Minha tia dizia que eu terminaria atrás das grades. Olha onde estou",
      "descricao": "Quando até a família aposta contra — e a resposta vem em forma de vida.",
      "tags": ["superação", "história real", "motivação"],
      "estrela": true          // opcional: aposta de maior alcance
    }
  }
}

Regras editoriais:
  - Título a partir do CONTEÚDO real do clip (leia a transcrição), nunca de template.
  - Dupla função: gancho (curiosidade, contraste, frase dita no clip) + palavra-chave
    pesquisável (o tema pelo nome que as pessoas buscam).
  - Até 70 caracteres. Caixa normal, não CAPS.
  - Corrija nomes próprios que a transcrição errou; nome de pessoa conhecida no título
    é palavra-chave forte. Na dúvida sobre um nome, NÃO chute — deixe fora e anote.
  - Descrição de uma frase, com segunda palavra-chave. 3 tags por clip.
  - "estrela" para histórias com reviravolta ou pergunta que desmonta objeção.
"""


class TitlingError(ValueError):
    """Arquivo de títulos inválido ou incompatível com os clips."""


def load_titles(path: Path) -> dict[str, dict]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise TitlingError(f"Não foi possível ler {path}: {error}") from error

    clips = payload.get("clips")
    if not isinstance(clips, dict) or not clips:
        raise TitlingError("O arquivo precisa de um objeto 'clips' não vazio")
    return clips


def validate_titles(titles: dict[str, dict]) -> tuple[str, ...]:
    """Problemas encontrados (vazio = tudo certo). Não interrompe: reporta."""
    issues: list[str] = []
    for clip_id, info in sorted(titles.items()):
        title = str(info.get("titulo", "")).strip()
        if not title:
            issues.append(f"{clip_id}: sem título")
            continue
        if len(title) > MAX_TITLE_CHARS:
            issues.append(f"{clip_id}: título com {len(title)} chars (máx {MAX_TITLE_CHARS})")
        if title.isupper():
            issues.append(f"{clip_id}: título todo em CAPS")
        if not info.get("descricao"):
            issues.append(f"{clip_id}: sem descrição")
        if not info.get("tags"):
            issues.append(f"{clip_id}: sem tags")
    return tuple(issues)


def safe_filename(title: str) -> str:
    """Nome de arquivo a partir do título: mantém acentos, remove o que quebra."""
    # Barra vira espaço (senão "IA/tecnologia" colaria em "IAtecnologia");
    # os demais somem porque costumam ser pontuação encostada na palavra.
    cleaned = title.replace("/", " ").replace("\\", " ")
    cleaned = re.sub(r'[:*?"<>|]', "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().rstrip(".")
    return cleaned


def publish_name(clip_id: str, title: str) -> str:
    """`clip_024` + título -> `024 - Título.mp4` (prefixo preserva a ordem)."""
    number = clip_id.split("_")[-1]
    return f"{number} - {safe_filename(title)}.mp4"


def apply_rename(
    clips_dir: Path, titles: dict[str, dict], *, dry_run: bool = False
) -> dict[str, str]:
    """Renomeia `clip_NNN.mp4` para o nome de publicação e grava o mapa."""
    mapping: dict[str, str] = {}
    missing: list[str] = []

    for source in sorted(clips_dir.glob("clip_*.mp4")):
        clip_id = source.stem
        info = titles.get(clip_id)
        if not info or not str(info.get("titulo", "")).strip():
            missing.append(clip_id)
            continue

        target = clips_dir / publish_name(clip_id, info["titulo"])
        if target.exists() and target != source:
            print(f"[titling] já existe, pulando: {target.name}", file=sys.stderr)
            continue
        if not dry_run:
            source.rename(target)
        mapping[clip_id] = target.name

    if missing:
        print(f"[titling] sem título (mantidos): {', '.join(missing)}", file=sys.stderr)

    if mapping and not dry_run:
        map_path = clips_dir.parent / RENAME_MAP_FILENAME
        existing = {}
        if map_path.exists():
            try:
                existing = json.loads(map_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                existing = {}
        merged = {**existing, **mapping}
        map_path.write_text(
            json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return mapping


def main() -> int:
    parser = argparse.ArgumentParser(description="Valida títulos e renomeia clips finais.")
    parser.add_argument("--titles", required=True, help="titulos.json (ver TITLES_CONTRACT)")
    parser.add_argument("--clips-dir", required=True, help="Pasta com os clip_NNN.mp4 finais")
    parser.add_argument("--check", action="store_true", help="Só validar, sem renomear")
    parser.add_argument("--dry-run", action="store_true", help="Mostrar o plano sem renomear")
    args = parser.parse_args()

    try:
        titles = load_titles(Path(args.titles).expanduser())
    except TitlingError as error:
        print(f"[titling] ERRO: {error}", file=sys.stderr)
        return 1

    issues = validate_titles(titles)
    for issue in issues:
        print(f"[titling] aviso: {issue}", file=sys.stderr)

    if args.check:
        print(f"[titling] {len(titles)} títulos, {len(issues)} avisos")
        return 0 if not issues else 1

    mapping = apply_rename(
        Path(args.clips_dir).expanduser(), titles, dry_run=args.dry_run
    )
    verb = "planejados" if args.dry_run else "renomeados"
    print(f"[titling] {len(mapping)} arquivos {verb}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
