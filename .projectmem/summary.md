# projectmem - printer-farm-sender

_Last updated: 2026-10-26_

## Project purpose
Десктопное приложение (Python 3.13 + Tkinter, стандартная библиотека + `requests`) для одновременной рассылки одного или нескольких `.gcode` файлов на другие принтеры Creality…

## Recent issues
- [DONE] #legacy_9ce9 Legacy issue: fix: always emit final overall and finished events on batch exit paths -> fix: always emit final overall and finished events on batch exit paths (fixed)
- [DONE] #legacy_7edf Legacy issue: fix: gitignore patterns on separate lines -> fix: gitignore patterns on separate lines (fixed)
- [DONE] #legacy_7b6e Legacy issue: fix: harden config loading, throttle progress, and clean send shutdown -> fix: harden config loading, throttle progress, and clean send shutdown (fixed)

## Decisions
- No decisions logged yet.

## Notes
- docs: design spec and implementation plan
- feat: config, printer model, seeding from deviceInfo.json, file validation
- feat: printer probes and Moonraker upload with progress and cancel
- feat: parallel multi-file batch sender with event callbacks
- feat: tkinter UI for parallel printer farm uploads
- feat: double-click launcher Р—Р°РїСѓСЃС‚РёС‚СЊ.bat without console window
- docs: README with usage and v1 limitations
- docs: dashboard design and implementation plan
- docs: clarify monitor and test interfaces in plan
- New feature: feat: mainsail settings and print control api [farm_core.py]

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

## Open questions
- None logged yet.
