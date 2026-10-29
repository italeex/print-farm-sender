# projectmem - printer-farm-sender

_Last updated: 2026-10-29_

## Project purpose
Десктопное приложение (Python 3.13 + Tkinter, стандартная библиотека + `requests`) для одновременной рассылки одного или нескольких `.gcode` файлов на другие принтеры Creality…

## Recent issues
- [DONE] #legacy_9ce9 Legacy issue: fix: always emit final overall and finished events on batch exit paths -> fix: always emit final overall and finished events on batch exit paths (fixed)
- [DONE] #legacy_7edf Legacy issue: fix: gitignore patterns on separate lines -> fix: gitignore patterns on separate lines (fixed)
- [DONE] #legacy_7b6e Legacy issue: fix: harden config loading, throttle progress, and clean send shutdown -> fix: harden config loading, throttle progress, and clean send shutdown (fixed)

## Decisions
- Refactor: refactor: move plan calendar into a tab [web/app.js]

## Notes
- New feature: feat: count in-progress parts in plan facts [farm_history.py]
- New feature: feat: add QC shift report to history store [farm_history.py]
- New feature: feat: expose QC report endpoints [farm_server.py]
- New feature: feat: add tab navigation shell [web/app.js]
- New feature: feat: show completed and in-progress plan progress [web/app.js]
- New feature: feat: add start-now recommendation block [web/app.js]
- New feature: feat: add QC report dialog [web/app.js]
- New feature: feat: plan progress in positions not pieces [web/app.js]
- New feature: feat: add part catalog with numbers [farm_history.py]
- New feature: feat: add farm file operations [farm_files.py]

## Key files
- `.gitignore`
- `docs/superpowers/plans/2026-10-23-printer-farm-sender.md`
- `docs/superpowers/specs/2026-10-23-printer-farm-sender-design.md`
- `farm_core.py`
- `tests/test_farm_core.py`
- `deviceInfo.json`
- `print_farm_sender.py`
- `"\320\227\320\260\320\277\321\203\321\201\321\202\320\270\321\202\321\214.bat"`
- `СЊ.bat`
- `README.md`
- `docs/superpowers/plans/2026-10-26-farm-dashboard.md`
- `docs/superpowers/specs/2026-10-26-farm-dashboard-design.md`
- `farm_errors.py`
- `tests/test_errors.py`
- `farm_thumbs.py`
- `tests/test_thumbs.py`
- `farm_monitor.py`
- `tests/test_monitor.py`
- `tests/fake_moonraker.py`
- `farm_jobs.py`

## Open questions
- None logged yet.
