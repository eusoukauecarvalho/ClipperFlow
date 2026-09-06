"""Testes de títulos de publicação e renomeação."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from titling import (  # noqa: E402
    TitlingError,
    apply_rename,
    load_titles,
    publish_name,
    safe_filename,
    validate_titles,
)


class SafeFilenameTests(unittest.TestCase):
    def test_keeps_accents(self):
        self.assertEqual(safe_filename("Pablo Marçal é referência"), "Pablo Marçal é referência")

    def test_strips_filesystem_breakers(self):
        self.assertEqual(safe_filename('a: b? c/d "e"'), "a b c d e")

    def test_drops_trailing_period(self):
        self.assertEqual(safe_filename("Olha onde estou."), "Olha onde estou")

    def test_collapses_whitespace(self):
        self.assertEqual(safe_filename("dois   espaços"), "dois espaços")


class PublishNameTests(unittest.TestCase):
    def test_prefixes_the_clip_number(self):
        self.assertEqual(publish_name("clip_024", "Título"), "024 - Título.mp4")

    def test_prefix_preserves_sort_order(self):
        names = [publish_name(f"clip_{i:03d}", "x") for i in (2, 10, 55)]

        self.assertEqual(names, sorted(names))


class ValidateTitlesTests(unittest.TestCase):
    def _ok(self):
        return {"titulo": "Um título bom", "descricao": "desc", "tags": ["a"]}

    def test_accepts_complete_titles(self):
        self.assertEqual(validate_titles({"clip_001": self._ok()}), ())

    def test_flags_missing_title(self):
        issues = validate_titles({"clip_001": {"descricao": "d", "tags": ["a"]}})

        self.assertTrue(any("sem título" in i for i in issues))

    def test_flags_title_over_the_limit(self):
        info = self._ok()
        info["titulo"] = "x" * 71

        issues = validate_titles({"clip_001": info})

        self.assertTrue(any("71 chars" in i for i in issues))

    def test_flags_all_caps(self):
        info = self._ok()
        info["titulo"] = "TÍTULO GRITADO"

        issues = validate_titles({"clip_001": info})

        self.assertTrue(any("CAPS" in i for i in issues))

    def test_flags_missing_description_and_tags(self):
        issues = validate_titles({"clip_001": {"titulo": "ok"}})

        self.assertEqual(len(issues), 2)


class ApplyRenameTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.clips_dir = Path(self.tmp.name) / "legendados"
        self.clips_dir.mkdir()
        (self.clips_dir / "clip_001.mp4").write_bytes(b"v")
        (self.clips_dir / "clip_002.mp4").write_bytes(b"v")
        self.titles = {
            "clip_001": {"titulo": "Primeiro título"},
            "clip_002": {"titulo": "Segundo: com dois-pontos?"},
        }

    def test_renames_using_the_titles(self):
        mapping = apply_rename(self.clips_dir, self.titles)

        self.assertTrue((self.clips_dir / "001 - Primeiro título.mp4").exists())
        self.assertEqual(mapping["clip_002"], "002 - Segundo com dois-pontos.mp4")

    def test_writes_the_rename_map(self):
        apply_rename(self.clips_dir, self.titles)

        map_path = self.clips_dir.parent / "renomeacao.json"
        saved = json.loads(map_path.read_text(encoding="utf-8"))
        self.assertEqual(len(saved), 2)

    def test_dry_run_touches_nothing(self):
        mapping = apply_rename(self.clips_dir, self.titles, dry_run=True)

        self.assertEqual(len(mapping), 2)
        self.assertTrue((self.clips_dir / "clip_001.mp4").exists())
        self.assertFalse((self.clips_dir.parent / "renomeacao.json").exists())

    def test_clip_without_title_is_kept(self):
        (self.clips_dir / "clip_003.mp4").write_bytes(b"v")

        mapping = apply_rename(self.clips_dir, self.titles)

        self.assertNotIn("clip_003", mapping)
        self.assertTrue((self.clips_dir / "clip_003.mp4").exists())

    def test_existing_target_is_not_overwritten(self):
        (self.clips_dir / "001 - Primeiro título.mp4").write_bytes(b"antigo")

        apply_rename(self.clips_dir, self.titles)

        self.assertTrue((self.clips_dir / "clip_001.mp4").exists())  # fonte preservada

    def test_rerun_merges_into_the_existing_map(self):
        apply_rename(self.clips_dir, {"clip_001": self.titles["clip_001"]})
        apply_rename(self.clips_dir, {"clip_002": self.titles["clip_002"]})

        saved = json.loads((self.clips_dir.parent / "renomeacao.json").read_text())
        self.assertEqual(len(saved), 2)


class LoadTitlesTests(unittest.TestCase):
    def test_rejects_missing_clips_object(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.json"
            path.write_text('{"clips": {}}', encoding="utf-8")

            with self.assertRaises(TitlingError):
                load_titles(path)

    def test_rejects_unreadable_file(self):
        with self.assertRaises(TitlingError):
            load_titles(Path("/nao/existe.json"))


if __name__ == "__main__":
    unittest.main()
