import tempfile
import unittest
from pathlib import Path

from textual.widgets import DataTable, Input, Select, Static

from photo_renamer import append_history_report
from photo_renamer_tui import PhotoRenamerApp, choose_directory, open_folder


class TuiTests(unittest.IsolatedAsyncioTestCase):
    async def test_tui_mounts_with_recursive_enabled_by_default(self):
        app = PhotoRenamerApp()

        async with app.run_test():
            recursive = app.query_one("#recursive_toggle")
            self.assertTrue(app._get_recursive())
            self.assertIn("已包含子目录", str(recursive.label))
            self.assertIsNotNone(app.query_one("#results", DataTable))
            self.assertEqual(app.focused.id, "source_input")

    async def test_recursive_toggle_switches_state(self):
        app = PhotoRenamerApp()

        async with app.run_test() as pilot:
            app._on_recursive_toggle()
            await pilot.pause()

            toggle = app.query_one("#recursive_toggle")
            self.assertFalse(app._get_recursive())
            self.assertIn("不包含子目录", str(toggle.label))

    async def test_high_frequency_buttons_exist_near_top(self):
        app = PhotoRenamerApp()

        async with app.run_test():
            self.assertIsNotNone(app.query_one("#browse_button"))
            self.assertIsNotNone(app.query_one("#preview_button"))
            self.assertIsNotNone(app.query_one("#execute_button"))
            self.assertIsNotNone(app.query_one("#undo_button"))
            self.assertEqual(len(app.query("#open_current_csv_button")), 0)

    async def test_preview_button_populates_summary_and_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "IMG20240101120000.jpg").write_bytes(b"photo")
            app = PhotoRenamerApp()

            async with app.run_test() as pilot:
                app.query_one("#source_input", Input).value = str(root)
                app.action_preview()
                await pilot.pause()

                renderable = app.query_one("#summary", Static).renderable
                summary = renderable.plain if hasattr(renderable, "plain") else str(renderable)
                table = app.query_one("#results", DataTable)
                self.assertIn("预览", summary)
                self.assertEqual(table.row_count, 1)
                self.assertIn("CSV 已生成", summary)

    async def test_source_input_accepts_directory_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = PhotoRenamerApp()

            async with app.run_test() as pilot:
                source_input = app.query_one("#source_input", Input)
                source_input.value = str(root)
                await pilot.pause(0.4)

                summary = str(app.query_one("#summary", Static).renderable)
                self.assertIn(str(root), summary)

    async def test_browse_button_uses_selected_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = PhotoRenamerApp()
            original = choose_directory

            try:
                import photo_renamer_tui

                photo_renamer_tui.choose_directory = lambda initial_dir="": str(root)
                async with app.run_test() as pilot:
                    app._on_browse()
                    await pilot.pause()

                    source_input = app.query_one("#source_input", Input)
                    self.assertEqual(source_input.value, str(root))
            finally:
                photo_renamer_tui.choose_directory = original

    def test_pick_external_target_prefers_directory_and_file_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nested = root / "nested"
            nested.mkdir()
            file_path = nested / "IMG20240101120000.jpg"
            file_path.write_bytes(b"photo")
            app = PhotoRenamerApp()

            self.assertEqual(app._pick_external_target([str(nested)]), nested)
            self.assertEqual(app._pick_external_target([str(file_path)]), nested)
            self.assertIsNone(app._pick_external_target(["Z:/definitely-missing-path"]))

    async def test_preview_table_has_date_and_source_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "IMG20240101120000.jpg").write_bytes(b"photo")
            app = PhotoRenamerApp()

            async with app.run_test() as pilot:
                app.query_one("#source_input", Input).value = str(root)
                app.action_preview()
                await pilot.pause()

                table = app.query_one("#results", DataTable)
                labels = [str(column.label) for column in table.columns.values()]
                self.assertIn("日期", labels)
                self.assertIn("规则来源", labels)
                self.assertIn("错误原因", labels)

    async def test_format_button_loads_profiles(self):
        app = PhotoRenamerApp()

        async with app.run_test() as pilot:
            app._on_formats()
            await pilot.pause()

            table = app.query_one("#results", DataTable)
            self.assertGreaterEqual(table.row_count, 4)

    async def test_format_table_uses_startup_default_column(self):
        app = PhotoRenamerApp()

        async with app.run_test() as pilot:
            app._on_formats()
            await pilot.pause()

            table = app.query_one("#results", DataTable)
            labels = [str(column.label) for column in table.columns.values()]
            self.assertIn("启动默认", labels)
            self.assertNotIn("当前", labels)

    async def test_set_current_format_saves(self):
        config_path = Path(__file__).resolve().parent.parent / "patterns.json"
        original_config = config_path.read_bytes()
        app = PhotoRenamerApp()

        try:
            async with app.run_test() as pilot:
                app.query_one("#fmt_name_input", Input).value = "自定义格式"
                app.query_one("#fmt_expr_input", Input).value = "%Y%m%d_%H%M%S"
                app._on_set_current()
                await pilot.pause()

                fmt_value = app.query_one("#format_select", Select).value
                self.assertEqual(str(fmt_value), "%Y%m%d_%H%M%S")
        finally:
            config_path.write_bytes(original_config)

    async def test_rules_button_loads_suggestions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Camera 2024_01_02 custom.jpg").write_bytes(b"photo")
            app = PhotoRenamerApp()

            async with app.run_test() as pilot:
                app.query_one("#source_input", Input).value = str(root)
                app._on_rules()
                await pilot.pause()

                table = app.query_one("#results", DataTable)
                self.assertGreaterEqual(table.row_count, 1)

    async def test_rule_action_button_is_available(self):
        app = PhotoRenamerApp()

        async with app.run_test():
            self.assertTrue(len(app.query("#add_rule_button")) > 0)

    async def test_rule_table_uses_clear_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Camera 2024_01_02 custom.jpg").write_bytes(b"photo")
            app = PhotoRenamerApp()

            async with app.run_test() as pilot:
                app.query_one("#source_input", Input).value = str(root)
                app._on_rules()
                await pilot.pause()

                table = app.query_one("#results", DataTable)
                labels = [str(column.label) for column in table.columns.values()]
                self.assertEqual(labels, ["匹配数", "示例文件名", "建议正则", "规则签名"])

    async def test_history_button_loads_reports(self):
        append_history_report({
            "mode": "execute",
            "source_dir": "D:\\photos",
            "files_count": 1,
            "ok_count": 1,
            "error_count": 0,
            "csv_path": "D:\\photos\\rename_log.csv",
        })
        app = PhotoRenamerApp()

        async with app.run_test() as pilot:
            app._on_history()
            await pilot.pause()

            table = app.query_one("#results", DataTable)
            self.assertGreaterEqual(table.row_count, 1)

    async def test_history_table_has_full_columns(self):
        append_history_report({
            "mode": "execute",
            "source_dir": "D:\\photos",
            "files_count": 5,
            "ok_count": 4,
            "error_count": 1,
            "csv_path": "D:\\photos\\rename_log.csv",
        })
        app = PhotoRenamerApp()

        async with app.run_test() as pilot:
            app._on_history()
            await pilot.pause()

            table = app.query_one("#results", DataTable)
            labels = [str(column.label) for column in table.columns.values()]
            self.assertIn("时间", labels)
            self.assertIn("成功", labels)
            self.assertIn("CSV 路径", labels)

    async def test_history_undo_button_exists(self):
        app = PhotoRenamerApp()

        async with app.run_test():
            self.assertTrue(len(app.query("#history_undo_button")) > 0)


class OpenFolderTests(unittest.TestCase):
    def test_open_folder_returns_error_for_nonexistent_path(self):
        error = open_folder("/nonexistent/path/does/not/exist")
        self.assertTrue(error)


if __name__ == "__main__":
    unittest.main()
