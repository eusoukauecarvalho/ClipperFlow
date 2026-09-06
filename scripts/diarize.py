#!/usr/bin/env python3
"""Diarização: quem fala em cada trecho do áudio.

Num podcast, a câmera de cada arquivo aponta para UMA pessoa, mas o áudio tem todas.
Cortar sem saber quem fala produz clips do convidado calado ouvindo o apresentador.
Este módulo atribui um locutor a cada fala da transcrição:

    1. Extrai um embedding de voz (ECAPA-TDNN, speechbrain) por fala.
    2. Agrupa os embeddings em N locutores (aglomerativo, cosseno).
    3. Grava `speakers.json` com o locutor de cada fala.

O rótulo "quem é a persona da câmera" é decisão humana: o módulo mostra amostras de
cada locutor e o usuário (ou o modelo, lendo as amostras) aponta qual é.

Uso:
    python3 diarize.py --audio out/.work/audio.wav --transcript out/transcript.json \\
        --output out/speakers.json --speakers 2
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

MIN_UTTERANCE_S = 0.6      # fala curta demais não tem voz suficiente para embedding
MAX_SEGMENT_S = 6.0        # identidade de voz não precisa de mais; acelera o batch
SAMPLE_RATE = 16000
EMBEDDING_BATCH = 64
MERGE_GAP_S = 0.25          # falas do mesmo locutor separadas por menos que isso viram um turno


class DiarizationError(RuntimeError):
    """Falha ao diarizar o áudio."""


def _load_wav_mono(audio_path: Path):
    """Lê WAV PCM16 mono com a stdlib — o pipeline sempre extrai nesse formato.

    torchaudio.load passou a exigir torchcodec; para um WAV que nós mesmos geramos,
    isso é dependência sem função.
    """
    import wave

    import numpy as np

    with wave.open(str(audio_path), "rb") as reader:
        if reader.getframerate() != SAMPLE_RATE or reader.getsampwidth() != 2:
            raise DiarizationError(
                f"Esperado WAV PCM16 {SAMPLE_RATE} Hz (o formato que media.extract_audio gera); "
                f"recebi {reader.getframerate()} Hz, {reader.getsampwidth() * 8} bits"
            )
        frames = reader.readframes(reader.getnframes())
        samples = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
        if reader.getnchannels() > 1:
            samples = samples.reshape(-1, reader.getnchannels()).mean(axis=1)
    return samples


@dataclass(frozen=True)
class SpeakerTurn:
    start: float
    end: float
    speaker: str


def diarize(
    audio_path: Path,
    utterances: list[dict],
    *,
    num_speakers: int = 2,
    embeddings_cache: Path | None = None,
) -> list[str | None]:
    """Locutor de cada fala (None quando curta demais para classificar)."""
    try:
        import numpy as np
        import torch
        from scipy.cluster.vq import kmeans2
        from speechbrain.inference.speaker import EncoderClassifier
    except ImportError as error:
        raise DiarizationError(
            f"Dependência ausente ({error.name}). Instale com: pip3 install speechbrain scipy"
        ) from error

    mono = torch.from_numpy(_load_wav_mono(audio_path))

    encoder = EncoderClassifier.from_hparams(
        source="speechbrain/spkrec-ecapa-voxceleb",
        savedir=str(Path.home() / ".cache" / "speechbrain" / "ecapa"),
        run_opts={"device": "cpu"},
    )

    usable: list[int] = []
    segments: list[torch.Tensor] = []
    max_samples = int(MAX_SEGMENT_S * SAMPLE_RATE)
    for index, utterance in enumerate(utterances):
        start = int(float(utterance["start"]) * SAMPLE_RATE)
        end = int(float(utterance["end"]) * SAMPLE_RATE)
        if (end - start) / SAMPLE_RATE < MIN_UTTERANCE_S:
            continue
        chunk = mono[start : min(end, start + max_samples)]
        if chunk.numel() < SAMPLE_RATE // 4:
            continue
        usable.append(index)
        segments.append(chunk)

    if len(usable) < num_speakers * 2:
        raise DiarizationError(
            f"Só {len(usable)} falas utilizáveis — insuficiente para {num_speakers} locutores"
        )

    matrix = _cached_embeddings(embeddings_cache, usable)
    if matrix is None:
        matrix = _compute_embeddings(segments, encoder, np, torch)
        if embeddings_cache is not None:
            np.savez_compressed(embeddings_cache, usable=np.array(usable), matrix=matrix)
    else:
        print(f"[diarize] reusando {len(usable)} embeddings do cache", flush=True)

    matrix = matrix / np.linalg.norm(matrix, axis=1, keepdims=True)

    # KMeans em vez de aglomerativo: com duas vozes na MESMA sala e no mesmo canal, o
    # aglomerativo separa um outlier (vinheta, ruído) e junta os locutores num cluster
    # só — foi exatamente o que aconteceu no primeiro episódio (S2 com 6s de 52min).
    _, flat = kmeans2(matrix, num_speakers, minit="++", seed=7)
    flat = [int(c) + 1 for c in flat]

    counts: dict[int, int] = {}
    for cluster in flat:
        counts[cluster] = counts.get(cluster, 0) + 1
    smallest = min(counts.values()) / len(flat)
    if smallest < 0.05:
        print(
            f"[diarize] AVISO: menor locutor tem só {smallest:.0%} das falas — "
            "o agrupamento pode ter falhado (vozes parecidas ou num_speakers errado)",
            flush=True,
        )

    # Rótulos por ordem de aparição: quem fala primeiro vira S1.
    order: dict[int, str] = {}
    labels: list[str | None] = [None] * len(utterances)
    for position, utterance_index in enumerate(usable):
        cluster = int(flat[position])
        if cluster not in order:
            order[cluster] = f"S{len(order) + 1}"
        labels[utterance_index] = order[cluster]

    labels = _fill_gaps(labels, utterances)
    return labels


def _cached_embeddings(cache_path, usable):
    import numpy as np

    if cache_path is None or not Path(cache_path).exists():
        return None
    data = np.load(cache_path)
    if list(data["usable"]) != usable:
        return None  # transcrição mudou; cache inválido
    return data["matrix"]


def _compute_embeddings(segments, encoder, np, torch):
    embeddings = []
    for batch_start in range(0, len(segments), EMBEDDING_BATCH):
        batch = segments[batch_start : batch_start + EMBEDDING_BATCH]
        max_len = max(c.numel() for c in batch)
        padded = torch.zeros(len(batch), max_len)
        lengths = torch.zeros(len(batch))
        for row, chunk in enumerate(batch):
            padded[row, : chunk.numel()] = chunk
            lengths[row] = chunk.numel() / max_len
        with torch.no_grad():
            emb = encoder.encode_batch(padded, lengths).squeeze(1)
        embeddings.append(emb.cpu().numpy())
        print(
            f"[diarize] embeddings {min(batch_start + EMBEDDING_BATCH, len(segments))}"
            f"/{len(segments)}",
            flush=True,
        )

    return np.concatenate(embeddings, axis=0)


def _fill_gaps(labels: list[str | None], utterances: list[dict]) -> list[str | None]:
    known = [i for i, label in enumerate(labels) if label]
    if not known:
        return labels

    filled = list(labels)
    for index, label in enumerate(labels):
        if label:
            continue
        nearest = min(
            known,
            key=lambda k: abs(
                float(utterances[k]["start"]) - float(utterances[index]["start"])
            ),
        )
        filled[index] = labels[nearest]
    return filled


def build_turns(utterances: list[dict], labels: list[str | None]) -> list[SpeakerTurn]:
    """Falas consecutivas do mesmo locutor viram turnos contínuos."""
    turns: list[SpeakerTurn] = []
    for utterance, label in zip(utterances, labels):
        if label is None:
            continue
        start, end = float(utterance["start"]), float(utterance["end"])
        if turns and turns[-1].speaker == label and start - turns[-1].end <= MERGE_GAP_S:
            turns[-1] = SpeakerTurn(turns[-1].start, end, label)
            continue
        turns.append(SpeakerTurn(start, end, label))
    return turns


def speaker_samples(
    utterances: list[dict], labels: list[str | None], per_speaker: int = 5
) -> dict[str, list[str]]:
    """Frases de exemplo de cada locutor, para o humano identificar quem é quem."""
    samples: dict[str, list[str]] = {}
    for utterance, label in zip(utterances, labels):
        if label is None:
            continue
        text = str(utterance.get("text", "")).strip()
        if len(text) < 25:
            continue
        bucket = samples.setdefault(label, [])
        if len(bucket) < per_speaker:
            bucket.append(text)
    return samples


def main() -> int:
    parser = argparse.ArgumentParser(description="Atribui um locutor a cada fala.")
    parser.add_argument("--audio", required=True)
    parser.add_argument("--transcript", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--speakers", type=int, default=2)
    args = parser.parse_args()

    transcript = json.loads(Path(args.transcript).read_text(encoding="utf-8"))
    utterances = transcript["utterances"]

    try:
        labels = diarize(
            Path(args.audio),
            utterances,
            num_speakers=args.speakers,
            embeddings_cache=Path(args.output).with_suffix(".embeddings.npz"),
        )
    except DiarizationError as error:
        print(f"[diarize] ERRO: {error}", file=sys.stderr)
        return 1

    turns = build_turns(utterances, labels)
    totals: dict[str, float] = {}
    for turn in turns:
        totals[turn.speaker] = totals.get(turn.speaker, 0.0) + (turn.end - turn.start)

    payload = {
        "num_speakers": args.speakers,
        "totals_s": {k: round(v, 1) for k, v in sorted(totals.items())},
        "samples": speaker_samples(utterances, labels),
        "utterances": [
            {"start": u["start"], "end": u["end"], "speaker": label}
            for u, label in zip(utterances, labels)
        ],
        "turns": [
            {"start": round(t.start, 2), "end": round(t.end, 2), "speaker": t.speaker}
            for t in turns
        ],
    }
    Path(args.output).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"[diarize] {len(turns)} turnos | tempo por locutor: {payload['totals_s']}")
    for speaker, sample_list in payload["samples"].items():
        print(f"\n== {speaker} ==")
        for sample in sample_list[:3]:
            print(f"   {sample[:100]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
