import sys
import unittest
from pathlib import Path

import eh_batch_gui


class GuiCommandTests(unittest.TestCase):
    def test_source_child_command_uses_gui_entrypoint(self) -> None:
        command = eh_batch_gui.build_child_command()

        self.assertEqual(command[0], sys.executable)
        self.assertIn(eh_batch_gui.CHILD_FLAG, command)

    def test_safe_command_does_not_quote_simple_paths(self) -> None:
        rendered = eh_batch_gui.safe_command_for_log(["python", "-u", r"F:\WORK\codex\eh_batch_gui.py"])

        self.assertEqual(rendered, r"python -u F:\WORK\codex\eh_batch_gui.py")

    def test_common_story_category_values_exist(self) -> None:
        values = {value for _label, value in eh_batch_gui.CATEGORY_CHOICES}

        self.assertTrue({"doujinshi", "manga", "non-h"}.issubset(values))

    def test_settings_path_is_not_in_project_directory(self) -> None:
        self.assertNotIn(r"F:\WORK\codex", str(eh_batch_gui.SETTINGS_PATH))

    def test_frozen_internal_output_dir_is_migrated(self) -> None:
        app_dir = Path(r"F:\WORK\codex\dist\EHBatchDownloader")
        old_output = str(app_dir / "_internal" / "eh_downloads")

        self.assertEqual(
            eh_batch_gui.normalize_output_dir(old_output, app_dir=app_dir, frozen=True),
            str(app_dir / "eh_downloads"),
        )


if __name__ == "__main__":
    unittest.main()
