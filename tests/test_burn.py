"""Testes da queima de legendas (parsing de .srt, quebra de linhas, grafo de overlay)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from burn import (  # noqa: E402
    DEFAULT_STYLE,
    BurnError,
    BurnStyle,
    build_zoom_filter,
    _fit_lines,
    _pull_back_orphans,
    build_overlay_filter,
    parse_srt,
    resolve_font,
)

SRT_SAMPLE = """1
00:00:00,000 --> 00:00:02,500
Primeira legenda

2
00:01:05,250 --> 00:01:07,000
Segunda legenda
em duas linhas
"""


class _FakeFont:
    """Mede 10px por caractere — torna as larguras previsíveis no teste."""

    def getbbox(self, text: str):
        return (0, 0, len(text) * 10, 20)


class ParseSrtTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "x.srt"
        self.path.write_text(SRT_SAMPLE, encoding="utf-8")

    def test_reads_every_cue(self):
        self.assertEqual(len(parse_srt(self.path)), 2)

    def test_converts_timecode_to_seconds(self):
        cues = parse_srt(self.path)

        self.assertAlmostEqual(cues[0].end, 2.5)
        self.assertAlmostEqual(cues[1].start, 65.25)

    def test_keeps_multiline_text(self):
        self.assertIn("\n", parse_srt(self.path)[1].text)

    def test_skips_malformed_blocks_instead_of_failing(self):
        self.path.write_text("lixo\nsem timecode\n\n" + SRT_SAMPLE, encoding="utf-8")

        self.assertEqual(len(parse_srt(self.path)), 2)

    def test_raises_on_unreadable_file(self):
        with self.assertRaises(BurnError):
            parse_srt(Path(self.tmp.name) / "nao-existe.srt")


class FitLinesTests(unittest.TestCase):
    def setUp(self):
        self.font = _FakeFont()

    def test_wraps_to_the_available_width(self):
        lines = _fit_lines("aaa bbb ccc ddd", self.font, max_width=80)

        self.assertGreater(len(lines), 1)
        self.assertTrue(all(len(line) * 10 <= 80 for line in lines))

    def test_ignores_source_line_breaks(self):
        lines = _fit_lines("aaa\nbbb", self.font, max_width=500)

        self.assertEqual(lines, ["aaa bbb"])

    def test_avoids_single_word_on_last_line(self):
        # "quatro" sozinho na última linha seria puxado de volta.
        lines = _fit_lines("um dois tres quatro", self.font, max_width=140)

        self.assertNotEqual(len(lines[-1].split()), 1)

    def test_handles_empty_text(self):
        self.assertEqual(_fit_lines("", self.font, max_width=100), [""])


class PullBackOrphansTests(unittest.TestCase):
    def test_moves_word_back_to_balance(self):
        result = _pull_back_orphans([["a", "b", "c"], ["d"]], _FakeFont(), 500)

        self.assertEqual(result[-1], ["c", "d"])

    def test_leaves_single_line_untouched(self):
        self.assertEqual(_pull_back_orphans([["a"]], _FakeFont(), 500), [["a"]])

    def test_does_not_strip_a_short_previous_line(self):
        result = _pull_back_orphans([["a", "b"], ["c"]], _FakeFont(), 500)

        self.assertEqual(result, [["a", "b"], ["c"]])

    def test_respects_the_width_limit(self):
        result = _pull_back_orphans([["aaa", "bbb", "ccc"], ["ddd"]], _FakeFont(), 60)

        self.assertEqual(result[-1], ["ddd"])


class OverlayFilterTests(unittest.TestCase):
    def test_chains_one_overlay_per_cue(self):
        graph, label = build_overlay_filter(3, video_height=1920)

        self.assertEqual(graph.count("overlay="), 3)
        self.assertEqual(label, "[v2]")

    def test_first_overlay_reads_the_source_video(self):
        graph, _ = build_overlay_filter(2, video_height=1920)

        self.assertTrue(graph.startswith("[0:v][1:v]overlay="))

    def test_each_cue_is_time_gated(self):
        graph, _ = build_overlay_filter(2, video_height=1920)

        self.assertEqual(graph.count("enable='between(t,"), 2)

    def test_positions_above_the_bottom_edge(self):
        graph, _ = build_overlay_filter(1, video_height=1000)

        expected = int(1000 * DEFAULT_STYLE.subtitle_bottom_ratio)
        self.assertIn(f"y=H-h-{expected}", graph)

    def test_signature_lifts_the_subtitle(self):
        com, _ = build_overlay_filter(1, 1000, has_signature=True)
        sem, _ = build_overlay_filter(1, 1000, has_signature=False)

        self.assertNotEqual(com, sem)  # a legenda sobe para não encostar na assinatura


class ZoomFilterTests(unittest.TestCase):
    def test_disabled_when_amplitude_is_zero(self):
        self.assertEqual(build_zoom_filter(BurnStyle(zoom_amplitude=0.0), 1080, 1920, 24), "")

    def test_upscales_before_zoompan_to_avoid_jitter(self):
        result = build_zoom_filter(BurnStyle(zoom_amplitude=0.03), 1080, 1920, 24)

        # zoompan trunca a origem em pixel inteiro; o upscale reduz o salto pela metade.
        self.assertTrue(result.startswith("scale=iw*2:ih*2,"))
        self.assertLess(result.index("scale=iw*2"), result.index("zoompan"))

    def test_pins_the_source_fps(self):
        result = build_zoom_filter(BurnStyle(zoom_amplitude=0.03), 1080, 1920, 23.976)

        # sem fps explícito o zoompan reescreve a cadência para 25.
        self.assertIn("fps=23.976", result)

    def test_uses_a_cosine_cycle(self):
        result = build_zoom_filter(BurnStyle(zoom_amplitude=0.03), 1080, 1920, 24)

        self.assertIn("cos(", result)  # começa e termina o ciclo com velocidade zero

    def test_holds_still_between_transitions(self):
        style = BurnStyle(zoom_amplitude=0.08, zoom_transition_s=4.0, zoom_hold_s=10.0)

        result = build_zoom_filter(style, 1080, 1920, 24)

        # O patamar é o que separa este zoom de um vaivém contínuo.
        self.assertIn("),1,", result)
        self.assertIn(f"mod(on/24.000000,{style.zoom_cycle_s:.3f})", result)

    def test_cycle_is_transitions_plus_holds(self):
        style = BurnStyle(zoom_transition_s=4.0, zoom_hold_s=10.0)

        self.assertAlmostEqual(style.zoom_cycle_s, 28.0)

    def test_zoom_comes_before_overlays(self):
        zoom = build_zoom_filter(BurnStyle(zoom_amplitude=0.03), 1080, 1920, 24)
        graph, _ = build_overlay_filter(1, 1920, zoom_filter=zoom)

        # Se viesse depois, a legenda escalaria junto com a imagem.
        self.assertLess(graph.index("zoompan"), graph.index("overlay="))


class ResolveFontTests(unittest.TestCase):
    def test_finds_a_system_font(self):
        self.assertTrue(Path(resolve_font()).exists())

    def test_falls_back_when_the_requested_font_is_missing(self):
        self.assertTrue(Path(resolve_font("/nao/existe.ttf")).exists())


if __name__ == "__main__":
    unittest.main()


class _W:
    """Palavra mínima, com a mesma forma que transcribe.Word."""

    def __init__(self, start, end, text):
        self.start, self.end, self.text = start, end, text


class MergeShortWordsTests(unittest.TestCase):
    """Palavra curta sozinha pisca; ela precisa entrar junto com a seguinte."""

    def setUp(self):
        from burn import _merge_short_words

        self.merge = _merge_short_words

    def test_joins_words_below_the_minimum(self):
        words = [_W(0.0, 0.1, "é"), _W(0.1, 0.2, "que"), _W(0.2, 0.8, "sim")]

        result = self.merge(words)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].text, "é que sim")

    def test_keeps_long_words_separate(self):
        words = [_W(0.0, 0.6, "primeira"), _W(0.6, 1.4, "segunda")]

        result = self.merge(words)

        self.assertEqual([w.text for w in result], ["primeira", "segunda"])

    def test_merged_span_covers_the_whole_group(self):
        words = [_W(1.0, 1.1, "a"), _W(1.1, 1.9, "casa")]

        result = self.merge(words)

        self.assertAlmostEqual(result[0].start, 1.0)
        self.assertAlmostEqual(result[0].end, 1.9)

    def test_trailing_leftover_joins_the_previous_group(self):
        # "fim" sobra sozinho: gruda no grupo anterior em vez de virar um flash.
        words = [_W(0.0, 0.5, "uma"), _W(0.5, 1.0, "frase"), _W(1.0, 1.1, "fim")]

        result = self.merge(words)

        self.assertTrue(result[-1].text.endswith("fim"))
        self.assertGreaterEqual(result[-1].end - result[-1].start, 0.38)

    def test_single_short_word_survives_alone(self):
        result = self.merge([_W(0.0, 0.1, "oi")])

        self.assertEqual(len(result), 1)

    def test_handles_empty_input(self):
        self.assertEqual(self.merge([]), [])


class ZoomHoldTierTests(unittest.TestCase):
    def setUp(self):
        from burn import hold_for_duration

        self.hold = hold_for_duration

    def test_short_clip_gets_the_short_pause(self):
        self.assertEqual(self.hold(20.0), 5.0)

    def test_boundary_of_thirty_seconds_is_inclusive(self):
        self.assertEqual(self.hold(30.0), 5.0)

    def test_medium_clip_gets_the_long_pause(self):
        self.assertEqual(self.hold(45.0), 20.0)

    def test_clip_above_one_minute_keeps_the_long_pause(self):
        self.assertEqual(self.hold(85.0), 20.0)

    def test_cycle_leaves_room_for_movement(self):
        # A pausa nunca pode engolir o clip: precisa sobrar tempo de ida e volta.
        for duration in (13.0, 30.0, 45.0, 90.0):
            style = BurnStyle(zoom_hold_s=self.hold(duration), zoom_transition_s=4.0)
            self.assertLess(style.zoom_transition_s * 2, style.zoom_cycle_s)
