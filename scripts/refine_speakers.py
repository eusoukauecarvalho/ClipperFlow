#!/usr/bin/env python3
"""Refina as fronteiras de locutor para resolução de palavra.

A diarização rotula falas inteiras do Whisper, mas uma fala pode conter DUAS vozes
(pergunta e reação no mesmo segmento). Este passo pega as falas em zona de transição,
quebra em janelas curtas pelos timestamps de palavra e classifica cada janela contra o
centroide de voz de cada locutor (embeddings já cacheados — só as janelas novas são
computadas).

Uso:
    python3 refine_speakers.py --audio a.wav --transcript t.json \\
        --speakers speakers.json --output speakers_fino.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from diarize import SAMPLE_RATE, DiarizationError, _load_wav_mono

WINDOW_S = 1.2
MIN_WINDOW_S = 0.45
MERGE_GAP_S = 0.25


def refine(audio_path: Path, transcript: dict, speakers: dict, cache_path: Path) -> dict:
    try:
        import numpy as np
        import torch
        from speechbrain.inference.speaker import EncoderClassifier
    except ImportError as error:
        raise DiarizationError(f"Dependência ausente: {error.name}") from error

    utterances = transcript["utterances"]
    labels = [u.get("speaker") for u in speakers["utterances"]]
    if len(labels) != len(utterances):
        raise DiarizationError("speakers.json não corresponde à transcrição atual")

    data = np.load(cache_path)
    usable, matrix = list(data["usable"]), data["matrix"]
    matrix = matrix / np.linalg.norm(matrix, axis=1, keepdims=True)

    centroids: dict[str, object] = {}
    for row, utterance_index in enumerate(usable):
        label = labels[utterance_index]
        centroids.setdefault(label, []).append(matrix[row])
    centroids = {k: np.mean(v, axis=0) for k, v in centroids.items()}
    centroids = {k: v / np.linalg.norm(v) for k, v in centroids.items()}

    mono = torch.from_numpy(_load_wav_mono(audio_path))
    encoder = EncoderClassifier.from_hparams(
        source="speechbrain/spkrec-ecapa-voxceleb",
        savedir=str(Path.home() / ".cache" / "speechbrain" / "ecapa"),
        run_opts={"device": "cpu"},
    )

    # Zona de transição: fala cujo vizinho tem outro rótulo.
    suspicious = {
        i for i in range(len(utterances))
        if (i > 0 and labels[i - 1] != labels[i])
        or (i + 1 < len(labels) and labels[i + 1] != labels[i])
    }
    print(f"[refine] {len(suspicious)} falas em zona de transição", flush=True)

    windows: list[tuple[int, float, float]] = []
    for index in sorted(suspicious):
        for start, end in _word_windows(utterances[index]):
            windows.append((index, start, end))

    assignments: dict[tuple[int, float, float], str] = {}
    batch_size = 64
    for offset in range(0, len(windows), batch_size):
        batch = windows[offset : offset + batch_size]
        chunks = [
            mono[int(s * SAMPLE_RATE) : int(e * SAMPLE_RATE)] for _, s, e in batch
        ]
        max_len = max(c.numel() for c in chunks)
        padded = torch.zeros(len(chunks), max_len)
        lengths = torch.zeros(len(chunks))
        for row, chunk in enumerate(chunks):
            padded[row, : chunk.numel()] = chunk
            lengths[row] = chunk.numel() / max_len
        with torch.no_grad():
            emb = encoder.encode_batch(padded, lengths).squeeze(1).cpu().numpy()
        emb = emb / np.linalg.norm(emb, axis=1, keepdims=True)
        for row, key in enumerate(batch):
            scores = {k: float(np.dot(emb[row], c)) for k, c in centroids.items()}
            assignments[key] = max(scores, key=scores.get)
        print(f"[refine] janelas {min(offset+batch_size, len(windows))}/{len(windows)}", flush=True)

    # Turnos finos: falas fora da zona mantêm o rótulo; as da zona usam as janelas.
    events: list[tuple[float, float, str]] = []
    for index, utterance in enumerate(utterances):
        if index not in suspicious:
            events.append((float(utterance["start"]), float(utterance["end"]), labels[index]))
            continue
        for (i, s, e), label in assignments.items():
            if i == index:
                events.append((s, e, label))
    events.sort()

    turns: list[list] = []
    for start, end, label in events:
        if turns and turns[-1][2] == label and start - turns[-1][1] <= MERGE_GAP_S:
            turns[-1][1] = max(turns[-1][1], end)
            continue
        turns.append([start, end, label])

    totals: dict[str, float] = {}
    for start, end, label in turns:
        totals[label] = totals.get(label, 0.0) + (end - start)

    return {
        **speakers,
        "refined": True,
        "totals_s": {k: round(v, 1) for k, v in sorted(totals.items())},
        "turns": [
            {"start": round(s, 2), "end": round(e, 2), "speaker": l} for s, e, l in turns
        ],
    }


def _word_windows(utterance: dict) -> list[tuple[float, float]]:
    """Janelas de ~1,2s alinhadas às palavras; sem palavras, a fala inteira."""
    words = utterance.get("words") or []
    if not words:
        return [(float(utterance["start"]), float(utterance["end"]))]

    windows: list[tuple[float, float]] = []
    current_start = float(words[0]["start"])
    last_end = current_start
    for word in words:
        last_end = float(word["end"])
        if last_end - current_start >= WINDOW_S:
            windows.append((current_start, last_end))
            current_start = last_end
    if last_end - current_start >= MIN_WINDOW_S:
        windows.append((current_start, last_end))
    elif windows:
        windows[-1] = (windows[-1][0], last_end)
    else:
        windows.append((float(utterance["start"]), float(utterance["end"])))
    return windows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio", required=True)
    parser.add_argument("--transcript", required=True)
    parser.add_argument("--speakers", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    transcript = json.loads(Path(args.transcript).read_text(encoding="utf-8"))
    speakers = json.loads(Path(args.speakers).read_text(encoding="utf-8"))
    cache = Path(args.speakers).with_suffix(".embeddings.npz")
    if not cache.exists():
        print(f"[refine] ERRO: cache de embeddings ausente: {cache}", file=sys.stderr)
        return 1

    refined = refine(Path(args.audio), transcript, speakers, cache)
    Path(args.output).write_text(
        json.dumps(refined, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[refine] turnos finos: {len(refined['turns'])} | {refined['totals_s']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
