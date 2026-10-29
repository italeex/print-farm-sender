# Этап 2: модуль файлов на принтерах

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Каталог деталей с номерами, матрица «деталь × принтер», массовые разложить/обновить/удалить файлы в папке `FARM`.

**Architecture:** SQLite хранит каталог, реестр файлов и режим папки по принтеру. Новый модуль `farm_files.py` говорит с Moonraker. Сервер отдаёт каталог и статусы, UI рисует таблицу и матрицу.

**Tech Stack:** Python stdlib, `requests`, SQLite, нативный JS/CSS.

**Spec:** `docs/superpowers/specs/2026-10-29-operator-modules-design.md`

## Global Constraints

- Python stdlib + `requests`; новых зависимостей нет.
- Русские подписи; `textContent`/`createElement`, без `innerHTML`.
- Файловые операции только внутри `FARM` (режим папки) или с префиксом `FARM_` (резерв).
- Удаление всегда с подтверждением и списком; печатающий принтер пропускается.
- Тесты не ходят на реальные принтеры; fake Moonraker на `127.0.0.1`.
- Запуск печати не добавляется.
- Версия и `EXPECTED_SERVER_VERSION` поднимаются вместе.

## Файловая структура

- `farm_history.py` — каталог, реестр, режимы, номера в плане и ОТК.
- `farm_files.py` — новый модуль операций Moonraker.
- `farm_server.py` — API каталога и файлов.
- `web/index.html`, `web/app.js`, `web/styles.css` — вкладка «Файлы».
- `tests/test_history.py`, `tests/test_files.py`, `tests/test_server.py`.

---

### Task 1: Каталог деталей и номера

**Files:**
- Modify: `farm_history.py`, `farm_server.py`, `web/app.js`
- Test: `tests/test_history.py`

**Interfaces:**
- Produces: `HistoryStore.catalog() -> list[dict]` — `{part_key, number, code, name, file_path, file_size, file_mtime}`; строки пополняются из `plan_items` и `events.parts_json`.
- Produces: `HistoryStore.set_catalog(part_key, number=None, file_path=None) -> dict`; номер целый > 0, уникальный; ошибки `ValueError` с русским текстом.
- Produces: `HistoryStore.catalog_map() -> dict[str, dict]`; `_period_items` добавляет `number` в каждую строку; `qc` берёт номер оттуда же.
- Produces: `GET /api/catalog`, `POST /api/catalog`.

- [ ] **Step 1: Тесты**

```python
def test_catalog_numbers_are_auto_and_unique(self):
    self.import_plan([{"date": "2026-10-29", "key": "a", "code": "A", "name": "A", "target": 2}])
    items = self.store().catalog()
    assert items[0]["number"] == 1
    with self.assertRaises(ValueError):
        self.store().set_catalog("a", number=0)
    with self.assertRaises(ValueError):
        self.store().set_catalog("a", number=2) if len(items) > 1 else None
```

- [ ] **Step 2: RED** — `python -m unittest tests.test_history -k catalog -v` → FAIL.
- [ ] **Step 3: Реализация**

Таблица `parts_catalog` из спеки с уникальным индексом. Пополнение при `catalog()`: `INSERT OR IGNORE` из `SELECT DISTINCT` плана и из завершённых событий (`parts_json` разбирается, имя через `split_code_name`). Новый номер = `MAX(number)+1`. `set_catalog` проверяет номер, обновляет `updated_at`. Миграция в `_migrate`.

- [ ] **Step 4: Маршруты** — `_api_catalog`, русские 400 на пустой `part_key`, неверный номер, отсутствующий файл.
- [ ] **Step 5: Номера в плане и ОТК** — `_period_items` и `qc` добавляют `number`; UI плана показывает `Номер N` перед названием.
- [ ] **Step 6: GREEN** — history и server тесты, `node --check web/app.js`.
- [ ] **Step 7: Коммит** `feat: add part catalog with numbers`.

### Task 2: Операции с файлами Moonraker

**Files:**
- Create: `farm_files.py`
- Modify: `farm_core.py` (параметр `path` при загрузке)
- Test: `tests/test_files.py`, `tests/test_server.py` (fake Moonraker)

**Interfaces:**
- Produces: `remote_name(number) -> "N7.gcode"`.
- Produces: `list_farm(printer, mode, session=None) -> list[dict]` — `{name, size, modified}`.
- Produces: `upload_to_farm(printer, file_path, name, mode, session=None) -> None`.
- Produces: `delete_from_farm(printer, names, mode, session=None) -> list[str]` — имена удалённых.
- Produces: `FarmFileError(Exception)` с русским текстом.
- Режимы: `folder` — `FARM/<name>`, `prefix` — `FARM_<name>` в корне.

- [ ] **Step 1: Тесты fake Moonraker**

Загрузка с `path=FARM`, список через `/server/files/directory?path=gcodes/FARM`, удаление через `DELETE /server/files/gcodes/FARM/<name>`, отказ папки (400) → `FarmFileError`; режим `prefix` даёт имя `FARM_N7.gcode`.

- [ ] **Step 2: RED** — `python -m unittest tests.test_files -v`.
- [ ] **Step 3: Реализация** — `requests.Session`, таймауты 10 с, `quote` для путей, понятные ошибки; загрузка через существующий прогресс-механизм не требуется.
- [ ] **Step 4: GREEN** и коммит `feat: add farm file operations`.

### Task 3: API каталога и файлов

**Files:**
- Modify: `farm_server.py`, `farm_history.py`
- Test: `tests/test_server.py`

**Interfaces:**
- Produces: `POST /api/files/sync {printers?: [key]}` → `{printers: {key: {ok, error?, files}}}`; пишет `file_registry` и `printer_files`.
- Produces: `POST /api/files/upload {parts: [part_key], printers: [key]}` → `{results: [{printer, part, ok, error?}]}`; имя `N<number>.gcode`; нет эталона — ошибка строки.
- Produces: `POST /api/files/delete {parts, printers}` → то же; печатающий принтер пропускается с ошибкой.
- Produces: `GET /api/catalog?sync=1` → каталог + `statuses: {printer: {part_key: "ok"|"stale"|"missing"|"printing"}}`.

- [ ] **Step 1: Тесты** — статусы `ok`/`stale`/`missing`/`printing`; удаление чужого файла вне `FARM` отклоняется; печатающий принтер пропущен; частичный успех при ошибке одного принтера.
- [ ] **Step 2: RED**.
- [ ] **Step 3: Реализация** — один общий помощник `_run_file_action`; блокировка записи в БД; режим из `printer_files`, по умолчанию `folder`.
- [ ] **Step 4: GREEN**, коммит `feat: expose catalog and file operations`.

### Task 4: Вкладка «Файлы» — каталог

**Files:**
- Modify: `web/index.html`, `web/app.js`, `web/styles.css`

**Interfaces:**
- Consumes: `/api/catalog`.
- Produces: `renderCatalog()`, `openCatalogFile(partKey)`; таблица `#catalog-table`, поле поиска `#catalog-search`, фильтр `#catalog-problems`.

- [ ] **Step 1: Таблица** — колонки: чекбокс, «Номер», «Позиция», «Файл» (имя, размер), «На принтерах» (сводка `12 из 13`), действие «Эталон…».
- [ ] **Step 2: Редактирование** — номер меняется по двойному клику/карандашу, `POST /api/catalog`; ошибка показывается тостом.
- [ ] **Step 3: Проверка** — `node --check`, ids, пустое состояние «каталог пуст — импортируйте план».
- [ ] **Step 4: Коммит** `feat: add files catalog table`.

### Task 5: Вкладка «Файлы» — матрица и массовые действия

**Files:**
- Modify: `web/index.html`, `web/app.js`, `web/styles.css`

**Interfaces:**
- Consumes: `/api/catalog?sync=1`, `/api/files/upload`, `/api/files/delete`, `/api/files/sync`.
- Produces: `renderFileMatrix()`, `selectedCatalogParts()`, `selectedMatrixPrinters()`.

- [ ] **Step 1: Матрица** — строки-детали, столбцы-принтеры, статусы цветом и текстом; выбор строк и столбцов; «только проблемы»; залипающие заголовки.
- [ ] **Step 2: Действия** — «Разложить», «Обновить», «Удалить с выбранных», «Удалить везде», «Обновить список»; перед удалением подтверждение со списком; после действия — результат по каждому принтеру.
- [ ] **Step 3: Прогресс** — блокировать кнопки на время операции, показывать «Разложено X из Y».
- [ ] **Step 4: Проверка** — `node --check`, подтверждение нельзя обойти, печатающий принтер помечен и пропущен.
- [ ] **Step 5: Коммит** `feat: add file matrix with bulk actions`.

### Task 6: Версия, README, приёмка

- [ ] Полный прогон тестов и `node --check`.
- [ ] Версия `2.10.0`, README: каталог, номера, папка `FARM`, массовые действия, режим `FARM_`.
- [ ] Перезапуск сервера, проверка живого каталога и матрицы.
- [ ] Коммит `chore: release files module`.

## Review Focus

- Пустой каталог: матрица и кнопки не падают, действия недоступны.
- Деталь без эталона: «Разложить» недоступна, остальные действия работают.
- Печатающий принтер: удаление и перезапись его файла пропускаются с понятной ошибкой.
- Ошибка одного принтера: остальные завершаются, итог честный.
- Номер меняется на занятый: 400 по-русски, данные не меняются.
