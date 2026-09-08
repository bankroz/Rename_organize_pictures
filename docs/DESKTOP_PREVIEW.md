# Desktop Preview Handoff

Date: 2026-09-08

## Delivered Scope

- Core fixes: unique durable execution journals, preview/execute separation,
  conservative identity-checked undo, no asynchronous timeout on mutations,
  bounded read-only daemon workers, TIFF public EXIF access, retained failure rows,
  coarse-format conflict reporting, atomic config writes and reload per job.
- Textual: background rule discovery and undo, busy controls, preserved last execution log.
- PySide6: folder selection/drop replacement, recursive option, JSON format selection
  and remembered default, preview, execution, progress, CSV link, undo and CSV recovery entry.
- Windows onedir spec in packaging/ and scripts/build-desktop.ps1. Old win/ removed.

## Validation

- Existing 67 tests plus 13 safety regressions and 4 desktop tests.
- Native Windows screenshots at 1180x760 and 800x540 under build/ui-checks.
- Packaged smoke entry: photo_renamer_desktop.exe --smoke-test OUTPUT_DIRECTORY.
  Uses generated temporary media; exercises preview, execution and undo and writes
  smoke.json plus a screenshot. It does not process user photos.
- Build with ./scripts/build-desktop.ps1. Root patterns.json now contains all 20 rules
  from the previous portable configuration. Output: dist/photo_renamer_desktop/.
- Windows Qt requires the system ICU exports. PyInstaller can accidentally collect
  a same-named Poppler ICU from PATH; the desktop spec excludes that conflicting DLL.
- Final packaged smoke passed with exit code 0, three completed stages and bundled
  ffprobe. Evidence: build/ui-checks/packaged-smoke/smoke.json and desktop-packaged.png.

## Pending User Review

Confirm main-window layout, contrast, directory drag/drop, result table and progress.
Then migrate rule discovery/confirmation, rule editing/deletion, output-format editing,
and full history browsing. Keep all date/rename logic in the shared core.

## Remaining Release Gates

Real NAS interruption and recovery, large mixed-media fixtures, clean Windows machine,
macOS/Linux runtime validation, dependency/version locking, licenses and signing.
The current task lock is in-process; separate application instances are not coordinated.
File identity uses metadata rather than a full content hash and may change after cloud sync.
Legacy CSVs lack identity fields. Copy operations are not undone as in-place renames.
Network I/O can remain pending; the window stays responsive but cannot safely declare
that an uncompleted OS mutation has been cancelled.
