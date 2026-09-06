"""Seleção semântica de cortes: julgamento editorial em vez de heurística acústica.

A montagem automática corta onde o áudio cala. Isso não sabe distinguir um pensamento
completo de um fragmento, nem manter uma pergunta junto da sua resposta, nem descartar
um "curte e compartilha". Este módulo prepara a transcrição para que um modelo leia e
escolha os trechos, e valida a escolha contra a transcrição real.

Fluxo:
    1. `write_briefing()` gera a transcrição indexada com timestamps.
    2. Um modelo lê o briefing e escreve uma seleção JSON (ver SELECTION_CONTRACT).
    3. `load_selection()` valida e converte em clips renderizáveis.

A validação existe porque um modelo pode inventar timestamps: tudo é conferido contra
as falas reais, encaixado nas fronteiras de fala e checado contra os limites de duração.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from transcribe import Utterance

BRIEFING_FILENAME = "briefing.txt"
SELECTION_FILENAME = "selecao.json"
SNAP_TOLERANCE_S = 3.0

SELECTION_CONTRACT = """\
Escreva um JSON com esta forma:

{
  "clips": [
    {
      "start": 1219,           // segundos, do briefing
      "end": 1298,             // segundos, do briefing
      "title": "frase curta",  // do que trata o corte
      "reason": "por que funciona como clip isolado",
      "score": 8.5             // 0-10, valor editorial real
    }
  ]
}

Regras de seleção:
  - Cada corte é UM pensamento completo: começa onde a ideia nasce, termina onde fecha.
  - Pergunta e resposta ficam SEMPRE no mesmo corte. Nunca termine numa pergunta.
  - Setup e punchline ficam juntos. Uma história precisa do desfecho.
  - Descarte: CTA ("curte, compartilha"), apresentador reformulando pergunta, small talk,
    transição entre blocos, qualquer trecho que só faça sentido sabendo o que veio antes.
  - Um corte precisa se sustentar sozinho para quem chegou ali pelo feed, sem contexto.
  - Prefira menos cortes bons a muitos cortes medianos.
"""


class SelectionError(ValueError):
    """Seleção inválida ou incompatível com a transcrição."""


@dataclass(frozen=True)
class SelectedClip:
    start: float
    end: float
    title: str
    reason: str
    score: float

    @property
    def duration(self) -> float:
        return self.end - self.start


def write_briefing(utterances: Sequence[Utterance], path: Path) -> Path:
    """Transcrição indexada por tempo, no formato mais compacto que ainda é legível."""
    lines = [
        "# Transcrição indexada. Cada linha: [segundos] (mm:ss) texto",
        f"# Total: {len(utterances)} falas, {_timecode(utterances[-1].end if utterances else 0)}",
        "",
    ]
    lines.extend(
        f"[{int(u.start)}] ({_timecode(u.start)}) {u.text.strip()}" for u in utterances
    )
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def load_selection(
    path: Path,
    utterances: Sequence[Utterance],
    *,
    min_duration_s: float,
    max_duration_s: float,
) -> tuple[SelectedClip, ...]:
    """Lê a seleção e valida cada corte contra a transcrição real."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SelectionError(f"Não foi possível ler a seleção em {path}: {error}") from error

    entries = payload.get("clips")
    if not isinstance(entries, list) or not entries:
        raise SelectionError("A seleção precisa de uma lista 'clips' não vazia")

    limit = utterances[-1].end if utterances else 0.0
    selected = [
        _validate_entry(entry, position, utterances, limit, min_duration_s, max_duration_s)
        for position, entry in enumerate(entries, start=1)
    ]
    return tuple(sorted(selected, key=lambda c: c.start))


def _validate_entry(
    entry: dict,
    position: int,
    utterances: Sequence[Utterance],
    limit: float,
    min_duration_s: float,
    max_duration_s: float,
) -> SelectedClip:
    try:
        start = float(entry["start"])
        end = float(entry["end"])
    except (KeyError, TypeError, ValueError) as error:
        raise SelectionError(f"Corte #{position}: start/end ausente ou inválido") from error

    if end <= start:
        raise SelectionError(f"Corte #{position}: end ({end}) não é maior que start ({start})")
    if start < 0 or end > limit + SNAP_TOLERANCE_S:
        raise SelectionError(
            f"Corte #{position}: {start}-{end}s fora do vídeo (0-{limit:.0f}s). "
            "Use apenas timestamps que aparecem no briefing."
        )

    start = snap(start, utterances, to_start=True)
    end = snap(end, utterances, to_start=False)
    duration = end - start

    if duration < min_duration_s:
        raise SelectionError(f"Corte #{position}: {duration:.1f}s abaixo do mínimo")
    if duration > max_duration_s:
        raise SelectionError(
            f"Corte #{position}: {duration:.1f}s acima do máximo de {max_duration_s:.0f}s"
        )

    return SelectedClip(
        start=start,
        end=end,
        title=str(entry.get("title", "")).strip(),
        reason=str(entry.get("reason", "")).strip(),
        score=float(entry.get("score", 0.0)),
    )


def snap(time: float, utterances: Sequence[Utterance], *, to_start: bool) -> float:
    """Encaixa na fronteira de fala mais próxima, para não cortar no meio de uma palavra."""
    boundaries = [u.start for u in utterances] if to_start else [u.end for u in utterances]
    if not boundaries:
        return time
    nearest = min(boundaries, key=lambda b: abs(b - time))
    return nearest if abs(nearest - time) <= SNAP_TOLERANCE_S else time


def overlaps(clips: Sequence[SelectedClip]) -> tuple[tuple[str, str], ...]:
    """Pares de cortes que se sobrepõem — permitido, mas vale reportar."""
    found: list[tuple[str, str]] = []
    ordered = sorted(clips, key=lambda c: c.start)
    for earlier, later in zip(ordered, ordered[1:]):
        if later.start < earlier.end:
            found.append((earlier.title or f"{earlier.start:.0f}s", later.title or f"{later.start:.0f}s"))
    return tuple(found)


def _timecode(seconds: float) -> str:
    total = int(seconds)
    return f"{total // 60:02d}:{total % 60:02d}"
