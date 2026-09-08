import csv
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from PIL import Image
import photo_renamer as core


class RegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.history = patch.object(core, 'append_history_report', return_value=self.root / 'history.csv')
        self.history.start()
        core.set_pattern_config_path('')
        core.DateExtractor.reload_patterns()

    def tearDown(self):
        self.history.stop()
        core.set_pattern_config_path('')
        core.DateExtractor.reload_patterns()
        self.temp.cleanup()

    def job(self, mode):
        return core.run_rename_job(core.RenameJobOptions(str(self.root), mode=mode))

    def test_repeat_execution_preserves_original_undo_log(self):
        source = self.root / 'IMG20240101120000.jpg'
        source.write_bytes(b'photo')
        first = self.job('execute')
        log = Path(first['csv_path']).read_bytes()
        second = self.job('execute')
        self.assertNotEqual(first['csv_path'], second['csv_path'])
        self.assertEqual(Path(first['csv_path']).read_bytes(), log)
        self.assertEqual(second['results'][0]['status'], 'unchanged')
        self.assertEqual(core.undo_from_csv(first['csv_path'])['restored'], 1)
        self.assertEqual(source.read_bytes(), b'photo')

    def test_preview_log_cannot_move_an_unrelated_file(self):
        source = self.root / 'IMG20240101120000.jpg'
        source.touch()
        result = self.job('preview')
        source.unlink()
        target = Path(result['results'][0]['dst'])
        target.write_bytes(b'unrelated')
        with self.assertRaises(ValueError):
            core.undo_from_csv(result['csv_path'])
        self.assertEqual(target.read_bytes(), b'unrelated')

    def test_undo_rejects_modified_file(self):
        (self.root / 'IMG20240101120000.jpg').write_bytes(b'photo')
        result = self.job('execute')
        target = Path(result['results'][0]['dst'])
        target.write_bytes(b'changed-content')
        self.assertEqual(core.undo_from_csv(result['csv_path'])['errors'], 1)
        self.assertTrue(target.exists())

    def test_pending_journal_recovers_rename_after_interruption(self):
        source = self.root / 'before.jpg'
        source.write_bytes(b'photo')
        target = self.root / 'after.jpg'
        identity = core._file_identity(source)
        log = self.root / 'recovery.csv'
        with log.open('w', newline='', encoding='utf-8-sig') as handle:
            writer = csv.DictWriter(handle, fieldnames=core.LOG_FIELDS)
            writer.writeheader()
            writer.writerow({'original': str(source), 'dst': str(target),
                             'operation': 'rename', 'status': 'pending', 'identity': identity})
        source.rename(target)
        self.assertEqual(core.undo_from_csv(str(log))['restored'], 1)

    def test_journal_is_created_before_any_mutation(self):
        source = self.root / 'IMG20240101120000.jpg'
        source.touch()
        with patch.object(core, '_journal_row', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.job('execute')
        self.assertTrue(source.exists())

    def test_tiff_internal_date(self):
        source = self.root / '20260101.tiff'
        exif = Image.Exif()
        exif[306] = '2017:03:04 18:50:00'
        Image.new('RGB', (2, 2)).save(source, exif=exif)
        dt, origin = core.DateExtractor.extract(source)
        self.assertEqual(dt, datetime(2017, 3, 4, 18, 50))
        self.assertTrue(origin.startswith('EXIF'))

    def test_coarse_output_format_reports_conflict(self):
        for name in ('a.jpg', 'b.jpg'):
            (self.root / name).touch()
        with patch.object(core.DateExtractor, 'extract', return_value=(datetime(2026, 1, 1), 'test')):
            result = core.run_rename_job(core.RenameJobOptions(str(self.root), fmt_arg='%Y'))
        self.assertEqual(result['error_count'], 1)
        self.assertEqual(result['ok_count'], 1)

    def test_failed_reads_remain_in_report(self):
        (self.root / 'a.jpg').touch()
        with patch.object(core.DateExtractor, 'extract', return_value=(None, 'Unknown(timeout)')):
            result = self.job('preview')
        self.assertEqual(result['error_count'], 1)
        self.assertEqual(len(result['results']), 1)

    def test_atomic_config_write_preserves_original_on_failure(self):
        config = self.root / 'patterns.json'
        config.write_text('{"old": true}', encoding='utf-8')
        with patch.object(core.os, 'replace', side_effect=OSError('disk error')):
            with self.assertRaises(OSError):
                core._write_config_document({'new': True}, str(config))
        self.assertEqual(json.loads(config.read_text()), {'old': True})

    def test_next_preview_reloads_external_rule_changes(self):
        config = self.root / 'patterns.json'
        core.generate_default_config(config)
        core.set_pattern_config_path(str(config))
        source = self.root / 'custom_2024_01_02.jpg'
        source.touch()
        first = self.job('preview')
        document = json.loads(config.read_text(encoding='utf-8'))
        document['patterns'].insert(0, {'id': 99, 'regex': r'custom_(\d{4})_(\d{2})_(\d{2})',
                                        'group_count': 3, 'description': 'new external rule'})
        config.write_text(json.dumps(document), encoding='utf-8')
        second = self.job('preview')
        self.assertNotEqual(first['results'][0]['source'], second['results'][0]['source'])
        self.assertIn('new external rule', second['results'][0]['source'])

    def test_no_replace_never_overwrites_existing_file(self):
        source, target = self.root / 'a.jpg', self.root / 'b.jpg'
        source.write_bytes(b'a')
        target.write_bytes(b'b')
        with self.assertRaises(OSError):
            core._rename_no_replace(source, target)
        self.assertEqual(target.read_bytes(), b'b')

    def test_history_failure_does_not_disguise_completed_rename(self):
        (self.root / 'IMG20240101120000.jpg').touch()
        with patch.object(core, 'append_history_report', side_effect=OSError('read only')):
            result = self.job('execute')
        self.assertEqual(result['ok_count'], 1)
        self.assertTrue(result['warning'])
        self.assertTrue(Path(result['csv_path']).exists())

    def test_windowed_executable_without_stdout(self):
        (self.root / 'IMG20240101120000.jpg').touch()
        with patch.object(core.sys, 'stdout', None):
            result = self.job('execute')
        self.assertEqual(result['ok_count'], 1)
