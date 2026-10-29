# Final shift-scheduler fix report (2026-10-29)

## Findings fixed
1. Parts with remaining > 0 but no reference file were silently dropped in
   `GET /api/schedule` (`farm_server.py:1000`). Now collected as
   `missing_ref`, excluded from the engine, then appended to `unscheduled`
   with reason `нет эталона` and full remaining count. `totals.copies` and
   `totals.unscheduled` include their copies; `totals.positions` still counts
   only positions passed to the engine (documented in code comment +
   README «Без эталона»).
2. README «Смена»: added estimate note «Оценка длительности — медиана
   завершённых печатей этой детали, иначе 4 часа; это ориентир, не гарантия».
   No `;TIME:`/Moonraker chain (documented deviation).
3. README «Смена»: added closed-shift note «После 18:00 смена считается
   закрытой; следующий запуск — завтра в 09:00, расчёт на завтра появится
   при выборе даты». Engine keeps same-date `closed: true` (documented
   deviation).
4. Engine boundary test: start exactly at 18:00 rejected
   (`test_start_exactly_at_18_rejected`: printing printer `finish_at`=18:00,
   `tasks` empty).

## Tests
- Updated `test_get_excludes_part_without_reference` to new contract
  (C listed as `нет эталона`, positions=1, copies=4).
- Added `test_get_missing_reference_listed_as_unscheduled` (API).
- Added `test_start_exactly_at_18_rejected` (engine).
- `python -m unittest tests.test_schedule tests.test_server -k schedule`: 25 tests OK.
- `node --check web/app.js`: OK.

## Files
- `farm_server.py` (`_api_schedule`: missing_ref collection + totals merge)
- `tests/test_server.py`, `tests/test_schedule.py`
- `README.md` («Смена»: estimate note, closed note, «Без эталона» positions note)
