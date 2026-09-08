import tempfile
import time
import threading
import unittest
import json
from pathlib import Path
from unittest.mock import patch

from textual.widgets import DataTable, Input, ProgressBar, Select, Static

from photo_renamer import DateExtractor, append_history_report, set_pattern_config_path
from photo_renamer_tui import PhotoRenamerApp, _build_format_options, choose_directory, open_folder


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

    def test_format_options_render_current_label_without_mojibake(self):
        options, current = _build_format_options()

        self.assertTrue(current)
        labels = [label for label, _value in options]
        self.assertTrue(any("默认" in label for label in labels))
        self.assertFalse(any("榛" in label or "鏍" in label for label in labels))

    async def test_preview_button_populates_summary_and_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "IMG20240101120000.jpg").write_bytes(b"photo")
            app = PhotoRenamerApp()

            async with app.run_test() as pilot:
                app.query_one("#source_input", Input).value = str(root)
                app.action_preview()
                for _ in range(20):
                    await pilot.pause(0.1)
                    if app.query_one("#results", DataTable).row_count:
                        break

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
                await pilot.pause(app.SOURCE_PATH_SETTLE_SECONDS + 0.2)

                summary = str(app.query_one("#summary", Static).renderable)
                self.assertIn(str(root), summary)

    async def test_source_input_replaces_existing_path_when_drop_text_is_appended(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_dir = root / "old"
            new_dir = root / "new"
            old_dir.mkdir()
            new_dir.mkdir()
            app = PhotoRenamerApp()

            async with app.run_test() as pilot:
                source_input = app.query_one("#source_input", Input)
                source_input.value = str(old_dir)
                await pilot.pause(app.SOURCE_PATH_SETTLE_SECONDS + 0.2)

                source_input.value = f"{old_dir}{new_dir}"
                await pilot.pause(app.SOURCE_PATH_SETTLE_SECONDS + 0.2)

                self.assertEqual(source_input.value, str(new_dir))

    async def test_source_input_waits_for_full_dropped_path_before_replacing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_dir = root / "old"
            parent_dir = root / "new"
            final_dir = parent_dir / "deep"
            old_dir.mkdir()
            final_dir.mkdir(parents=True)
            app = PhotoRenamerApp()

            async with app.run_test() as pilot:
                source_input = app.query_one("#source_input", Input)
                source_input.value = str(old_dir)
                await pilot.pause(app.SOURCE_PATH_SETTLE_SECONDS + 0.2)

                source_input.value = f"{old_dir}{parent_dir}"
                await pilot.pause(0.2)
                self.assertNotEqual(source_input.value, str(parent_dir))

                source_input.value = f"{old_dir}{final_dir}"
                await pilot.pause(app.SOURCE_PATH_SETTLE_SECONDS + 0.2)

                self.assertEqual(source_input.value, str(final_dir))

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

    async def test_preview_shows_live_progress_while_running(self):
        release = threading.Event()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "IMG20240101120000.jpg").write_bytes(b"photo")
            app = PhotoRenamerApp()
            import photo_renamer_tui

            original = photo_renamer_tui.run_rename_job

            def fake_run_rename_job(options):
                callback = options.progress_callback
                if callback:
                    callback({
                        "stage": "preview",
                        "current": 1,
                        "total": 3,
                        "percent": 33,
                        "info": "step1",
                        "done": False,
                    })
                    release.wait(5)
                return {
                    "mode": "preview",
                    "source_dir": str(root),
                    "files_count": 3,
                    "ok_count": 3,
                    "error_count": 0,
                    "csv_path": str(root / "preview_report.csv"),
                    "history_path": "",
                    "results": [],
                }

            try:
                photo_renamer_tui.run_rename_job = fake_run_rename_job
                async with app.run_test() as pilot:
                    app.query_one("#source_input", Input).value = str(root)
                    app.action_preview()
                    await pilot.pause(0.05)

                    progress = app.query_one("#progress_bar", ProgressBar)
                    status = str(app.query_one("#status", Static).renderable)
                    self.assertGreater(progress.progress, 0)
                    self.assertLess(progress.progress, 100)
                    self.assertIn("1/3", status)
                    release.set()
            finally:
                release.set()
                photo_renamer_tui.run_rename_job = original

    async def test_execute_shows_live_progress_while_running(self):
        release = threading.Event()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "IMG20240101120000.jpg").write_bytes(b"photo")
            app = PhotoRenamerApp()
            import photo_renamer_tui

            original = photo_renamer_tui.run_rename_job

            def fake_run_rename_job(options):
                callback = options.progress_callback
                if callback:
                    callback({
                        "stage": "analyze",
                        "current": 1,
                        "total": 3,
                        "percent": 33,
                        "info": "scan1",
                        "done": False,
                    })
                    callback({
                        "stage": "execute",
                        "current": 1,
                        "total": 3,
                        "percent": 33,
                        "info": "rename1",
                        "done": False,
                    })
                    release.wait(5)
                return {
                    "mode": "execute",
                    "source_dir": str(root),
                    "files_count": 3,
                    "ok_count": 3,
                    "error_count": 0,
                    "csv_path": str(root / "rename_history.csv"),
                    "history_path": str(root / "history.csv"),
                    "results": [],
                }

            try:
                photo_renamer_tui.run_rename_job = fake_run_rename_job
                async with app.run_test() as pilot:
                    app.query_one("#source_input", Input).value = str(root)
                    app.action_execute()
                    await pilot.pause(0.05)

                    progress = app.query_one("#progress_bar", ProgressBar)
                    status = str(app.query_one("#status", Static).renderable)
                    self.assertGreater(progress.progress, 0)
                    self.assertLess(progress.progress, 100)
                    self.assertIn("执行重命名", status)
                    self.assertIn("1/3", status)
                    release.set()
            finally:
                release.set()
                photo_renamer_tui.run_rename_job = original

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

    async def test_rule_table_shows_real_filename_not_match_fragment(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            filename = "Camera 2024_01_02 custom.jpg"
            (root / filename).write_bytes(b"photo")
            app = PhotoRenamerApp()

            async with app.run_test() as pilot:
                app.query_one("#source_input", Input).value = str(root)
                app._on_rules()
                await pilot.pause()

                table = app.query_one("#results", DataTable)
                row_key = next(iter(table.rows))
                cells = [str(cell) for cell in table.get_row(row_key)]
                self.assertIn(filename, cells[1])

    async def test_rule_scan_detects_hyphen_date_with_compact_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            filename = "2022-05-01-2201.jpg"
            (root / filename).write_bytes(b"photo")
            config = root / 'patterns.json'
            config.write_text(json.dumps({'patterns': [{
                'id': 14, 'regex': r'(\d{4})-(\d{2})-(\d{2})',
                'group_count': 3, 'description': 'date only', 'is_own_output': False,
            }]}), encoding='utf-8')
            with patch('photo_renamer._find_config_path', return_value=config):
                app = PhotoRenamerApp()
                async with app.run_test() as pilot:
                    app.query_one("#source_input", Input).value = str(root)
                    app._on_rules()
                    await pilot.pause()
                    table = app.query_one("#results", DataTable)
                    self.assertEqual(table.row_count, 1)
                    row_key = next(iter(table.rows))
                    cells = [str(cell) for cell in table.get_row(row_key)]
                    self.assertIn(filename, cells[1])
                    self.assertIn("YYYY-MM-DD-HHMM", cells[3])
            DateExtractor.reload_patterns()

    async def test_rule_scan_shows_existing_rule_coverage_when_not_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "patterns.json"
            config_path.write_text(json.dumps({
                "patterns": [
                    {
                        "id": 20,
                        "regex": r"(\d{4})-(\d{2})-(\d{2})-(\d{2})(\d{2})(?!\d)",
                        "group_count": 5,
                        "description": "YYYY-MM-DD-HHMM（用户确认添加）",
                        "is_own_output": False,
                    }
                ]
            }, ensure_ascii=False), encoding="utf-8")
            photo_dir = root / "photos"
            photo_dir.mkdir()
            (photo_dir / "2022-05-01-2201.jpg").write_bytes(b"photo")
            app = PhotoRenamerApp()

            set_pattern_config_path(str(config_path))
            DateExtractor.reload_patterns(str(config_path))
            try:
                async with app.run_test() as pilot:
                    app.query_one("#source_input", Input).value = str(photo_dir)
                    app._on_rules()
                    await pilot.pause()

                    table = app.query_one("#results", DataTable)
                    self.assertEqual(table.row_count, 1)
                    row_key = next(iter(table.rows))
                    cells = [str(cell) for cell in table.get_row(row_key)]
                    self.assertEqual(cells[0], "已覆盖")
                    self.assertEqual(cells[3], "20")
                    self.assertIn("YYYY-MM-DD-HHMM", cells[4])
            finally:
                set_pattern_config_path("")
                DateExtractor.reload_patterns()

    async def test_add_rule_button_writes_selected_signature_to_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "patterns.json"
            config_path.write_text(json.dumps({
                "patterns": [
                    {
                        "id": 14,
                        "regex": r"(\d{4})-(\d{2})-(\d{2})",
                        "group_count": 3,
                        "description": "YYYY-MM-DD（纯中划线日期）",
                        "is_own_output": False,
                    }
                ]
            }, ensure_ascii=False), encoding="utf-8")
            photo_dir = root / "photos"
            photo_dir.mkdir()
            (photo_dir / "2022-05-01-2201.jpg").write_bytes(b"photo")
            app = PhotoRenamerApp()

            set_pattern_config_path(str(config_path))
            DateExtractor.reload_patterns(str(config_path))
            try:
                async with app.run_test() as pilot:
                    app.query_one("#source_input", Input).value = str(photo_dir)
                    app._on_rules()
                    await pilot.pause()

                    app._on_add_rule()
                    await pilot.pause()

                config = json.loads(config_path.read_text(encoding="utf-8"))
                self.assertEqual(config["patterns"][0]["description"], "YYYY-MM-DD-HHMM（用户确认添加）")
            finally:
                set_pattern_config_path("")
                DateExtractor.reload_patterns()

    async def test_pattern_rule_list_allows_edit_and_delete(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "patterns.json"
            config_path.write_text(json.dumps({
                "patterns": [
                    {
                        "id": 30,
                        "regex": r"(\d{4})-(\d{2})-(\d{2})",
                        "group_count": 3,
                        "description": "旧识别规则",
                        "is_own_output": False,
                    }
                ]
            }, ensure_ascii=False), encoding="utf-8")
            app = PhotoRenamerApp()

            set_pattern_config_path(str(config_path))
            DateExtractor.reload_patterns(str(config_path))
            try:
                async with app.run_test() as pilot:
                    app._on_patterns()
                    await pilot.pause()

                    table = app.query_one("#results", DataTable)
                    self.assertEqual(table.row_count, 1)
                    row_key = next(iter(table.rows))
                    cells = [str(cell) for cell in table.get_row(row_key)]
                    self.assertIn("旧识别规则", cells[1])

                    app.query_one("#fmt_name_input", Input).value = "新识别规则"
                    app.query_one("#fmt_expr_input", Input).value = r"(\d{4})\.(\d{2})\.(\d{2})"
                    app._on_save_format()
                    await pilot.pause()

                    config = json.loads(config_path.read_text(encoding="utf-8"))
                    self.assertEqual(config["patterns"][0]["description"], "新识别规则")
                    self.assertEqual(config["patterns"][0]["regex"], r"(\d{4})\.(\d{2})\.(\d{2})")

                    app._on_delete_format()
                    await pilot.pause()

                    config = json.loads(config_path.read_text(encoding="utf-8"))
                    self.assertEqual(config["patterns"], [])
            finally:
                set_pattern_config_path("")
                DateExtractor.reload_patterns()

    async def test_pattern_rule_list_row_count_matches_json_patterns(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "patterns.json"
            config_path.write_text(json.dumps({
                "patterns": [
                    {
                        "id": 10 + idx,
                        "regex": r"(\d{4})-(\d{2})-(\d{2})",
                        "group_count": 3,
                        "description": f"规则{idx}",
                        "is_own_output": False,
                    }
                    for idx in range(6)
                ]
            }, ensure_ascii=False), encoding="utf-8")
            app = PhotoRenamerApp()

            set_pattern_config_path(str(config_path))
            DateExtractor.reload_patterns(str(config_path))
            try:
                async with app.run_test() as pilot:
                    app._on_patterns()
                    await pilot.pause()

                    table = app.query_one("#results", DataTable)
                    summary = str(app.query_one("#summary", Static).renderable)
                    self.assertEqual(table.row_count, 6)
                    self.assertIn("JSON 条目数：6", summary)
                    self.assertIn("表格行数：6", summary)
            finally:
                set_pattern_config_path("")
                DateExtractor.reload_patterns()

    async def test_rule_scan_skips_supported_unix_timestamp_filename(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "1728267073523_100_edit_798591120684390.jpg").write_bytes(b"photo")
            app = PhotoRenamerApp()

            async with app.run_test() as pilot:
                app.query_one("#source_input", Input).value = str(root)
                app._on_rules()
                await pilot.pause()

                table = app.query_one("#results", DataTable)
                self.assertEqual(table.row_count, 0)

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
