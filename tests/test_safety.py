import csv
import os
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import json

from photo_renamer import (
    DateExtractor,
    PhotoRenamer,
    RenameJobOptions,
    add_pattern_suggestion,
    append_history_report,
    delete_format_profile,
    delete_pattern_rule,
    discover_rule_report,
    discover_rule_suggestions,
    load_format_profiles,
    load_pattern_rules,
    load_history_reports,
    save_format_profile,
    save_pattern_rule,
    _load_patterns_from_json,
    resolve_format,
    run_rename_job,
    run_with_timeout,
    set_pattern_config_path,
    get_video_metadata_timeout,
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

    def test_discover_rule_suggestions_keeps_higher_precision_than_date_only_rule(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '2022-05-01-2201.jpg').write_bytes(b'photo')

            config = root / 'patterns.json'
            config.write_text(json.dumps({'patterns': [{
                'id': 14, 'regex': r'(\d{4})-(\d{2})-(\d{2})',
                'group_count': 3, 'description': 'date only', 'is_own_output': False,
            }]}), encoding='utf-8')
            with patch('photo_renamer._find_config_path', return_value=config):
                suggestions = discover_rule_suggestions(str(root))
            DateExtractor.reload_patterns()

            self.assertTrue(suggestions)
            self.assertEqual(suggestions[0]['signature'], 'YYYY-MM-DD-HHMM')
            self.assertEqual(suggestions[0]['count'], 1)
            self.assertNotIn('group_order', suggestions[0]['suggestion'])

    def test_add_pattern_suggestion_stores_signature_and_prioritizes_specific_rule(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / 'patterns.json'
            config_path.write_text(json.dumps({
                'patterns': [
                    {
                        'id': 14,
                        'regex': r'(\d{4})-(\d{2})-(\d{2})',
                        'group_count': 3,
                        'description': 'YYYY-MM-DD（纯中划线日期）',
                        'is_own_output': False,
                    }
                ]
            }, ensure_ascii=False), encoding='utf-8')

            result = add_pattern_suggestion('YYYY-MM-DD-HHMM', str(config_path))
            config = json.loads(config_path.read_text(encoding='utf-8'))

            self.assertEqual(result['description'], 'YYYY-MM-DD-HHMM（用户确认添加）')
            self.assertEqual(config['patterns'][0]['description'], 'YYYY-MM-DD-HHMM（用户确认添加）')
            self.assertEqual(config['patterns'][1]['description'], 'YYYY-MM-DD（纯中划线日期）')
            self.assertEqual(DateExtractor.extract(Path('2022-05-01-2201.jpg'))[0], datetime(2022, 5, 1, 22, 1))
            set_pattern_config_path('')
            DateExtractor.reload_patterns()

    def test_discover_rule_report_shows_existing_rule_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / 'patterns.json'
            config_path.write_text(json.dumps({
                'patterns': [
                    {
                        'id': 20,
                        'regex': r'(\d{4})-(\d{2})-(\d{2})-(\d{2})(\d{2})(?!\d)',
                        'group_count': 5,
                        'description': 'YYYY-MM-DD-HHMM（用户确认添加）',
                        'is_own_output': False,
                    }
                ]
            }, ensure_ascii=False), encoding='utf-8')
            (root / '2022-05-01-2201.jpg').write_bytes(b'photo')
            (root / '2022-05-01-2202.jpg').write_bytes(b'photo')

            set_pattern_config_path(str(config_path))
            DateExtractor.reload_patterns(str(config_path))
            try:
                report = discover_rule_report(str(root))
            finally:
                set_pattern_config_path('')
                DateExtractor.reload_patterns()

            self.assertEqual(report['suggestions'], [])
            self.assertEqual(report['covered'][0]['id'], 20)
            self.assertEqual(report['covered'][0]['count'], 2)
            self.assertIn('YYYY-MM-DD-HHMM', report['covered'][0]['name'])

    def test_add_pattern_suggestion_deduplicates_existing_equivalent_rules(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / 'patterns.json'
            duplicate = {
                'regex': r'(\d{4})-(\d{2})-(\d{2})-(\d{2})(\d{2})(?!\d)',
                'group_count': 5,
                'description': 'old',
                'is_own_output': False,
                'id': 20,
            }
            config_path.write_text(json.dumps({
                'patterns': [dict(duplicate), dict(duplicate, id=21)],
            }, ensure_ascii=False), encoding='utf-8')

            add_pattern_suggestion('YYYY-MM-DD-HHMM', str(config_path))
            config = json.loads(config_path.read_text(encoding='utf-8'))

            matches = [p for p in config['patterns'] if p['regex'] == duplicate['regex']]
            self.assertEqual(len(matches), 1)
            self.assertEqual(matches[0]['id'], 20)
            self.assertEqual(matches[0]['description'], 'YYYY-MM-DD-HHMM（用户确认添加）')
            set_pattern_config_path('')
            DateExtractor.reload_patterns()

    def test_load_save_delete_pattern_rules_roundtrip_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / 'patterns.json'
            config_path.write_text(json.dumps({
                'patterns': [
                    {
                        'id': 31,
                        'regex': r'(\d{4})-(\d{2})-(\d{2})',
                        'group_count': 3,
                        'description': 'old rule',
                        'is_own_output': False,
                    }
                ]
            }, ensure_ascii=False), encoding='utf-8')

            rules = load_pattern_rules(str(config_path))
            self.assertEqual(rules[0]['name'], 'old rule')

            save_pattern_rule('new rule', r'(\d{4})\.(\d{2})\.(\d{2})', str(config_path), rule_id=31)
            updated = load_pattern_rules(str(config_path))
            self.assertEqual(updated[0]['name'], 'new rule')
            self.assertEqual(updated[0]['regex'], r'(\d{4})\.(\d{2})\.(\d{2})')

            removed = delete_pattern_rule(str(config_path), rule_id=31)
            self.assertEqual(removed['description'], 'new rule')
            self.assertEqual(load_pattern_rules(str(config_path)), [])
            set_pattern_config_path('')
            DateExtractor.reload_patterns()

    def test_discover_rule_suggestions_handles_month_names_and_12_hour_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'Alice_Jan-02-2026_7-30PM.jpg').write_bytes(b'photo')
            (root / 'User_26-02-03 07-30-05.jpg').write_bytes(b'photo')
            (root / 'Camera_2026-02-03_0730PM.jpg').write_bytes(b'photo')

            suggestions = discover_rule_suggestions(str(root))
            signatures = {item['signature'] for item in suggestions}

            self.assertIn('MON-DD-YYYY', signatures)
            self.assertIn('YY-MM-DD HH:MM:SS', signatures)
            self.assertIn('YYYY-MM-DD HHMM AMPM', signatures)

    def test_added_12_hour_rule_converts_pm_to_24_hour_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / 'patterns.json'
            config_path.write_text(json.dumps({'patterns': []}, ensure_ascii=False), encoding='utf-8')

            add_pattern_suggestion('YYYY-MM-DD HHMM AMPM', str(config_path))
            dt, source = DateExtractor.extract(Path('Camera_2026-02-03_0730PM.jpg'))

            self.assertEqual(dt, datetime(2026, 2, 3, 19, 30))
            self.assertIn('YYYY-MM-DD HHMM AMPM', source)
            set_pattern_config_path('')
            DateExtractor.reload_patterns()

    def test_discover_rule_suggestions_skips_supported_unix_timestamp_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '1728267073523_100_edit_798591120684390.jpg').write_bytes(b'photo')

            suggestions = discover_rule_suggestions(str(root))

            self.assertEqual(suggestions, [])

    def test_prefixed_unix_timestamp_name_uses_explicit_rule(self):
        dt, source = DateExtractor.extract(Path('1728267073523_100_edit_798591120684390.jpg'))
        self.assertIsNotNone(dt)
        self.assertEqual(source, 'UnixTimestamp(ms,编辑导出)')

    def test_unix_timestamp_filename_wins_over_manual_date_filename(self):
        dt, source = DateExtractor.extract(Path('20240615_1728267073523.jpg'))

        self.assertEqual(dt, datetime.fromtimestamp(1728267073523 / 1000.0))
        self.assertEqual(source, 'UnixTimestamp(ms)')

    def test_file_property_time_is_last_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'no_date_marker.bin'
            path.write_bytes(b'data')

            dt, source = DateExtractor.extract(path)

        self.assertIsNotNone(dt)
        self.assertIn(source, {'FileCreateTime', 'FileModifyTime'})

    def test_video_metadata_prefers_quicktime_creationdate(self):
        probe = {
            'format': {
                'tags': {
                    'creation_time': '2017-03-04T10:50:17.000000Z',
                    'com.apple.quicktime.creationdate': '2017-03-04T18:50:16+0800',
                },
            },
            'streams': [
                {'tags': {'creation_time': '2017-03-04T10:50:17.000000Z'}},
            ],
        }

        dt, source = DateExtractor._from_video_metadata_probe(probe)

        self.assertEqual(dt, datetime(2017, 3, 4, 18, 50, 16))
        self.assertEqual(source, 'VideoMetadata(com.apple.quicktime.creationdate)')

    def test_mov_extract_uses_video_metadata_before_file_time(self):
        probe = {
            'format': {
                'tags': {
                    'com.apple.quicktime.creationdate': '2017-03-04T18:50:16+0800',
                },
            },
            'streams': [],
        }

        with patch.object(DateExtractor, '_run_ffprobe_json', return_value=(probe, '')):
            dt, source = DateExtractor.extract(Path('IMG_3383.MOV'))

        self.assertEqual(dt, datetime(2017, 3, 4, 18, 50, 16))
        self.assertEqual(source, 'VideoMetadata(com.apple.quicktime.creationdate)')

    def test_video_metadata_wins_over_filename_date(self):
        probe = {
            'format': {
                'tags': {
                    'creation_time': '2016-10-18T10:07:47.000000Z',
                },
            },
            'streams': [],
        }

        with patch.object(DateExtractor, '_run_ffprobe_json', return_value=(probe, '')):
            dt, source = DateExtractor.extract(Path('20161116.MP4'))

        self.assertEqual(dt, datetime(2016, 10, 18, 18, 7, 47))
        self.assertEqual(source, 'VideoMetadata(creation_time)')

    def test_video_falls_back_to_filename_when_metadata_missing(self):
        with patch.object(DateExtractor, '_run_ffprobe_json', return_value=({}, '')):
            dt, source = DateExtractor.extract(Path('20161116.MP4'))

        self.assertEqual(dt, datetime(2016, 11, 16, 0, 0, 0))
        self.assertTrue(source.startswith('Filename('))

    def test_video_metadata_accepts_creationdate_and_date_fields(self):
        probe = {
            'format': {
                'tags': {
                    'creationdate': '2017-03-04T18:50:16+0800',
                },
            },
        }

        dt, source = DateExtractor._from_video_metadata_probe(probe)

        self.assertEqual(dt, datetime(2017, 3, 4, 18, 50, 16))
        self.assertEqual(source, 'VideoMetadata(creationdate)')

        dt, source = DateExtractor._from_video_metadata_probe({
            'format': {'tags': {'date': '2017-03-04'}},
        })
        self.assertEqual(dt, datetime(2017, 3, 4, 0, 0, 0))
        self.assertEqual(source, 'VideoMetadata(date)')

    def test_video_metadata_stops_after_format_level_match(self):
        calls = []

        def fake_probe(_path, entries, sections):
            calls.append((entries, tuple(sections)))
            return ({
                'format': {
                    'tags': {
                        'creation_time': '2017-03-04T10:50:17.000000Z',
                    },
                },
            }, '')

        with patch.object(DateExtractor, '_run_ffprobe_json', side_effect=fake_probe):
            dt, source = DateExtractor._from_video_metadata(Path('clip.mp4'))

        self.assertIsNotNone(dt)
        self.assertEqual(source, 'VideoMetadata(creation_time)')
        self.assertEqual(len(calls), 1)
        self.assertIn('-show_format', calls[0][1])

    def test_video_timeout_uses_patterns_json_above_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / 'patterns.json'
            config_path.write_text(json.dumps({
                'video_metadata_timeout_seconds': 4,
            }), encoding='utf-8')

            original_config = os.environ.get('PHOTO_RENAMER_VIDEO_TIMEOUT')
            os.environ['PHOTO_RENAMER_VIDEO_TIMEOUT'] = '9'
            set_pattern_config_path(str(config_path))
            try:
                self.assertEqual(get_video_metadata_timeout(), 4)
            finally:
                if original_config is None:
                    os.environ.pop('PHOTO_RENAMER_VIDEO_TIMEOUT', None)
                else:
                    os.environ['PHOTO_RENAMER_VIDEO_TIMEOUT'] = original_config
                set_pattern_config_path('')

    def test_video_timeout_reflects_patterns_json_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / 'patterns.json'
            config_path.write_text(json.dumps({
                'video_metadata_timeout_seconds': 2,
            }), encoding='utf-8')
            set_pattern_config_path(str(config_path))
            try:
                self.assertEqual(get_video_metadata_timeout(), 2)

                config_path.write_text(json.dumps({
                    'video_metadata_timeout_seconds': 5,
                }), encoding='utf-8')

                self.assertEqual(get_video_metadata_timeout(), 5)
            finally:
                set_pattern_config_path('')

    def test_save_and_load_format_profiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / 'patterns.json'
            config_path.write_text(json.dumps({'patterns': [], 'output_formats': []}), encoding='utf-8')

            save_format_profile('年月日', '%Y年%m月%d日_%H%M', str(config_path), make_current=True)
            profiles = load_format_profiles(str(config_path))

            custom = [p for p in profiles if p['name'] == '年月日'][0]
            self.assertEqual(custom['format'], '%Y年%m月%d日_%H%M')
            self.assertTrue(custom['current'])

    def test_rename_and_delete_custom_format_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / 'patterns.json'
            config_path.write_text(json.dumps({
                'patterns': [],
                'output_formats': [{'name': '旧格式', 'format': '%Y%m%d_%H%M'}],
            }, ensure_ascii=False), encoding='utf-8')

            save_format_profile('新格式', '%Y-%m-%d_%H%M', str(config_path), original_name='旧格式')
            profiles = load_format_profiles(str(config_path))
            names = [p['name'] for p in profiles]
            self.assertIn('新格式', names)
            self.assertNotIn('旧格式', names)

            removed = delete_format_profile('新格式', str(config_path))
            self.assertEqual(removed['name'], '新格式')
            names = [p['name'] for p in load_format_profiles(str(config_path))]
            self.assertNotIn('新格式', names)

    def test_only_one_format_profile_is_current_when_formats_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / 'patterns.json'
            config_path.write_text(json.dumps({
                'patterns': [],
                'default_output_format': '%Y%m%d_%H%M%S',
                'current_output_format_name': '自定义格式',
                'output_formats': [
                    {'name': '自定义格式', 'format': '%Y%m%d_%H%M%S'},
                ],
            }, ensure_ascii=False), encoding='utf-8')

            profiles = load_format_profiles(str(config_path))
            current = [p for p in profiles if p['current']]

            self.assertEqual(len(current), 1)
            self.assertEqual(current[0]['name'], '自定义格式')

    def test_setting_builtin_current_does_not_create_custom_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / 'patterns.json'
            config_path.write_text(json.dumps({'patterns': [], 'output_formats': []}), encoding='utf-8')

            save_format_profile('默认', '%Y.%m.%d_%H%M', str(config_path), make_current=True)
            config = json.loads(config_path.read_text(encoding='utf-8'))
            profiles = load_format_profiles(str(config_path))
            current = [p for p in profiles if p['current']]

            self.assertEqual(config['current_output_format_name'], '默认')
            self.assertEqual(config['default_output_format'], '%Y.%m.%d_%H%M')
            self.assertEqual(config['output_formats'], [])
            self.assertEqual(len(current), 1)
            self.assertEqual(current[0]['name'], '默认')

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
