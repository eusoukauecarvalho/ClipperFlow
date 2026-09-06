"""Regressão: nenhum job pode sobrescrever estado de projeto sem backup antes."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from processing import STATE_FILES, backup_existing_state


class BackupExistingStateTests(unittest.TestCase):
    def test_backs_up_every_state_file_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            for name in STATE_FILES:
                (base / name).write_text(json.dumps({"marker": name}), encoding="utf-8")

            saved = backup_existing_state(str(base))

        self.assertEqual(len(saved), len(STATE_FILES))

    def test_backup_preserves_original_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "metadata.json").write_text('{"clips": [1,2,3]}', encoding="utf-8")

            saved = backup_existing_state(str(base))
            content = json.loads(Path(saved[0]).read_text(encoding="utf-8"))

        self.assertEqual(content["clips"], [1, 2, 3])

    def test_empty_project_produces_no_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            saved = backup_existing_state(tmp)

        self.assertEqual(saved, [])

    def test_original_file_is_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            original = base / "metadata.json"
            original.write_text('{"clips": []}', encoding="utf-8")

            backup_existing_state(str(base))

            self.assertTrue(original.exists())
            self.assertEqual(original.read_text(encoding="utf-8"), '{"clips": []}')


if __name__ == "__main__":
    unittest.main()
