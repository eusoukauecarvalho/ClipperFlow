"""Queima legendas no vídeo sem depender de libass.

O ffmpeg do Homebrew vem sem `libass` e sem `drawtext`, então os filtros usuais de
legenda não existem. A saída é renderizar cada cartão como PNG transparente (Pillow) e
sobrepor com o filtro `overlay`, que está sempre presente, usando `enable=between(t,...)`
para cada janela de tempo.

Estilo pensado para vertical de rede social: texto branco em negrito, contorno preto
grosso, na faixa inferior mas acima da área de UI do Reels/TikTok.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from ffmpeg_utils import run

TIMECODE_PATTERN = re.compile(
    r"(\d{2}):(\d{2}):(\d{2}),(\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2}),(\d{3})"
)

DEFAULT_FONT = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
FONT_FALLBACKS = (
    DEFAULT_FONT,
    "/System/Library/Fonts/Supplemental/Arial Black.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/Library/Fonts/Arial Bold.ttf",
)

# Proporções relativas à largura do vídeo — mantêm o estilo igual em qualquer resolução.
FONT_SIZE_RATIO = 0.055
STROKE_RATIO = 0.16
LINE_SPACING_RATIO = 0.28
TEXT_WIDTH_RATIO = 0.88
BOTTOM_MARGIN_RATIO = 0.16  # acima da UI de Reels/TikTok
SHADOW_OFFSET_RATIO = 0.05

TEXT_COLOR = (255, 255, 255, 255)
STROKE_COLOR = (0, 0, 0, 255)
SHADOW_COLOR = (0, 0, 0, 150)

# Karaokê: a frase inteira fica visível apagada e só a palavra falada acende.
# O contorno também perde opacidade junto, senão as palavras apagadas ficam com
# a borda preta cheia e o contraste some.
DIM_TEXT_COLOR = (255, 255, 255, 115)
DIM_STROKE_COLOR = (0, 0, 0, 140)
ACTIVE_TEXT_COLOR = (255, 255, 255, 255)

# Assinatura fixa (foto + @ + selo) sobreposta em todos os quadros.
OVERLAY_WIDTH_RATIO = 0.82
OVERLAY_BOTTOM_MARGIN_RATIO = 0.055
SUBTITLE_LIFT_WITH_OVERLAY = 0.06  # sobe a legenda para não encostar na assinatura

# Palavra curta ("é", "que", "a") sozinha na tela pisca e não dá tempo de ler.
# Abaixo deste tempo, ela é mostrada junto com a palavra seguinte.
MIN_WORD_SCREEN_S = 0.38

# A pausa do zoom acompanha a duração do clip: corte curto não comporta pausa longa
# (não sobraria tempo para o movimento), corte longo com pausa curta vira vaivém.
ZOOM_HOLD_TIERS = ((30.0, 5.0), (60.0, 20.0))
ZOOM_HOLD_DEFAULT_S = 20.0


def hold_for_duration(duration_s: float) -> float:
    """Segundos de pausa do zoom para um clip desta duração."""
    for limit, hold in ZOOM_HOLD_TIERS:
        if duration_s <= limit:
            return hold
    return ZOOM_HOLD_DEFAULT_S


STYLE_BLOCK = "block"
STYLE_KARAOKE = "karaoke"
STYLE_POP = "pop"
SUBTITLE_STYLES = (STYLE_BLOCK, STYLE_KARAOKE, STYLE_POP)


@dataclass(frozen=True)
class BurnStyle:
    """Aparência da legenda queimada. Tudo relativo ao vídeo, então independe da resolução."""

    font_size_ratio: float = 0.0726
    stroke_ratio: float = 0.0     # sem contorno; a sombra sustenta a legibilidade
    shadow_ratio: float = 0.055
    dim_alpha: int = 115
    subtitle_bottom_ratio: float = 0.115
    signature_width_ratio: float = 0.52
    signature_bottom_ratio: float = 0.095
    zoom_amplitude: float = 0.0     # 0 desliga; 0.08 = aproxima 8%
    zoom_transition_s: float = 4.0  # tempo de ida (e de volta)
    zoom_hold_s: float = 10.0        # pausa parada em cada extremo
    zoom_upscale: int = 2           # anti-jitter: zoompan trunca a origem em pixel inteiro

    @property
    def zoom_cycle_s(self) -> float:
        """Ida + pausa + volta + pausa."""
        return 2 * (self.zoom_transition_s + self.zoom_hold_s)

    def font_size(self, video_width: int) -> int:
        return max(12, int(video_width * self.font_size_ratio))

    def stroke(self, video_width: int) -> int:
        return int(self.font_size(video_width) * self.stroke_ratio)

    def shadow(self, video_width: int) -> int:
        return max(1, int(self.font_size(video_width) * self.shadow_ratio))

    def dim_text(self) -> tuple[int, int, int, int]:
        return (255, 255, 255, self.dim_alpha)

    def dim_stroke(self) -> tuple[int, int, int, int]:
        return (0, 0, 0, min(255, int(self.dim_alpha * 1.2)))


DEFAULT_STYLE = BurnStyle()


class BurnError(RuntimeError):
    """Falha ao queimar as legendas."""


@dataclass(frozen=True)
class Cue:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class WordCue:
    """Um quadro de karaokê: a frase toda, com uma palavra acesa."""

    start: float
    end: float
    words: tuple[str, ...]
    active: int  # índice da palavra acesa; -1 acende nenhuma


def parse_srt(path: Path) -> tuple[Cue, ...]:
    """Lê um .srt em cues. Ignora blocos malformados em vez de quebrar o render."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as error:
        raise BurnError(f"Não foi possível ler {path}: {error}") from error

    cues: list[Cue] = []
    for block in raw.strip().split("\n\n"):
        lines = block.strip().split("\n")
        if len(lines) < 3:
            continue
        match = TIMECODE_PATTERN.search(lines[1])
        if not match:
            continue
        start = _to_seconds(match.groups()[:4])
        end = _to_seconds(match.groups()[4:])
        text = "\n".join(lines[2:]).strip()
        if text and end > start:
            cues.append(Cue(start, end, text))
    return tuple(cues)


def _to_seconds(parts: Sequence[str]) -> float:
    hours, minutes, seconds, millis = (int(p) for p in parts)
    return hours * 3600 + minutes * 60 + seconds + millis / 1000


def resolve_font(font_path: str | None = None) -> str:
    candidates = (font_path, *FONT_FALLBACKS) if font_path else FONT_FALLBACKS
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    raise BurnError(
        "Nenhuma fonte encontrada. Passe --subtitle-font com o caminho de um .ttf"
    )


def render_cue(
    cue: Cue, index: int, video_width: int, workdir: Path, font_path: str,
    style: "BurnStyle | None" = None,
) -> Path:
    """Desenha um cartão de legenda como PNG transparente da largura do vídeo."""
    from PIL import Image, ImageDraw, ImageFont  # import tardio: dependência opcional

    style = style or DEFAULT_STYLE
    font_size = style.font_size(video_width)
    stroke = style.stroke(video_width)
    spacing = int(font_size * LINE_SPACING_RATIO)
    shadow = style.shadow(video_width)

    font = ImageFont.truetype(font_path, font_size)
    lines = _fit_lines(cue.text, font, int(video_width * TEXT_WIDTH_RATIO))

    line_height = font_size + spacing
    padding = max(stroke * 2, shadow * 3)
    height = line_height * len(lines) + padding * 2
    canvas = Image.new("RGBA", (video_width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    for position, line in enumerate(lines):
        y = padding + position * line_height
        draw.text(
            (video_width // 2 + shadow, y + shadow), line, font=font,
            fill=SHADOW_COLOR, anchor="ma",
        )
        draw.text(
            (video_width // 2, y), line, font=font, fill=TEXT_COLOR,
            stroke_width=stroke, stroke_fill=STROKE_COLOR, anchor="ma",
        )

    target = workdir / f"cue_{index:04d}.png"
    canvas.save(target)
    return target


def _fit_lines(text: str, font, max_width: int) -> list[str]:
    """Reflui o texto na largura disponível, sem deixar palavra órfã na última linha.

    As quebras do .srt são descartadas: elas foram calculadas por contagem de caracteres,
    e aqui a medida real é a largura em pixels da fonte usada.
    """
    words = text.split()
    if not words:
        return [text]

    lines = _greedy_wrap(words, font, max_width)
    return [" ".join(line) for line in _pull_back_orphans(lines, font, max_width)]


def _greedy_wrap(words: list[str], font, max_width: int) -> list[list[str]]:
    lines: list[list[str]] = []
    current: list[str] = []

    for word in words:
        probe = " ".join([*current, word])
        if current and font.getbbox(probe)[2] > max_width:
            lines.append(current)
            current = [word]
            continue
        current.append(word)

    if current:
        lines.append(current)
    return lines


def _pull_back_orphans(lines: list[list[str]], font, max_width: int) -> list[list[str]]:
    """Puxa palavras da linha anterior enquanto a última linha tiver uma palavra só.

    Uma palavra sozinha no fim ("aquilo") desequilibra o bloco e chama atenção
    para a quebra em vez do texto.
    """
    if len(lines) < 2:
        return lines

    balanced = [list(line) for line in lines]
    while len(balanced[-1]) == 1 and len(balanced[-2]) > 2:
        candidate = [balanced[-2][-1], *balanced[-1]]
        if font.getbbox(" ".join(candidate))[2] > max_width:
            break
        balanced[-1] = candidate
        balanced[-2] = balanced[-2][:-1]
    return balanced


def build_zoom_filter(style: BurnStyle, width: int, height: int, fps: float) -> str:
    """Zoom que aproxima, PARA, volta e para de novo.

    Um cosseno contínuo nunca descansa e o movimento vira um vaivém perceptível. Aqui o
    ciclo é: ida suave (transition) -> parado no ponto fechado (hold) -> volta suave ->
    parado no ponto aberto. As transições usam meia onda de cosseno, então a partida e a
    chegada têm velocidade zero e não há solavanco ao entrar ou sair da pausa.

    O upscale antes do zoompan existe porque o filtro trunca a origem do corte em pixel
    inteiro — no dobro da resolução, cada salto vale meio pixel na saída.
    """
    if style.zoom_amplitude <= 0:
        return ""

    transition = max(0.1, style.zoom_transition_s)
    hold = max(0.0, style.zoom_hold_s)
    cycle = style.zoom_cycle_s
    phase = f"mod(on/{fps:.6f},{cycle:.3f})"

    going = f"(1-cos(PI*{phase}/{transition:.3f}))/2"
    returning = f"(1+cos(PI*({phase}-{transition + hold:.3f})/{transition:.3f}))/2"
    shape = (
        f"if(lt({phase},{transition:.3f}),{going},"
        f"if(lt({phase},{transition + hold:.3f}),1,"
        f"if(lt({phase},{2 * transition + hold:.3f}),{returning},0)))"
    )
    zoom_expr = f"1.0+{style.zoom_amplitude:.4f}*({shape})"

    upscale = max(1, style.zoom_upscale)
    prefix = f"scale=iw*{upscale}:ih*{upscale}," if upscale > 1 else ""
    return (
        f"{prefix}zoompan=z='{zoom_expr}':d=1"
        f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        f":s={width}x{height}:fps={fps:.6f}"
    )


def build_overlay_filter(
    count: int, video_height: int, *, has_signature: bool = False,
    style: BurnStyle = DEFAULT_STYLE, zoom_filter: str = "",
) -> tuple[str, str]:
    """Encadeia um overlay por cartão, cada um ativo só na sua janela de tempo.

    Com assinatura fixa, a legenda sobe para não encostar nela. A assinatura entra como
    o ÚLTIMO input do ffmpeg e é desenhada por primeiro, ficando embaixo dos cartões.
    """
    lift = SUBTITLE_LIFT_WITH_OVERLAY if has_signature else 0.0
    margin = int(video_height * (style.subtitle_bottom_ratio + lift))
    steps: list[str] = []
    current = "[0:v]"

    # O zoom entra ANTES das sobreposições: só a imagem respira, texto e assinatura
    # ficam parados. Se viesse depois, a legenda inteira escalaria junto.
    if zoom_filter:
        steps.append(f"{current}{zoom_filter}[zoomed]")
        current = "[zoomed]"

    if has_signature:
        signature_margin = int(video_height * style.signature_bottom_ratio)
        steps.append(
            f"{current}[{count + 1}:v]overlay=x=(W-w)/2:y=H-h-{signature_margin}[sig]"
        )
        current = "[sig]"

    for index in range(count):
        label = f"[v{index}]"
        steps.append(
            f"{current}[{index + 1}:v]overlay="
            f"x=0:y=H-h-{margin}:enable='between(t,{{start{index}}},{{end{index}}})'{label}"
        )
        current = label

    return ";".join(steps), current


def prepare_signature(
    image_path: Path, video_width: int, workdir: Path, style: BurnStyle = DEFAULT_STYLE
) -> Path:
    """Recorta o espaço vazio da assinatura e escala para a largura do vídeo.

    O PNG original costuma ser quadrado com muita área transparente; sem o recorte a
    barra ficaria minúscula ao escalar pela largura total.
    """
    from PIL import Image

    source = Image.open(image_path).convert("RGBA")
    bbox = source.getchannel("A").getbbox()
    if bbox:
        source = source.crop(bbox)

    target_width = max(1, int(video_width * style.signature_width_ratio))
    ratio = target_width / source.width
    resized = source.resize(
        (target_width, max(1, int(source.height * ratio))), Image.LANCZOS
    )

    workdir.mkdir(parents=True, exist_ok=True)
    target = workdir / "signature.png"
    resized.save(target)
    return target


def burn(
    video_path: Path,
    srt_path: Path,
    output_path: Path,
    workdir: Path,
    *,
    video_width: int,
    video_height: int,
    font_path: str | None = None,
    signature_path: Path | None = None,
    style: BurnStyle | None = None,
    fps: float = 0.0,
    encoder_args: Sequence[str] = ("-c:v", "libx264", "-crf", "20", "-preset", "veryfast"),
) -> Path:
    """Gera uma cópia do vídeo com as legendas queimadas."""
    style = style or DEFAULT_STYLE
    cues = parse_srt(srt_path)
    if not cues:
        raise BurnError(f"{srt_path.name} não tem legendas utilizáveis")

    workdir.mkdir(parents=True, exist_ok=True)
    font = resolve_font(font_path)
    images = [
        render_cue(cue, index, video_width, workdir, font, style)
        for index, cue in enumerate(cues)
    ]
    signature = (
        prepare_signature(signature_path, video_width, workdir, style)
        if signature_path
        else None
    )

    zoom_filter = build_zoom_filter(style, video_width, video_height, fps) if fps else ""
    filter_template, final_label = build_overlay_filter(
        len(cues), video_height, has_signature=signature is not None, style=style,
        zoom_filter=zoom_filter,
    )
    filter_complex = filter_template.format(
        **{
            key: f"{value:.3f}"
            for index, cue in enumerate(cues)
            for key, value in ((f"start{index}", cue.start), (f"end{index}", cue.end))
        }
    )

    command = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(video_path)]
    for image in images:
        command += ["-i", str(image)]
    if signature:
        command += ["-i", str(signature)]
    command += [
        "-filter_complex", filter_complex,
        "-map", final_label, "-map", "0:a?",
        *encoder_args,
        "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags", "+faststart",
        str(output_path),
    ]
    run(command)
    return output_path


# --- karaokê -----------------------------------------------------------------


def build_word_cues(
    utterances: Sequence, clip_start: float, keep_ranges: Sequence[tuple[float, float]],
    *, style: str, max_words: int = 6,
) -> tuple[WordCue, ...]:
    """Um cue por palavra falada, em tempo relativo ao clip.

    `style` decide o que aparece em volta da palavra acesa: em `karaoke` a frase toda
    fica visível apagada; em `pop` só a palavra atual aparece.
    """
    from subtitles import map_to_clip_time

    cues: list[WordCue] = []
    for utterance in utterances:
        words = getattr(utterance, "words", ())
        if not words:
            continue
        readable = _merge_short_words(list(words))
        for group in _group_words(readable, max_words):
            texts = tuple(w.text for w in group)
            for index, word in enumerate(group):
                start = map_to_clip_time(word.start, keep_ranges)
                end = map_to_clip_time(word.end, keep_ranges)
                if start is None or end is None or end <= start:
                    continue
                visible = texts if style == STYLE_KARAOKE else (word.text,)
                active = index if style == STYLE_KARAOKE else 0
                cues.append(WordCue(start, end, visible, active))
    return tuple(cues)


def _merge_short_words(words: list, min_screen_s: float = MIN_WORD_SCREEN_S) -> list:
    """Junta palavras curtas demais com a seguinte, para nenhuma piscar na tela."""
    from transcribe import Word

    merged: list = []
    pending: list = []

    for word in words:
        pending.append(word)
        if pending[-1].end - pending[0].start >= min_screen_s:
            merged.append(_join(pending, Word))
            pending = []

    if pending:
        tail = _join(pending, Word)
        if merged:
            # Sobra no fim: gruda no último em vez de virar um flash solto.
            previous = merged.pop()
            merged.append(
                Word(previous.start, tail.end, f"{previous.text} {tail.text}".strip())
            )
        else:
            merged.append(tail)

    return merged


def _join(words: list, word_class):
    if len(words) == 1:
        return words[0]
    return word_class(
        words[0].start, words[-1].end, " ".join(w.text for w in words).strip()
    )


def _group_words(words: list, max_words: int) -> list[list]:
    """Agrupa palavras em blocos legíveis, quebrando em pontuação forte."""
    groups: list[list] = []
    current: list = []
    for word in words:
        current.append(word)
        ends_sentence = word.text.rstrip().endswith((".", "!", "?", "…", ","))
        if len(current) >= max_words or (ends_sentence and len(current) >= max_words // 2):
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return groups


def render_word_cue(
    cue: WordCue, index: int, video_width: int, workdir: Path, font_path: str,
    style: BurnStyle = DEFAULT_STYLE,
) -> Path:
    """Desenha a frase com a palavra ativa em branco pleno e o resto apagado."""
    from PIL import Image, ImageDraw, ImageFont

    font_size = style.font_size(video_width)
    stroke = style.stroke(video_width)
    shadow = style.shadow(video_width)
    spacing = int(font_size * LINE_SPACING_RATIO)
    font = ImageFont.truetype(font_path, font_size)

    lines = _wrap_word_lines(cue.words, font, int(video_width * TEXT_WIDTH_RATIO))
    line_height = font_size + spacing
    padding = max(stroke * 2, shadow * 3)
    height = line_height * len(lines) + padding * 2
    canvas = Image.new("RGBA", (video_width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    space_width = font.getbbox(" ")[2]
    position = 0
    for row, line in enumerate(lines):
        widths = [font.getbbox(w)[2] for w in line]
        total = sum(widths) + space_width * (len(line) - 1)
        x = (video_width - total) // 2
        y = padding + row * line_height

        for column, word in enumerate(line):
            is_active = position == cue.active
            # Sem contorno, a sombra é o que mantém o texto legível sobre fundo claro.
            draw.text((x + shadow, y + shadow), word, font=font, fill=SHADOW_COLOR)
            draw.text(
                (x, y), word, font=font,
                fill=ACTIVE_TEXT_COLOR if is_active else style.dim_text(),
                stroke_width=stroke,
                stroke_fill=STROKE_COLOR if is_active else style.dim_stroke(),
            )
            x += widths[column] + space_width
            position += 1

    target = workdir / f"word_{index:05d}.png"
    canvas.save(target)
    return target


def _wrap_word_lines(words: Sequence[str], font, max_width: int) -> list[list[str]]:
    """Quebra em linhas preservando as palavras como unidades posicionáveis."""
    lines: list[list[str]] = []
    current: list[str] = []
    for word in words:
        probe = " ".join([*current, word])
        if current and font.getbbox(probe)[2] > max_width:
            lines.append(current)
            current = [word]
            continue
        current.append(word)
    if current:
        lines.append(current)
    return lines or [list(words)]


def burn_word_cues(
    video_path: Path,
    cues: Sequence[WordCue],
    output_path: Path,
    workdir: Path,
    *,
    video_width: int,
    video_height: int,
    font_path: str | None = None,
    signature_path: Path | None = None,
    style: BurnStyle = DEFAULT_STYLE,
    fps: float = 0.0,
    encoder_args: Sequence[str] = ("-c:v", "libx264", "-crf", "20", "-preset", "veryfast"),
) -> Path:
    """Queima os quadros de karaokê no vídeo."""
    if not cues:
        raise BurnError("nenhuma palavra com tempo para o karaokê")

    workdir.mkdir(parents=True, exist_ok=True)
    font = resolve_font(font_path)
    images = [
        render_word_cue(cue, index, video_width, workdir, font, style)
        for index, cue in enumerate(cues)
    ]
    signature = (
        prepare_signature(signature_path, video_width, workdir, style)
        if signature_path
        else None
    )

    zoom_filter = build_zoom_filter(style, video_width, video_height, fps) if fps else ""
    template, final_label = build_overlay_filter(
        len(cues), video_height, has_signature=signature is not None, style=style,
        zoom_filter=zoom_filter,
    )
    filter_complex = template.format(
        **{
            key: f"{value:.3f}"
            for index, cue in enumerate(cues)
            for key, value in ((f"start{index}", cue.start), (f"end{index}", cue.end))
        }
    )

    command = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(video_path)]
    for image in images:
        command += ["-i", str(image)]
    if signature:
        command += ["-i", str(signature)]
    command += [
        "-filter_complex", filter_complex,
        "-map", final_label, "-map", "0:a?",
        *encoder_args,
        "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags", "+faststart",
        str(output_path),
    ]
    run(command)
    return output_path
