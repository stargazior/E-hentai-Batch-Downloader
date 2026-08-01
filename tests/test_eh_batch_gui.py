import ast
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

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

    def test_task_file_defaults_to_application_directory(self) -> None:
        app_dir = Path(r"F:\apps\EHBatchDownloader")

        self.assertEqual(eh_batch_gui.default_config_file(app_dir), str(app_dir / "eh_batch_configs.json"))

    def test_search_and_tag_conditions_are_combined_for_gui_commands(self) -> None:
        gui = object.__new__(eh_batch_gui.BatchDownloaderGui)
        command: list[str] = []

        gui._append_server_conditions(
            command,
            [
                {"type": "Search", "value": "language:chinese$"},
                {"type": "Tag", "value": 'parody:"touhou project$"'},
            ],
        )

        self.assertEqual(command, ["--search", 'language:chinese$ parody:"touhou project$"'])

    def test_tag_condition_is_forwarded_as_a_raw_search_expression(self) -> None:
        gui = object.__new__(eh_batch_gui.BatchDownloaderGui)
        command: list[str] = []

        gui._append_server_conditions(command, [{"type": "Tag", "value": "-furry ~yaoi"}])

        self.assertEqual(command, ["--search", "-furry ~yaoi"])

    def test_paned_window_add_uses_tk_supported_options(self) -> None:
        tree = ast.parse(Path(eh_batch_gui.__file__).read_text(encoding="utf-8"))
        unsupported_keywords = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Attribute) or node.func.attr != "add":
                continue
            unsupported_keywords.extend(keyword.arg for keyword in node.keywords if keyword.arg == "minsize")

        self.assertEqual(unsupported_keywords, [])


class GuiTaskSelectionTests(unittest.TestCase):
    def test_selection_callback_is_ignored_during_task_load(self) -> None:
        gui = object.__new__(eh_batch_gui.BatchDownloaderGui)
        gui._loading_task = True
        gui._suppress_task_selection = False
        gui._programmatic_task_selection = None
        gui.task_tree = Mock()
        gui._load_task_name = Mock()

        gui._on_task_selected()

        gui._load_task_name.assert_not_called()

    def test_programmatic_tree_selection_does_not_reload_task(self) -> None:
        gui = object.__new__(eh_batch_gui.BatchDownloaderGui)
        gui._loading_task = False
        gui._suppress_task_selection = True
        gui._programmatic_task_selection = "task"
        gui.task_tree = Mock()
        gui.task_tree.selection.return_value = ("task",)
        gui._load_task_name = Mock()

        gui._on_task_selected()

        self.assertIsNone(gui._programmatic_task_selection)
        gui._load_task_name.assert_not_called()

    def test_different_user_selection_clears_stale_programmatic_marker(self) -> None:
        gui = object.__new__(eh_batch_gui.BatchDownloaderGui)
        gui._loading_task = False
        gui._suppress_task_selection = False
        gui._programmatic_task_selection = "old-task"
        gui.config_name = Mock()
        gui.config_name.get.return_value = "old-task"
        gui.task_tree = Mock()
        gui.task_tree.selection.return_value = ("new-task",)
        gui._load_task_name = Mock()

        gui._on_task_selected()

        self.assertIsNone(gui._programmatic_task_selection)
        gui._load_task_name.assert_called_once_with("new-task")

    def test_current_task_selection_is_idempotent(self) -> None:
        gui = object.__new__(eh_batch_gui.BatchDownloaderGui)
        gui._loading_task = False
        gui._suppress_task_selection = False
        gui._programmatic_task_selection = None
        gui.config_name = Mock()
        gui.config_name.get.return_value = "current-task"
        gui.task_tree = Mock()
        gui.task_tree.selection.return_value = ("current-task",)
        gui._load_task_name = Mock()

        gui._on_task_selected()

        gui._load_task_name.assert_not_called()


if __name__ == "__main__":
    unittest.main()
