import csv
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import json

from photo_renamer import (
    PhotoRenamer,
    RenameJobOptions,
    append_history_report,
    discover_rule_suggestions,
    load_format_profiles,
    load_history_reports,
    save_format_profile,
    _load_patterns_from_json,
    resolve_format,
    run_rename_job,
    run_with_timeout,
    undo_from_csv,
)


class SafetyTests(unittest.TestCase):
    def test_run_with_timeout_returns_without_waiting_for_slow_function(self):
        start = time.time()

        result = run_with_timeout(time.sleep, 1, timeout=0.05, default='timeout')

        self.assertEqual(result, 'timeout')
        self.assertLess(time.time() - start, 0.5)

    def test_resolve_format_rejects_path_separators_in_output_name(self):
        fmt, valid = resolve_format('%Y/%m/%d_%H%M')

        self.assertFalse(valid)
        self.assertEqual(fmt, '%Y/%m/%d_%H%M')

    def test_copy_mode_does_not_overwrite_existing_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src_dir = root / 'src'
            out_dir = root / 'out'
            src_dir.mkdir()
            out_dir.mkdir()
            source = src_dir / 'IMG20240101120000.jpg'
            target = out_dir / '2024.01.01_1200.jpg'
            source.write_bytes(b'new-content')
            target.write_bytes(b'keep-me')

            renamer = PhotoRenamer(str(src_dir), output_dir=str(out_dir))
            success = renamer.execute()

            self.assertEqual(success, 0)
            self.assertEqual(target.read_bytes(), b'keep-me')
            self.assertEqual(renamer.results[0]['status'], 'conflict')

    def test_write_csv_escapes_excel_formula_cells(self):
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / 'report.csv'
            renamer = PhotoRenamer(tmp)
            renamer.results = [{
                'original': '=cmd|/C calc!A0.jpg',
                'new_name': '+2024.01.01_1200.jpg',
                'date': '2024-01-01 12:00:00',
                'source': 'Filename(test)',
                'status': 'ok',
            }]

            renamer.write_csv(str(csv_path))

            with open(csv_path, newline='', encoding='utf-8-sig') as f:
                row = next(csv.DictReader(f))
            self.assertEqual(row['original'], "'=cmd|/C calc!A0.jpg")
            self.assertEqual(row['new_name'], "'+2024.01.01_1200.jpg")

    def test_pattern_config_rejects_dangerous_nested_quantifier_regex(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / 'patterns.json'
            config_path.write_text(json.dumps({
                'patterns': [{
                    'id': 99,
                    'regex': r'((\d+)+)(\d{2})(\d{2})',
                    'group_count': 3,
                    'description': 'dangerous',
                    'is_own_output': False,
                }]
            }), encoding='utf-8')

            with self.assertRaises(ValueError):
                _load_patterns_from_json(config_path)

    def test_write_csv_includes_destination_path_for_undo(self):
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / 'rename_log.csv'
            renamer = PhotoRenamer(tmp)
            renamer.results = [{
                'original': str(Path(tmp) / 'IMG20240101120000.jpg'),
                'new_name': '2024.01.01_1200.jpg',
                'date': '2024-01-01 12:00:00',
                'source': 'Filename(test)',
                'status': 'ok',
                'dst': str(Path(tmp) / '2024.01.01_1200.jpg'),
            }]

            renamer.write_csv(str(csv_path))

            with open(csv_path, newline='', encoding='utf-8-sig') as f:
                row = next(csv.DictReader(f))
            self.assertEqual(row['dst'], str(Path(tmp) / '2024.01.01_1200.jpg'))

    def test_undo_from_csv_restores_renamed_files_in_reverse_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = root / 'IMG20240101120000.jpg'
            renamed = root / '2024.01.01_1200.jpg'
            renamed.write_bytes(b'photo')
            csv_path = root / 'rename_log.csv'
            with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
                writer = csv.DictWriter(f, fieldnames=['original', 'new_name', 'date', 'source', 'status', 'dst'])
                writer.writeheader()
                writer.writerow({
                    'original': str(original),
                    'new_name': renamed.name,
                    'date': '2024-01-01 12:00:00',
                    'source': 'Filename(test)',
                    'status': 'ok',
                    'dst': str(renamed),
                })

            summary = undo_from_csv(str(csv_path))

            self.assertEqual(summary['restored'], 1)
            self.assertTrue(original.exists())
            self.assertFalse(renamed.exists())

    def test_undo_from_csv_skips_when_original_path_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = root / 'IMG20240101120000.jpg'
            renamed = root / '2024.01.01_1200.jpg'
            original.write_bytes(b'current')
            renamed.write_bytes(b'renamed')
            csv_path = root / 'rename_log.csv'
            with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
                writer = csv.DictWriter(f, fieldnames=['original', 'new_name', 'date', 'source', 'status', 'dst'])
                writer.writeheader()
                writer.writerow({
                    'original': str(original),
                    'new_name': renamed.name,
                    'date': '2024-01-01 12:00:00',
                    'source': 'Filename(test)',
                    'status': 'ok',
                    'dst': str(renamed),
                })

            summary = undo_from_csv(str(csv_path))

            self.assertEqual(summary['restored'], 0)
            self.assertEqual(summary['skipped'], 1)
            self.assertEqual(original.read_bytes(), b'current')
            self.assertEqual(renamed.read_bytes(), b'renamed')

    def test_run_rename_job_preview_returns_summary_without_modifying_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'IMG20240101120000.jpg'
            source.write_bytes(b'photo')

            summary = run_rename_job(RenameJobOptions(source_dir=str(root), mode='preview'))

            self.assertEqual(summary['mode'], 'preview')
            self.assertEqual(summary['files_count'], 1)
            self.assertEqual(summary['ok_count'], 1)
            self.assertTrue(source.exists())
            self.assertTrue(Path(summary['csv_path']).exists())

    def test_run_rename_job_execute_writes_log_with_undo_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'IMG20240101120000.jpg'
            source.write_bytes(b'photo')

            summary = run_rename_job(RenameJobOptions(source_dir=str(root), mode='execute'))

            self.assertEqual(summary['mode'], 'execute')
            self.assertEqual(summary['ok_count'], 1)
            self.assertFalse(source.exists())
            self.assertTrue((root / '2024.01.01_1200.jpg').exists())
            self.assertTrue(Path(summary['csv_path']).exists())

            undo_summary = undo_from_csv(summary['csv_path'])
            self.assertEqual(undo_summary['restored'], 1)
            self.assertTrue(source.exists())

    def test_discover_rule_suggestions_returns_candidate_patterns(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'Camera 2024_01_02 custom.jpg').write_bytes(b'photo')

            suggestions = discover_rule_suggestions(str(root))

            self.assertTrue(suggestions)
            self.assertIn('signature', suggestions[0])
            self.assertIn('suggestion', suggestions[0])

    def test_save_and_load_format_profiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / 'patterns.json'
            config_path.write_text(json.dumps({'patterns': [], 'output_formats': []}), encoding='utf-8')

            save_format_profile('年月日', '%Y年%m月%d日_%H%M', str(config_path), make_current=True)
            profiles = load_format_profiles(str(config_path))

            custom = [p for p in profiles if p['name'] == '年月日'][0]
            self.assertEqual(custom['format'], '%Y年%m月%d日_%H%M')
            self.assertTrue(custom['current'])

    def test_history_reports_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            history_path = Path(tmp) / 'history.csv'
            summary = {
                'mode': 'execute',
                'source_dir': str(Path(tmp) / 'photos'),
                'files_count': 3,
                'ok_count': 2,
                'error_count': 1,
                'csv_path': str(Path(tmp) / 'photos' / 'rename_log.csv'),
            }

            append_history_report(summary, str(history_path))
            rows = load_history_reports(str(history_path))

            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['folder'], summary['source_dir'])
            self.assertEqual(rows[0]['csv_path'], summary['csv_path'])

    def test_help_exposes_tui_entrypoint(self):
        result = subprocess.run(
            [sys.executable, 'photo_renamer.py', '--help'],
            cwd=Path(__file__).resolve().parent.parent,
            capture_output=True,
            text=True,
            encoding='utf-8',
        )

        self.assertEqual(result.returncode, 0)
        self.assertIn('--tui', result.stdout)

    def test_tui_optional_dependency_is_declared(self):
        requirements = Path(__file__).resolve().parent.parent / 'requirements-tui.txt'

        self.assertIn('textual', requirements.read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
