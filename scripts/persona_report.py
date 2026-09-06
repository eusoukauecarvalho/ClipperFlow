#!/usr/bin/env python3
"""Análise de impacto da persona sobre cortes já planejados.

Para cada clip do metadata.json, mede quanto é fala da persona e quanto é de outros
locutores, e classifica o dano:

    limpo        — outros locutores < 15% do tempo
    contaminado  — 15% a 50% (o filtro de persona resolve removendo os turnos)
    comprometido — > 50% (o clip é majoritariamente outra pessoa; repensar o corte)

Uso:
    python3 persona_report.py --metadata out/metadata.json \\
        --speakers out/speakers.json --persona S1
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from persona import load_speakers, window_breakdown

CLEAN_THRESHOLD = 0.15
COMPROMISED_THRESHOLD = 0.50


def classify(foreign_ratio: float) -> str:
    if foreign_ratio < CLEAN_THRESHOLD:
        return "limpo"
    if foreign_ratio <= COMPROMISED_THRESHOLD:
        return "contaminado"
    return "comprometido"


def analyze(metadata: dict, speakers, persona: str) -> list[dict]:
    rows: list[dict] = []
    for clip in metadata.get("clips", []):
        start = float(clip["start_seconds"])
        end = float(clip["end_seconds"])
        totals = window_breakdown(speakers, start, end)
        spoken = sum(totals.values()) or 1.0
        foreign = sum(v for k, v in totals.items() if k != persona)
        ratio = foreign / spoken
        rows.append(
            {
                "id": clip["id"],
                "start": clip["start"],
                "end": clip["end"],
                "persona_s": round(totals.get(persona, 0.0), 1),
                "outros_s": round(foreign, 1),
                "outros_pct": round(ratio * 100),
                "status": classify(ratio),
            }
        )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Impacto da persona nos cortes planejados.")
    parser.add_argument("--metadata", required=True)
    parser.add_argument("--speakers", required=True)
    parser.add_argument("--persona", required=True)
    parser.add_argument("--json-out", help="Grava a análise também em JSON")
    args = parser.parse_args()

    metadata = json.loads(Path(args.metadata).read_text(encoding="utf-8"))
    speakers = load_speakers(Path(args.speakers))
    rows = analyze(metadata, speakers, args.persona)

    by_status: dict[str, int] = {}
    for row in rows:
        by_status[row["status"]] = by_status.get(row["status"], 0) + 1

    print(f"persona: {args.persona} | clips: {len(rows)} | {by_status}")
    print(f"{'clip':<11} {'janela':<20} {'persona':>8} {'outros':>7} {'%':>4}  status")
    for row in rows:
        print(
            f"{row['id']:<11} {row['start']+'-'+row['end']:<20} "
            f"{row['persona_s']:>7.1f}s {row['outros_s']:>6.1f}s {row['outros_pct']:>3}%  {row['status']}"
        )

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
