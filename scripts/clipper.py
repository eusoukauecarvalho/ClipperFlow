#!/usr/bin/env python3
"""Pipeline podcast-clipper: vídeo longo -> clips curtos + metadata.json.

Uso:
    python3 clipper.py --source entrevista.mp4 --output-dir output
    python3 clipper.py --source podcast.HEIC --max-clip-duration 45 --silence-threshold 1.5
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from config import (
    DEFAULT_LANGUAGE,
    DEFAULT_WHISPER_MODEL,
    MAX_CLIP_DURATION_S,
    MAX_CLIPS,
    MIN_CLIP_DURATION_S,
    SCALE_SHORT_SIDE,
    SILENCE_DB_THRESHOLD,
    SILENCE_DETECTION_FLOOR_S,
    SILENCE_EDGE_PADDING_S,
    SILENCE_REMOVAL_THRESHOLD_S,
    CPU_FRACTION_PER_JOB,
    MAX_PARALLEL_JOBS,
    SILENCE_SPLIT_THRESHOLD_S,
    VIDEO_ENCODER_AUTO,
    ClipperConfig,
)
from burn import BurnError
from ffmpeg_utils import (
    FFmpegError,
    format_timecode,
    probe_dimensions,
    probe_duration_s,
    probe_fps,
    require_ffmpeg,
)
from media import extract_audio, normalize_video
import adjustments
import burn as burn_module
import persona as persona_module
import subtitles
from adjustments import AdjustmentError, AdjustmentPlan
from render import render_clip
from segment import Clip, build_clips, score_clip, text_for_window
from silence import detect_silences
import transcript_cache
from subtitles import SUBTITLE_EXTENSION
from trimming import keep_ranges
from transcribe import Transcript, TranscriptionError, Utterance, transcribe

CLIPS_DIRNAME = "clips"
BURNED_DIRNAME = "legendados"
WORK_DIRNAME = ".work"
METADATA_FILENAME = "metadata.json"


def main(argv: list[str] | None = None) -> int:
    config = _config_from_args(_parse_args(argv))
    try:
        config.validate()
        require_ffmpeg()
        result = run_pipeline(config)
    except (ValueError, FFmpegError, TranscriptionError) as error:
        print(f"[podcast-clipper] ERRO: {error}", file=sys.stderr)
        return 1

    print(f"[podcast-clipper] {result['clips_generated']} clips em {config.output_dir}")
    return 0


def run_pipeline(config: ClipperConfig) -> dict:
    clips_dir = config.output_dir / CLIPS_DIRNAME
    workdir = config.output_dir / WORK_DIRNAME
    clips_dir.mkdir(parents=True, exist_ok=True)
    workdir.mkdir(parents=True, exist_ok=True)

    _log("normalizando vídeo")
    video_path = normalize_video(config.source, workdir)
    total_duration_s = probe_duration_s(video_path)

    _log("extraindo áudio")
    audio_path = extract_audio(video_path, workdir)

    _log("detectando silêncios")
    silences = detect_silences(
        audio_path,
        db_threshold=config.silence_db_threshold,
        min_duration_s=SILENCE_DETECTION_FLOOR_S,
        total_duration_s=total_duration_s,
    )

    transcript = _load_or_transcribe(audio_path, config)
    _log(f"transcrição via {transcript.engine}: {len(transcript.utterances)} falas")

    _log("montando clips")
    clips = build_clips(transcript.utterances, silences, total_duration_s, config)

    # ORDEM IMPORTA: ajustes primeiro (fixam a janela final start/end de cada clip),
    # persona depois (remove locutores alheios DESSA janela final). Na ordem inversa,
    # _apply_adjustments reconstrói removable_silences só a partir do silêncio acústico
    # e apaga silenciosamente qualquer corte de persona calculado antes — foi assim que
    # fala do outro locutor voltou a vazar num clip cuja janela tinha sido ajustada.
    if config.adjustments:
        plan = adjustments.load_plan(config.adjustments)
        adjustments.verify_fingerprint(plan, clips)
        before = len(clips)
        clips = _apply_adjustments(clips, plan, silences, transcript.utterances, config)
        _log(f"ajustes aplicados: {before} clips -> {len(clips)}")

    if config.speakers_file:
        speakers = persona_module.load_speakers(config.speakers_file)
        before_cuts = sum(len(c.removable_silences) for c in clips)
        clips = _apply_persona(clips, speakers, config)
        added = sum(len(c.removable_silences) for c in clips) - before_cuts
        _log(f"filtro de persona '{config.persona}': {added} trechos de outros locutores removidos")
    if not clips:
        _log("nenhum clip atendeu aos critérios — reduza --silence-threshold ou --min-clip-duration")

    rendered = (
        ()
        if config.dry_run
        else _render_all(video_path, clips, clips_dir, config, transcript.utterances)
    )

    metadata = _build_metadata(config, transcript, clips, total_duration_s, rendered)
    metadata_path = config.output_dir / METADATA_FILENAME
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if not config.keep_workdir:
        shutil.rmtree(workdir, ignore_errors=True)

    return metadata


def _load_or_transcribe(audio_path: Path, config: ClipperConfig) -> Transcript:
    """Reusa a transcrição em cache quando compatível; caso contrário transcreve e salva."""
    cache_file = transcript_cache.cache_path(config.output_dir)
    cached = transcript_cache.load(
        cache_file,
        language=config.language,
        model=config.whisper_model,
        require_words=config.word_timestamps,
    )
    if cached:
        _log(f"reusando transcrição em cache ({cache_file.name})")
        return cached

    _log(f"transcrevendo ({config.engine}) — etapa longa, resultado será cacheado")
    transcript = transcribe(
        audio_path,
        language=config.language,
        engine=config.engine,
        model=config.whisper_model,
        word_timestamps=config.word_timestamps,
    )
    transcript_cache.save(cache_file, transcript, model=config.whisper_model)
    return transcript


def _apply_adjustments(
    clips: tuple[Clip, ...],
    plan: AdjustmentPlan,
    silences: tuple,
    utterances: tuple,
    config: ClipperConfig,
) -> tuple[Clip, ...]:
    """Reescreve os cortes segundo o plano editorial, preservando os ids originais."""
    dropped = plan.removed_ids
    by_id = {clip.clip_id: clip for clip in clips}

    unknown = set(plan.clips) - set(by_id)
    if unknown:
        raise AdjustmentError(f"Ajuste para clip inexistente: {', '.join(sorted(unknown))}")

    kept: list[Clip] = []
    for clip in clips:
        if clip.clip_id in dropped:
            continue

        adjustment = plan.clips.get(clip.clip_id)
        if adjustment is None:
            kept.append(clip)
            continue

        absorbed_ends = [by_id[cid].end for cid in adjustment.absorb if cid in by_id]
        start, end = adjustments.resolve_window(
            adjustment, (clip.start, clip.end), absorbed_ends, utterances, plan
        )
        removable = adjustments.rebuild_silences(
            start, end, silences, config.silence_removal_threshold_s
        )
        text = text_for_window(utterances, start, end)
        kept.append(
            Clip(
                index=clip.index,
                start=start,
                end=end,
                text=text,
                score=score_clip(text, end - start),
                removable_silences=removable,
                edge_padding_s=config.silence_edge_padding_s,
            )
        )

    return tuple(kept)


def _apply_persona(
    clips: tuple[Clip, ...], speakers, config: ClipperConfig
) -> tuple[Clip, ...]:
    """Whitelist: mantém só os trechos onde a persona comprovadamente fala.

    Blacklist ("remova o outro locutor") deixa vazar fala mal rotulada — o defeito
    fica audível como tocos truncados. Aqui o complemento dos trechos da persona vira
    corte, então o incerto cai junto. O edge padding zera: a whitelist já apara as
    bordas, e devolver respiro reintroduziria o vazamento.
    """
    filtered: list[Clip] = []
    for clip in clips:
        keep = persona_module.persona_keep_spans(
            speakers, config.persona, clip.start, clip.end
        )
        cuts = persona_module.removable_from_keep_spans(keep, clip.start, clip.end)
        merged = tuple(sorted(
            (*clip.removable_silences, *cuts), key=lambda s: s.start
        ))
        filtered.append(
            Clip(
                index=clip.index, start=clip.start, end=clip.end, text=clip.text,
                score=clip.score, removable_silences=merged,
                edge_padding_s=0.0,
            )
        )
    return tuple(filtered)


def _resolve_jobs(config: ClipperConfig) -> int:
    """Quantos ffmpeg simultâneos. Cada um já usa várias threads, daí a divisão."""
    if config.jobs > 0:
        return config.jobs
    cores = os.cpu_count() or CPU_FRACTION_PER_JOB
    return max(1, min(MAX_PARALLEL_JOBS, cores // CPU_FRACTION_PER_JOB))


def _render_all(
    video_path: Path,
    clips: tuple[Clip, ...],
    clips_dir: Path,
    config: ClipperConfig,
    utterances: tuple[Utterance, ...],
) -> tuple[str, ...]:
    workers = _resolve_jobs(config)
    _log(f"renderizando {len(clips)} clips com {workers} processos paralelos")

    rendered: list[str] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending = {
            pool.submit(_render_one, video_path, clip, clips_dir, config, utterances): clip
            for clip in clips
        }
        for future in as_completed(pending):
            clip = pending[future]
            try:
                rendered.append(future.result())
            except (FFmpegError, OSError) as error:
                print(f"[podcast-clipper] falha em {clip.clip_id}: {error}", file=sys.stderr)

    _log(f"{len(rendered)}/{len(clips)} clips renderizados")
    return tuple(sorted(rendered))


def _render_one(
    video_path: Path,
    clip: Clip,
    clips_dir: Path,
    config: ClipperConfig,
    utterances: tuple[Utterance, ...],
) -> str:
    target = clips_dir / f"{clip.clip_id}.mp4"
    if config.skip_existing and target.exists():
        _log(f"{clip.clip_id} já existe, pulando render")
    else:
        render_clip(video_path, clip, target, config)
    if config.subtitles:
        _write_clip_subtitles(clip, clips_dir, config, utterances)
    if config.burn_subtitles:
        _burn_clip_subtitles(clip, clips_dir, config, utterances)
    return clip.clip_id


def _burn_clip_subtitles(
    clip: Clip, clips_dir: Path, config: ClipperConfig, utterances: tuple[Utterance, ...]
) -> None:
    """Grava uma cópia com a legenda queimada, para postar sem passar por editor."""
    source = clips_dir / f"{clip.clip_id}.mp4"
    burned_dir = config.output_dir / BURNED_DIRNAME
    burned_dir.mkdir(parents=True, exist_ok=True)
    target = burned_dir / f"{clip.clip_id}.mp4"
    if config.skip_existing and target.exists():
        return

    width, height = probe_dimensions(source)
    fps = probe_fps(source)
    hold = (
        config.zoom_hold_s
        if config.zoom_hold_s > 0
        else burn_module.hold_for_duration(clip.final_duration)
    )
    style = burn_module.BurnStyle(
        font_size_ratio=config.subtitle_size_ratio,
        signature_width_ratio=config.signature_size_ratio,
        zoom_amplitude=config.zoom_amplitude,
        zoom_transition_s=config.zoom_transition_s,
        zoom_hold_s=hold,
    )
    workdir = burned_dir / ".cues" / clip.clip_id
    try:
        if config.subtitle_style == burn_module.STYLE_BLOCK:
            srt = clips_dir / f"{clip.clip_id}{SUBTITLE_EXTENSION}"
            if not srt.exists():
                return
            burn_module.burn(
                source, srt, target, workdir,
                video_width=width, video_height=height,
                font_path=config.subtitle_font, signature_path=config.signature,
                style=style, fps=fps,
            )
            return

        inside = tuple(u for u in utterances if u.end > clip.start and u.start < clip.end)
        cues = burn_module.build_word_cues(
            inside, clip.start, clip.keep_ranges, style=config.subtitle_style
        )
        burn_module.burn_word_cues(
            source, cues, target, workdir,
            video_width=width, video_height=height,
            font_path=config.subtitle_font, signature_path=config.signature,
            style=style, fps=fps,
        )
    except BurnError as error:
        print(f"[podcast-clipper] legenda de {clip.clip_id}: {error}", file=sys.stderr)


def _write_clip_subtitles(
    clip: Clip, clips_dir: Path, config: ClipperConfig, utterances: tuple[Utterance, ...]
) -> None:
    """Gera o .srt do clip, com tempos já compensados pelos silêncios removidos."""
    ranges = keep_ranges(
        clip.start, clip.end, clip.removable_silences, config.silence_edge_padding_s
    )
    inside = tuple(u for u in utterances if u.end > clip.start and u.start < clip.end)
    entries = subtitles.build_entries(inside, ranges)
    if entries:
        subtitles.write_srt(entries, clips_dir / f"{clip.clip_id}{SUBTITLE_EXTENSION}")


def _build_metadata(
    config: ClipperConfig,
    transcript: Transcript,
    clips: tuple[Clip, ...],
    total_duration_s: float,
    rendered: tuple[str, ...],
) -> dict:
    rendered_set = set(rendered)
    return {
        "source": config.source.name,
        "total_duration": format_timecode(total_duration_s),
        "clips_generated": len(rendered_set) if not config.dry_run else 0,
        "clips_planned": len(clips),
        "transcription_engine": transcript.engine,
        "language": transcript.language,
        "parameters": {
            "min_clip_duration_s": config.min_clip_duration_s,
            "max_clip_duration_s": config.max_clip_duration_s,
            "silence_split_threshold_s": config.silence_split_threshold_s,
            "silence_db_threshold": config.silence_db_threshold,
            "silence_removal_threshold_s": config.silence_removal_threshold_s,
            "scale_short_side": config.scale_short_side,
            "video_encoder": config.video_encoder,
        },
        "full_transcription": transcript.full_text,
        "clips": [
            {
                "id": clip.clip_id,
                "filename": f"{clip.clip_id}.mp4",
                "subtitles": f"{clip.clip_id}{SUBTITLE_EXTENSION}" if config.subtitles else None,
                "rendered": clip.clip_id in rendered_set,
                "start": format_timecode(clip.start),
                "end": format_timecode(clip.end),
                "start_seconds": round(clip.start, 3),
                "end_seconds": round(clip.end, 3),
                "original_duration": f"{clip.original_duration:.1f}s",
                "silence_removed": f"{clip.removed_silence_duration:.1f}s",
                "final_duration": f"{clip.final_duration:.1f}s",
                "transcription": clip.text,
                "importance_score": clip.score,
            }
            for clip in clips
        ],
    }


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="podcast-clipper",
        description="Extrai clips curtos de podcasts/entrevistas usando pausas naturais.",
    )
    parser.add_argument("--source", required=True, help="Vídeo de entrada (.mp4, .mov, .HEIC...)")
    parser.add_argument("--output-dir", default="output", help="Diretório de saída")
    parser.add_argument("--min-clip-duration", type=float, default=MIN_CLIP_DURATION_S)
    parser.add_argument("--max-clip-duration", type=float, default=MAX_CLIP_DURATION_S)
    parser.add_argument("--silence-threshold", type=float, default=SILENCE_SPLIT_THRESHOLD_S)
    parser.add_argument("--silence-db", type=float, default=SILENCE_DB_THRESHOLD)
    parser.add_argument("--silence-removal", type=float, default=SILENCE_REMOVAL_THRESHOLD_S)
    parser.add_argument("--edge-padding", type=float, default=SILENCE_EDGE_PADDING_S)
    parser.add_argument("--max-clips", type=int, default=MAX_CLIPS)
    parser.add_argument(
        "--scale-short-side",
        type=int,
        default=SCALE_SHORT_SIDE,
        help="Lado MENOR da saída em px (1080 = Full HD vertical ou horizontal); 0 mantém o original",
    )
    parser.add_argument(
        "--video-encoder",
        default=VIDEO_ENCODER_AUTO,
        help="auto (usa VideoToolbox se houver), libx264, h264_videotoolbox...",
    )
    parser.add_argument("--language", default=DEFAULT_LANGUAGE)
    parser.add_argument(
        "--engine",
        default="auto",
        choices=("auto", "meetily", "faster-whisper", "openai-whisper", "whisper-cli"),
    )
    parser.add_argument("--whisper-model", default=DEFAULT_WHISPER_MODEL)
    parser.add_argument(
        "--jobs", type=int, default=0, help="Renders simultâneos; 0 deriva dos núcleos"
    )
    parser.add_argument(
        "--no-subtitles", action="store_true", help="Não gerar os .srt ao lado dos clips"
    )
    parser.add_argument(
        "--burn-subtitles", action="store_true",
        help="Gerar também uma cópia com a legenda queimada em output/legendados/",
    )
    parser.add_argument("--subtitle-font", help="Caminho de um .ttf para a legenda queimada")
    parser.add_argument("--speakers", help="speakers.json gerado por diarize.py")
    parser.add_argument("--persona", default="", help="Locutor da câmera (ex.: S1) — os demais são removidos dos clips")
    parser.add_argument("--signature", help="PNG transparente fixado no rodapé de todo clip")
    parser.add_argument(
        "--zoom", type=float, default=0.12,
        help="Amplitude do zoom que entra e sai (0.03 = 3%%); 0 desliga",
    )
    parser.add_argument("--zoom-transition", type=float, default=4.0, help="Segundos de ida (e volta) do zoom")
    parser.add_argument(
        "--zoom-hold", type=float, default=0.0,
        help="Segundos parado em cada extremo; 0 escolhe pela duração do clip (5s até 30s, 20s acima)",
    )
    parser.add_argument("--subtitle-size", type=float, default=0.0726, help="Fonte / largura do vídeo")
    parser.add_argument("--signature-size", type=float, default=0.52, help="Assinatura / largura do vídeo")
    parser.add_argument(
        "--word-timestamps", action="store_true",
        help="Transcrever com tempo por palavra (necessário para karaokê)",
    )
    parser.add_argument(
        "--subtitle-style", default="block", choices=("block", "karaoke", "pop"),
        help="block: frase inteira | karaoke: frase apagada com a palavra acesa | pop: uma palavra por vez",
    )
    parser.add_argument(
        "--adjustments", help="JSON com ajustes editoriais (estender, absorver, remover)"
    )
    parser.add_argument(
        "--skip-existing", action="store_true",
        help="Não re-renderizar clips cujo MP4 já existe (retomar run interrompido)",
    )
    parser.add_argument("--keep-workdir", action="store_true", help="Mantém áudio/temporários")
    parser.add_argument(
        "--dry-run", action="store_true", help="Só planeja e escreve metadata.json"
    )
    return parser.parse_args(argv)


def _config_from_args(args: argparse.Namespace) -> ClipperConfig:
    return ClipperConfig(
        source=Path(args.source).expanduser(),
        output_dir=Path(args.output_dir).expanduser(),
        min_clip_duration_s=args.min_clip_duration,
        max_clip_duration_s=args.max_clip_duration,
        silence_split_threshold_s=args.silence_threshold,
        silence_db_threshold=args.silence_db,
        silence_removal_threshold_s=args.silence_removal,
        silence_edge_padding_s=args.edge_padding,
        max_clips=args.max_clips,
        scale_short_side=args.scale_short_side,
        video_encoder=args.video_encoder,
        language=args.language,
        engine=args.engine,
        whisper_model=args.whisper_model,
        subtitles=not args.no_subtitles,
        burn_subtitles=args.burn_subtitles,
        subtitle_font=args.subtitle_font,
        word_timestamps=args.word_timestamps or args.subtitle_style != "block",
        subtitle_style=args.subtitle_style,
        signature=Path(args.signature).expanduser() if args.signature else None,
        speakers_file=Path(args.speakers).expanduser() if args.speakers else None,
        persona=args.persona,
        zoom_amplitude=args.zoom,
        zoom_transition_s=args.zoom_transition,
        zoom_hold_s=args.zoom_hold,
        subtitle_size_ratio=args.subtitle_size,
        signature_size_ratio=args.signature_size,
        jobs=args.jobs,
        skip_existing=args.skip_existing,
        adjustments=Path(args.adjustments).expanduser() if args.adjustments else None,
        keep_workdir=args.keep_workdir,
        dry_run=args.dry_run,
    )


def _log(message: str) -> None:
    print(f"[podcast-clipper] {message}", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
