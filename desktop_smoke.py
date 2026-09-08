"""Opt-in packaged smoke check, using only disposable generated files."""
import json
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QTimer
from photo_renamer import DateExtractor


def run_smoke(app, window, output_dir):
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    sandbox = tempfile.TemporaryDirectory()
    root = Path(sandbox.name)
    source = root / 'IMG20240101120000.jpg'
    source.write_bytes(b'smoke fixture')
    history = patch('photo_renamer.append_history_report', return_value=root / 'history.csv')
    history.start()
    window.source.setText(str(root))
    stage = [0]
    deadline = time.monotonic() + 45
    timer = QTimer(window)

    def finish(error=''):
        timer.stop()
        window.grab().save(str(output / 'desktop-packaged.png'))
        (output / 'smoke.json').write_text(json.dumps({
            'ok': not error, 'error': error,
            'ffprobe': DateExtractor._find_ffprobe(),
            'stages_completed': stage[0],
        }, ensure_ascii=False, indent=2), encoding='utf-8')
        history.stop()
        sandbox.cleanup()
        app.exit(1 if error else 0)

    def tick():
        if window.worker is not None:
            if time.monotonic() > deadline:
                # The caller applies a process deadline; do not destroy a live QThread.
                (output / 'timeout.txt').write_text(window.status.text(), encoding='utf-8')
            return
        try:
            if stage[0] == 0:
                window.start_job('preview')
            elif stage[0] == 1:
                assert window.model.rowCount() == 1 and source.exists(), window.status.text()
                window.start_job('execute')
            elif stage[0] == 2:
                assert not source.exists() and window.last_execution_csv, window.status.text()
                window.start_job('undo')
            else:
                assert source.read_bytes() == b'smoke fixture', window.status.text()
                assert DateExtractor._find_ffprobe(), 'bundled ffprobe missing'
                finish()
                return
            stage[0] += 1
        except Exception as exc:
            finish(str(exc))

    timer.timeout.connect(tick)
    timer.start(50)
    return app.exec()
