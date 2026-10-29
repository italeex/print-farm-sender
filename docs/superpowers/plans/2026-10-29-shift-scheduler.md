# Этап 3: планировщик смены 09:00–18:00

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Считать очередь запусков на смену по свободным принтерам и остатку плана; показывать, что не влезло и почему.

**Architecture:** Чистая функция `build_schedule()` без БД и сети, отдельный модуль `farm_schedule.py`. Сервер собирает входные данные из плана, каталога и монитора. UI рисует очередь по принтерам; утверждение сохраняет задачи в `schedule_tasks`.

**Tech Stack:** Python stdlib, SQLite, нативный JS/CSS.

**Spec:** `docs/superpowers/specs/2026-10-29-operator-modules-design.md`, раздел 5.

## Global Constraints

- Python stdlib + `requests`; новых зависимостей нет.
- Запуск печати не добавляется: планировщик только рекомендует и раскладывает файлы.
- Окно запусков 09:00–18:00; печать может идти ночью; старт позже 18:00 не предлагается.
- Пауза, ошибка, offline — принтер недоступен до конца смены.
- Одна задача — одна копия.
- Русские подписи; без `innerHTML`; без комментариев.
- Тесты без реальных принтеров.

## Файловая структура

- `farm_schedule.py` — новый чистый движок.
- `farm_history.py` — `schedule_tasks`, сохранение и чтение.
- `farm_server.py` — `/api/schedule`, `/api/schedule/apply`.
- `web/index.html`, `web/app.js`, `web/styles.css` — вкладка «Смена».
- `tests/test_schedule.py`, `tests/test_history.py`, `tests/test_server.py`.

---

### Task 1: Движок расписания

**Files:**
- Create: `farm_schedule.py`
- Test: `tests/test_schedule.py`

**Interfaces:**
- Produces: `SHIFT_START = "09:00"`, `SHIFT_END = "18:00"`.
- Produces: `build_schedule(now: float, date: str, items: list[dict], printers: list[dict], ready: dict[str, set[str]], estimates: dict[str, float]) -> dict`.
  - `items`: `{part_key, number, name, remaining}` уже отсортированные по приоритету.
  - `printers`: `{key, name, state, finish_at}`.
  - `ready[printer_key]` — множество `part_key`, для которых файл на принтере актуален.
  - `estimates[part_key]` — секунды.
  - Результат: `{date, window: {start, end}, tasks: [{printer_key, printer, part_key, number, name, start, finish, file_ready}], unscheduled: [{part_key, number, name, remaining, reason}], totals: {positions, copies, scheduled, unscheduled}}`.
- Produces: `estimate_seconds(history_durations: list[float]) -> tuple[float, bool]` — медиана или 4 часа, второй элемент `True` для оценки по умолчанию.

- [ ] **Step 1: Тесты**

```python
def test_assigns_to_earliest_free_printer():
    out = build_schedule(NOW, "2026-10-29", [{"part_key": "a", "number": 1, "name": "A", "remaining": 2}],
                         [{"key": "p1", "name": "P1", "state": "idle", "finish_at": None}],
                         {"p1": {"a"}}, {"a": 3600})
    assert [t["start"] for t in out["tasks"]] == [at("09:00"), at("10:00")]
    assert out["totals"]["scheduled"] == 2
```

Ещё тесты: ночная печать (`finish` после 18:00 разрешён, следующий старт на этом принтере — завтра), занятый принтер (`finish_at` 14:00 → старт 14:00), пауза/ошибка/offline недоступны, файл не готов (`file_ready=False`), не влезло — причина `нет свободных принтеров до 18:00`, пустой план.

- [ ] **Step 2: RED** — `python -m unittest tests.test_schedule -v`.
- [ ] **Step 3: Реализация**

Границы смены — локальные `datetime` от `date`. Свободное время принтера: `idle` → `max(now, shift_start)`; `printing` → `max(now, finish_at)`, если `finish_at` есть, иначе недоступен; `paused/error/offline` → недоступен. Старт разрешён только `< shift_end`; финиш может быть больше. Жадность: для каждой копии — принтер с минимальным свободным временем, где файл готов или его можно загрузить; при равенстве — по имени. После назначения свободное время = финиш. Не назначенное агрегируется по позиции с причиной.

- [ ] **Step 4: GREEN** и коммит `feat: add shift scheduling engine`.

### Task 2: Хранение и API смены

**Files:**
- Modify: `farm_history.py`, `farm_server.py`
- Test: `tests/test_history.py`, `tests/test_server.py`

**Interfaces:**
- Produces: `HistoryStore.save_schedule(date, tasks) -> dict` — заменяет задачи даты, сохраняет `manual`; `HistoryStore.schedule(date) -> list[dict]`.
- Produces: `GET /api/schedule?date=YYYY-MM-DD` — собирает вход и вызывает движок, **ничего не пишет**; приоритет: остаток дня, затем неделя; `estimates` — медиана длительности завершённых печатей по `part_key`, иначе 4 часа.
- Produces: `POST /api/schedule/apply {date, tasks}` — сохраняет утверждённые задачи.
- Produces: `HistoryStore.part_durations() -> dict[part_key, list[float]]`.

- [ ] **Step 1: Тесты** — дневной приоритет выше недельного, файл не готов, утверждение сохраняет и заменяет, GET не пишет, пустой план.
- [ ] **Step 2: RED**.
- [ ] **Step 3: Реализация** — `schedule_tasks` из спеки; сеть только для данных монитора, не для файлов; статусы файлов из реестра.
- [ ] **Step 4: GREEN** и коммит `feat: expose shift schedule API`.

### Task 3: Вкладка «Смена»

**Files:**
- Modify: `web/index.html`, `web/app.js`, `web/styles.css`

**Interfaces:**
- Consumes: `/api/schedule`, `/api/schedule/apply`, `/api/files/upload`.
- Produces: `renderShift()`, `shiftApply()`; `#panel-shift`, `#shift-date`, `#shift-summary`, `#shift-printers`, `#shift-unscheduled`.

- [ ] **Step 1: Экран** — дата, окно «09:00–18:00», итог «Позиций N · Копий M · В очереди K · Не влезло J»; таблица по принтерам: старт, финиш, номер, позиция, статус файла.
- [ ] **Step 2: Не влезло** — таблица с причиной; кнопка «Разложить файлы» загружает отсутствующие через `/api/files/upload`; «Утвердить смену» сохраняет.
- [ ] **Step 3: Поведение** — вне окна: «Смена закрыта, следующий запуск завтра в 09:00»; пустой план: «Импортируйте план»; ошибки тостом; кнопки блокируются на время запроса.
- [ ] **Step 4: Проверка** — `node --check`, ids, нет `innerHTML`, мобильная прокрутка.
- [ ] **Step 5: Коммит** `feat: add shift scheduling tab`.

### Task 4: Версия, README, приёмка

- [ ] Полный прогон тестов и `node --check`.
- [ ] Версия `2.11.0`; README: окно 09–18, приоритет, ночная печать, ограничения, ручной запуск.
- [ ] Перезапуск, живая проверка `/api/schedule` на реальном парке.
- [ ] Коммит `chore: release shift scheduler`.

## Review Focus

- Смена закрыта (после 18:00) — задач нет, причина понятна.
- Принтер освобождается в 17:50, печать 8 часов — запуск разрешён, следующий только завтра.
- Пауза не считается свободной; ETA паузы игнорируется.
- Остаток 0 или позиция без файла — не попадает в очередь.
- Утверждение не теряет задачи при повторном GET и не пишет при простом просмотре.
