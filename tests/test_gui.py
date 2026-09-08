import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl
from PySide6.QtGui import QDropEvent
from PySide6.QtWidgets import QApplication
from photo_renamer_gui import RenamerWindow
import photo_renamer as core


class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyle('Fusion')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.history = patch.object(core, 'append_history_report', return_value=self.root / 'history.csv')
        self.history.start()
        self.window = RenamerWindow()
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.wait_job()
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.history.stop()
        self.temp.cleanup()

    def wait_job(self):
        deadline = time.monotonic() + 15
        while self.window.worker is not None and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.01)
        self.assertIsNone(self.window.worker, 'worker did not finish')

    def test_preview_execute_undo_and_preserved_last_log(self):
        names = ['IMG20240101120000.jpg', 'IMG20240102130000.jpg']
        for name in names:
            (self.root / name).write_bytes(b'photo')
        self.window.source.setText(str(self.root))
        self.window.start_job('preview')
        self.assertFalse(self.window.execute.isEnabled())
        self.wait_job()
        self.assertTrue(all((self.root / name).exists() for name in names))
        self.assertEqual(self.window.model.rowCount(), 2)
        self.assertFalse(self.window.undo.isEnabled())
        self.window.start_job('execute')
        self.wait_job()
        execution = self.window.last_execution_csv
        self.assertTrue(execution)
        self.assertFalse(any((self.root / name).exists() for name in names))
        self.window.start_job('preview')
        self.wait_job()
        self.assertEqual(self.window.last_execution_csv, execution)
        self.window.start_job('undo')
        self.wait_job()
        self.assertTrue(all((self.root / name).read_bytes() == b'photo' for name in names))
        self.assertEqual(self.window.progress.value(), 100)

    def test_drop_replaces_full_path_including_spaces_and_unicode(self):
        path = self.root / '中文 相册'
        path.mkdir()
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(path))])
        event = QDropEvent(QPointF(50, 50), Qt.DropAction.CopyAction, mime,
                           Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        self.window.source.setText('C:/old/path')
        self.window.dropEvent(event)
        self.assertEqual(Path(self.window.source.text()), path)

    def test_busy_close_is_rejected_and_progress_not_complete(self):
        import threading
        release = threading.Event()
        def job(options):
            release.wait(3)
            raise ValueError('test stopped')
        with patch('photo_renamer_gui.run_rename_job', side_effect=job):
            self.window.source.setText(str(self.root))
            self.window.start_job('execute')
            try:
                self.window.close()
                self.assertTrue(self.window.isVisible())
                self.assertFalse(self.window.execute.isEnabled())
                self.window.on_progress({'stage': 'analyze', 'current': 10, 'total': 10})
                self.assertEqual(self.window.progress.value(), 60)
            finally:
                release.set()
                self.wait_job()

    def test_window_sizes_and_screenshots(self):
        (self.root / 'IMG20240101120000.jpg').touch()
        self.window.source.setText(str(self.root))
        self.window.start_job('preview')
        self.wait_job()
        output = Path(__file__).resolve().parents[1] / 'build' / 'ui-checks'
        output.mkdir(parents=True, exist_ok=True)
        for width, height in [(1180, 760), (800, 540)]:
            self.window.resize(width, height)
            self.app.processEvents()
            self.assertGreater(self.window.table.height(), 100)
            self.assertLessEqual(self.window.execute.geometry().right(), self.window.width())
            self.assertTrue(self.window.grab().save(str(output / f'desktop-{width}.png')))
