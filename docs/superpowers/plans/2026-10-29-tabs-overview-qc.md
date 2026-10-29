# Этап 1: вкладки, обзор, полоса плана, ОТК

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Разложить дашборд по вкладкам, показать прогресс плана «готово + в печати» и список ОТК за смену.

**Architecture:** Одна страница, hash-маршруты, без фреймворка. Новая вкладка — секция в `index.html` и функция показа в `app.js`. План переезжает из модалки во вкладку с сохранением id. ОТК считается на сервере из завершённых событий.

**Tech Stack:** Python stdlib, `requests`, SQLite, нативный JS/CSS.

**Spec:** `docs/superpowers/specs/2026-10-29-operator-modules-design.md`

## Global Constraints

- Python stdlib + `requests`; новых зависимостей нет.
- Все подписи по-русски; `textContent`/`createElement`, без `innerHTML`.
- Запуск печати не добавляется; управление только пауза/продолжить/отмена.
- Тесты не ходят на реальные принтеры; fake Moonraker на `127.0.0.1`.
- Существующие id и поведение карточек, истории, плана не ломаются.
- Версия сервера и `EXPECTED_SERVER_VERSION` поднимаются вместе.

## Файловая структура

- `farm_history.py` — метод `qc`.
- `farm_server.py` — маршрут `/api/qc` и `/api/qc.csv`.
- `web/index.html` — навигация, панели вкладок, блоки обзора, окно ОТК.
- `web/app.js` — маршрутизация, рендер обзора, ОТК, перенос плана.
- `web/styles.css` — вкладки, полоса, блоки, печать ОТК.
- `tests/test_history.py`, `tests/test_server.py` — тесты.

---

### Task 1: Список ОТК на сервере

**Files:**
- Modify: `farm_history.py` (метод после `calendar`)
- Test: `tests/test_history.py`

**Interfaces:**
- Produces: `HistoryStore.qc(day: str, start_hour: int = 9) -> dict`, где `day` — `YYYY-MM-DD`, период `[day-1 09:00, day 09:00)`, результат `{"date", "start", "end", "items": [{"part_key", "number", "code", "name", "count"}], "positions", "total"}`.

- [ ] **Step 1: Написать падающий тест**

```python
def test_qc_groups_shift_completions(self):
    store = self.store()
    store.record(self.view("complete", parts=[{"name": "МВСЛ.ГР3.01.42000.01 Крышка", "count": 3}]), now=self.at("2026-10-28 10:00"))
    store.record(self.view("complete", parts=[{"name": "МФСЛ.ГР3.01.42000.01_Крышка.stl", "count": 2}]), now=self.at("2026-10-29 08:30"))
    store.record(self.view("complete", parts=[{"name": "МВСЛ.ГР3.01.42000.01 Крышка", "count": 9}]), now=self.at("2026-10-29 09:30"))
    result = store.qc("2026-10-29")
    assert result["total"] == 5
    assert result["positions"] == 1
    assert result["items"][0]["count"] == 5
```

- [ ] **Step 2: Прогнать тест, убедиться, что падает**

Run: `python -m unittest tests.test_history -k qc -v`
Expected: FAIL, `AttributeError: qc`.

- [ ] **Step 3: Реализовать `qc`**

Период: `datetime(day-1, 09:00)` и `datetime(day, 09:00)` в локальной зоне, через `_local_ts`. Выборка `event='complete'`, `ts >= start AND ts < end`, только `parts_json`. Группировка по `part_key(name)`; имя и код — через `split_code_name`; `number` из `parts_catalog`, если таблица уже есть (иначе `None`). Сортировка по номеру, затем по имени.

- [ ] **Step 4: Тест проходит**

Run: `python -m unittest tests.test_history -k qc -v`
Expected: PASS.

- [ ] **Step 5: Коммит**

```bash
git add farm_history.py tests/test_history.py
git commit -m "feat: add QC shift report to history store"
```

### Task 2: Маршруты ОТК

**Files:**
- Modify: `farm_server.py` (`do_GET`, новый `_api_qc`, `_api_qc_csv`)
- Test: `tests/test_server.py`

**Interfaces:**
- Consumes: `HistoryStore.qc`.
- Produces: `GET /api/qc?date=YYYY-MM-DD` → JSON из Task 1; `GET /api/qc.csv?date=...` → `text/csv; charset=utf-8`, колонки `Позиция,Количество`; дата по умолчанию — сегодня; неверная дата — 400 по-русски.

- [ ] **Step 1: Тесты**

```python
def test_api_qc_returns_items(self):
    self.complete("2026-10-29 08:00", [{"name": "МВСЛ.ГР3.01.42000.01 Крышка", "count": 4}])
    data = self.get_json("/api/qc?date=2026-10-29")
    assert data["total"] == 4

def test_api_qc_csv(self):
    ...
    assert response.headers["Content-Type"].startswith("text/csv")
    assert "Крышка" in response.text
```

- [ ] **Step 2: Убедиться, что падает**

Run: `python -m unittest tests.test_server -k qc -v`
Expected: FAIL, 404.

- [ ] **Step 3: Реализовать маршруты**

`_query_text(query, "date")`; проверка `_iso_day`; ошибка `Неверная дата`. CSV через `csv`/`io.StringIO`, BOM не добавлять.

- [ ] **Step 4: Тесты проходят**

Run: `python -m unittest tests.test_server -k qc -v`
Expected: PASS.

- [ ] **Step 5: Коммит**

```bash
git add farm_server.py tests/test_server.py
git commit -m "feat: expose QC report endpoints"
```

### Task 3: Каркас вкладок

**Files:**
- Modify: `web/index.html`, `web/styles.css`, `web/app.js`

**Interfaces:**
- Produces: `showTab(name)`, `TABS = ["overview", "files", "plan", "shift", "printers", "log"]`, hash `#/overview`; панели `#panel-overview`, `#panel-files`, `#panel-plan`, `#panel-shift`, `#panel-printers`, `#panel-log`.

- [ ] **Step 1: Разметка**

В шапку — `nav.tabs` с шестью кнопками `data-tab`. Под шапкой — существующие `summary`, `toolbar`, сетка карточек внутри `#panel-overview`. Панели «Файлы», «Смена» — заглушки «Скоро». `#journal-panel` переезжает внутрь `#panel-log`. Кнопки «План», «Печатается» остаются в шапке как быстрые действия.

- [ ] **Step 2: Роутер**

```js
function showTab(name) {
  const next = TABS.includes(name) ? name : "overview";
  document.querySelectorAll("[data-tab]").forEach((el) => el.setAttribute("aria-selected", String(el.dataset.tab === next)));
  document.querySelectorAll("[data-panel]").forEach((el) => { el.hidden = el.dataset.panel !== next; });
  state.tab = next;
  if (location.hash !== "#/" + next) history.replaceState(null, "", "#/" + next);
  if (next === "plan") openPlanTab();
  if (next === "log") refreshJournal();
}
window.addEventListener("hashchange", () => showTab(location.hash.replace("#/", "")));
```

- [ ] **Step 3: Проверка**

Run: `node --check web/app.js`
Expected: exit 0. Вручную: каждая вкладка открывается, карточки и фильтры работают.

- [ ] **Step 4: Коммит**

```bash
git add web/index.html web/styles.css web/app.js
git commit -m "feat: add tab navigation shell"
```

### Task 4: План во вкладке

**Files:**
- Modify: `web/index.html`, `web/app.js`, `web/styles.css`

**Interfaces:**
- Consumes: существующие `renderPlanCalendar`, `renderPlanSelection`, `#modal-plan`.
- Produces: `openPlanTab()`, `closePlanTab()`; те же id внутри `#panel-plan`.

- [ ] **Step 1: Перенести разметку**

Содержимое `#modal-plan .modal-body` и `footer` переносится в `#panel-plan`; модалка и её обработчики закрытия удаляются. Кнопка «План» вызывает `showTab("plan")`.

- [ ] **Step 2: Поправить открытие/закрытие**

`openPlanTab()` загружает календарь, если он ещё не загружен, и не добавляет модалку в стек. `Escape` на вкладке плана возвращает «Обзор».

- [ ] **Step 3: Проверка**

Run: `node --check web/app.js`
Expected: exit 0. Вручную: месяц, день, неделя, импорт, «Сегодня» работают.

- [ ] **Step 4: Коммит**

```bash
git add web/index.html web/app.js web/styles.css
git commit -m "refactor: move plan calendar into a tab"
```

### Task 5: Полоса выполнения плана

**Files:**
- Modify: `web/index.html`, `web/app.js`, `web/styles.css`

**Interfaces:**
- Consumes: `/api/plan` (`.day`, `.week` с `parts_target`, `parts_done`, `parts_in_progress`).
- Produces: `renderPlanProgress(container, period)`; блоки `#overview-progress`, `#plan-progress`.

- [ ] **Step 1: Разметка и рендер**

Два сегмента: `.progress-done`, `.progress-live`; подпись `101 + 240 из 1225 · 34% + 20%`. Проценты от `parts_target`, при нуле — «план не задан» и скрытая полоса. `title` — «Учтено X из Y».

- [ ] **Step 2: Проверка**

Run: `node --check web/app.js`
Expected: exit 0. Вручную: числа совпадают с `/api/plan`, полоса не переполняется при `done + live > target`.

- [ ] **Step 3: Коммит**

```bash
git add web/index.html web/app.js web/styles.css
git commit -m "feat: show completed and in-progress plan progress"
```

### Task 6: Рекомендация «Запустить сейчас»

**Files:**
- Modify: `web/index.html`, `web/app.js`, `web/styles.css`

**Interfaces:**
- Consumes: `/api/state`, `/api/plan`.
- Produces: `buildAdvice() -> [{printer, part, remaining, eta}]`, блок `#advice`.

- [ ] **Step 1: Логика**

Свободные принтеры (`idle`) + позиции дня с остатком `max(0, target - done - in_progress)`, сортировка по остатку. До трёх строк: номер, название, остаток, принтер. Если нет свободных — «Все принтеры заняты». Кнопки: «План» и «Обзор печати».

- [ ] **Step 2: Проверка**

Run: `node --check web/app.js`
Expected: exit 0. Вручную: при 2 свободных и остатке 16 видно две строки.

- [ ] **Step 3: Коммит**

```bash
git add web/index.html web/app.js web/styles.css
git commit -m "feat: add start-now recommendation block"
```

### Task 7: Окно ОТК

**Files:**
- Modify: `web/index.html`, `web/app.js`, `web/styles.css`

**Interfaces:**
- Consumes: `/api/qc?date=`, `/api/qc.csv?date=`.
- Produces: `openQc()`, `renderQc(data)`; блок `#qc-card`, окно `#modal-qc`.

- [ ] **Step 1: Разметка и рендер**

Дата по умолчанию — сегодня (смена, закончившаяся сегодня). Таблица «Позиция / Количество», итог «Позиций: N · Деталей: M». Кнопки: «Копировать» (`navigator.clipboard`, fallback — `textarea`), «CSV» (`location.href`), «Печать» (`window.print()`), стрелки даты. Пусто — «За период ничего не завершено».

- [ ] **Step 2: Проверка**

Run: `node --check web/app.js`
Expected: exit 0. Вручную: сумма строк равна итогу, копирование даёт текст «Позиция — N» построчно.

- [ ] **Step 3: Коммит**

```bash
git add web/index.html web/app.js web/styles.css
git commit -m "feat: add QC report dialog"
```

### Task 8: Версия, документация, приёмка

**Files:**
- Modify: `farm_server.py`, `web/app.js`, `README.md`

- [ ] **Step 1: Прогон**

Run: `python -m unittest discover -s tests` и `node --check web/app.js`
Expected: все тесты OK, exit 0.

- [ ] **Step 2: Версия**

`APP_VERSION` и `EXPECTED_SERVER_VERSION` → `2.9.0`.

- [ ] **Step 3: README**

Раздел про вкладки, полосу, ОТК; обновить число тестов.

- [ ] **Step 4: Коммит**

```bash
git add farm_server.py web/app.js README.md
git commit -m "chore: release tabs and QC report"
```

## Review Focus

- Пустой план (`parts_target = 0`) — полоса не рисуется, нет деления на ноль.
- `done + in_progress > target` — сегменты не выходят за 100%.
- ОТК на границе 09:00 — событие ровно в 09:00 относится к следующей смене.
- Принтер без деталей в текущей печати — рекомендация не падает.
- Переход по прямой ссылке `#/plan` до загрузки состояния — вкладка открывается без ошибок.
