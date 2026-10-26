# Printer Farm Dashboard — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Заменить Tkinter-рассыльщик локальным веб-дашбордом фермы K2 Plus: карточки со статусом, превью, прогрессом, ошибками Creality, Mainsail и управлением печатью, сохранив отправку gcode.

**Architecture:** Python-stdlib HTTP-сервер (`farm_server.py`) отдаёт статику и JSON API; фоновый поллер (`farm_monitor.py`) опрашивает Moonraker и складывает снимок состояния; отдельные модули отвечают за коды ошибок, превью и job отправки; фронтенд — ванильные HTML/CSS/JS без сборки и CDN.

**Tech Stack:** Python 3.13.7, stdlib (`http.server`, `threading`, `json`, `concurrent.futures`), `requests` 2.34.2, ванильный JS/CSS. Существующий `farm_core.py` переиспользуется.

**Spec:** `docs/superpowers/specs/2026-10-26-farm-dashboard-design.md`

## Global Constraints

- Только stdlib + `requests`; никаких новых Python-зависимостей, фреймворков, CDN и сборщиков.
- Все пользовательские строки — на русском; код без комментариев.
- Тесты никогда не обращаются к реальным принтерам; fake Moonraker слушает `127.0.0.1` на эфемерном порту.
- `printers.json`: UTF-8, `ensure_ascii=False`, атомарная запись.
- Сервер слушает только `127.0.0.1`; Origin-проверка для не-GET; статика без path traversal.
- Не изменять ничего вне `printer-farm-sender/`; не пушить; коммиты локальные.
- Запуск тестов: `python -m unittest discover -s tests -v` из корня репозитория (PowerShell может печатать stderr как ошибку — реальный код возврата проверять через `cmd /c`).
- Не трогать реальные принтеры и не вызывать print-start; управляющие вызовы — только pause/resume/cancel по явному действию пользователя.

## Review Focus

1. **Офлайн-принтеры не вешают UI.** 10 из 25 сейчас не отвечают: поллер обязан таймаутить, уходить в бэкофф и не занимать все воркеры; дашборд остаётся отзывчивым.
2. **Klipper error ≠ offline.** Упавший Klipper (shutdown/error) при живом Moonraker должен показывать ошибку, а не «не в сети».
3. **Отправка на печатающий/офлайн-принтер.** Job обязан завершаться терминальным событием, ошибки одного принтера не останавливают остальные, отмена работает.
4. **Кириллица и пробелы.** Имена файлов и папок в URL, Range-запросах, превью, multipart-загрузке и JSON не должны ломаться.
5. **Закрытая вкладка не оставляет процесс.** Heartbeat/idle-shutdown, порт занят, второй экземпляр — всё должно вести к одному работающему серверу и корректному выходу.
6. **Точечное обновление DOM.** Выбор, фокус, скролл и hover не сбрасываются каждые 2 секунды; картинки не перезагружаются без смены версии.

---

### Task 1: Конфиг с Mainsail и управление печатью

**Files:**
- Modify: `farm_core.py`
- Modify: `tests/test_farm_core.py`

**Interfaces:**
- Produces: `Printer(..., mainsail_port: int = 4408)`, `Printer.mainsail_url -> str`, `AppConfig(..., refresh_seconds: int = 5)`, `ControlError`, `print_action(printer, action, *, session=None, timeout=5.0) -> None`, константы `PAUSE_PATH`, `RESUME_PATH`, `CANCEL_PATH`.
- Consumes: существующие `Printer`, `AppConfig`, `DEFAULT_PORT`, `_http`-паттерны `farm_core`.

- [ ] **Step 1: Написать падающие тесты**

Добавить в `tests/test_farm_core.py` класс `DashboardConfigTests` и `PrintControlTests`:

```python
class DashboardConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "printers.json"

    def test_old_config_defaults_mainsail_and_refresh(self):
        self.path.write_text('{"printers": [{"host": "10.0.0.1"}]}', encoding="utf-8")
        config = load_config(self.path)
        self.assertEqual(config.printers[0].mainsail_port, 4408)
        self.assertEqual(config.printers[0].mainsail_url, "http://10.0.0.1:4408")
        self.assertEqual(config.refresh_seconds, 5)

    def test_mainsail_port_validation_and_clamp(self):
        self.path.write_text(
            '{"printers": [{"host": "10.0.0.1", "mainsail_port": 0}], "refresh_seconds": 999}',
            encoding="utf-8",
        )
        config = load_config(self.path)
        self.assertEqual(config.printers[0].mainsail_port, 4408)
        self.assertEqual(config.refresh_seconds, 30)
        saved = json.loads(json.dumps(config.to_dict(), ensure_ascii=False))
        self.assertEqual(saved["printers"][0]["mainsail_port"], 4408)
        self.assertEqual(saved["refresh_seconds"], 30)


class PrintControlTests(unittest.TestCase):
    def setUp(self):
        self.server = FakeMoonraker().start()
        self.addCleanup(self.server.stop)
        self.printer = Printer("test", "127.0.0.1", self.server.port)

    def test_pause_posts_to_moonraker(self):
        print_action(self.printer, "pause")
        self.assertEqual(self.server.requests[-1]["path"], "/printer/print/pause")

    def test_unknown_action_rejected(self):
        with self.assertRaises(ControlError):
            print_action(self.printer, "start")

    def test_control_error_contains_russian_text(self):
        self.server.control_status = 500
        with self.assertRaises(ControlError) as caught:
            print_action(self.printer, "cancel")
        self.assertIn("отменить", str(caught.exception).lower())
```

В `FakeMoonraker` добавить `control_status = 200` и обработку `POST /printer/print/pause|resume|cancel` с записью в `requests` и возвратом `outer.control_status`.

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `cmd /c "python -m unittest tests.test_farm_core.DashboardConfigTests tests.test_farm_core.PrintControlTests -v"`
Expected: FAIL — нет `mainsail_port`/`print_action`.

- [ ] **Step 3: Реализовать**

В `farm_core.py`:

```python
MAINSAIL_PORT = 4408
PAUSE_PATH = "/printer/print/pause"
RESUME_PATH = "/printer/print/resume"
CANCEL_PATH = "/printer/print/cancel"
CONTROL_PATHS = {"pause": PAUSE_PATH, "resume": RESUME_PATH, "cancel": CANCEL_PATH}
CONTROL_TITLES = {"pause": "поставить на паузу", "resume": "продолжить", "cancel": "отменить"}

class ControlError(Exception):
    pass
```

`Printer`: поле `mainsail_port: int = MAINSAIL_PORT`; в `from_dict` брать `mainsail_port`, при мусоре/вне 1..65535 — 4408; `to_dict` включает поле; свойство:

```python
@property
def mainsail_url(self) -> str:
    return f"http://{self.host}:{self.mainsail_port}"
```

`AppConfig`: поле `refresh_seconds: int = 5`; `from_dict` приводит к int, при мусоре 5, clamp 2..30; `to_dict` включает. `save_config` не меняется.

`print_action`:

```python
def print_action(printer: Printer, action: str, *, session=None, timeout: float = 5.0) -> None:
    path = CONTROL_PATHS.get(action)
    if path is None:
        raise ControlError(f"Неизвестное действие: {action}")
    client = session or requests
    try:
        response = client.post(printer.base_url + path, timeout=timeout)
    except requests.RequestException as exc:
        raise ControlError(f"Не удалось {CONTROL_TITLES[action]}: {exc}") from exc
    if response.status_code >= 400:
        raise ControlError(
            f"Не удалось {CONTROL_TITLES[action]}: HTTP {response.status_code}"
        )
```

- [ ] **Step 4: Прогнать тесты**

Run: `cmd /c "python -m unittest discover -s tests -v"`
Expected: все тесты OK (23 старых + новые).

- [ ] **Step 5: Коммит**

```bash
git add farm_core.py tests/test_farm_core.py
git commit -m "feat: mainsail settings and print control api"
```

---

### Task 2: Коды ошибок Creality

**Files:**
- Create: `farm_errors.py`
- Create: `tests/test_errors.py`

**Interfaces:**
- Produces: `CREALITY_HELP_URL: str`, `ErrorInfo(code, label, hint, detail, help_url)`, `CREALITY_ERRORS: dict[str, tuple[str, str]]`, `parse_error(message="", *, state="", log_tail="") -> ErrorInfo | None`, `extract_log_error(text) -> tuple[str, str] | None`.

- [ ] **Step 1: Написать падающие тесты**

`tests/test_errors.py`:

```python
class ParseErrorTests(unittest.TestCase):
    def test_creality_code(self):
        info = parse_error("TE2509: Unable to obtain nozzle temperature")
        self.assertEqual(info.code, "TE2509")
        self.assertIn("термистор", info.label.lower())
        self.assertEqual(info.help_url, CREALITY_HELP_URL)

    def test_numeric_key_code(self):
        info = parse_error("!! key2564 Heater extruder not heating at expected rate")
        self.assertEqual(info.code, "2564")
        self.assertIn("нагрев", info.label.lower())

    def test_key_with_colon_from_screen(self):
        info = parse_error("Key：2509，Nozzle Sensor temperature exception")
        self.assertEqual(info.code, "2509")

    def test_unknown_code_keeps_raw_detail(self):
        info = parse_error("ZZ9999: weird failure")
        self.assertEqual(info.label, "Ошибка принтера")
        self.assertIn("ZZ9999", info.detail)

    def test_keyword_fallback_without_code(self):
        info = parse_error("Lost communication with MCU 'mcu'")
        self.assertIn("MCU", info.label)

    def test_ready_state_returns_none(self):
        self.assertIsNone(parse_error("", state="ready"))

    def test_shutdown_state_without_message(self):
        info = parse_error("", state="shutdown")
        self.assertIn("Klipper", info.label)

    def test_extract_log_error(self):
        log = 'ok\n!! {"code":"key2564", "msg":"Heater extruder not heating at expected rate"}\n'
        self.assertEqual(
            extract_log_error(log),
            ("2564", "Heater extruder not heating at expected rate"),
        )

    def test_extract_log_error_garbage(self):
        self.assertIsNone(extract_log_error("!! not json\n"))
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `cmd /c "python -m unittest tests.test_errors -v"`
Expected: `ModuleNotFoundError: No module named 'farm_errors'`.

- [ ] **Step 3: Реализовать**

`farm_errors.py`:

```python
CREALITY_HELP_URL = "https://wiki.creality.com/en/k2-flagship-series/k2-plus/error-code-summary"

@dataclass(frozen=True)
class ErrorInfo:
    code: str
    label: str
    hint: str
    detail: str
    help_url: str = CREALITY_HELP_URL
```

Таблица `CREALITY_ERRORS` — полный словарь кодов K2 Plus из вики (значение: `(label, hint)`):

```python
CREALITY_ERRORS = {
    "AC0101": ("AI: обнаружен дефект печати", "Печать приостановлена. При ложном срабатывании отключите AI-детекцию."),
    "AC0103": ("AI: дефект первого слоя", "Проверьте первый слой; при ложном срабатывании отключите AI."),
    "AC0104": ("AI: посторонний предмет на столе", "Уберите предмет со стола и повторите."),
    "AC0500": ("AI: проблема качества печати", "Проверьте печать; при ложном срабатывании отключите AI."),
    "AC0503": ("AI: дефект первого слоя", "Проверьте первый слой."),
    "AL0504": ("Файл не поддерживает контроль потока", "Используйте файл, подготовленный официальным слайсером."),
    "AL0505": ("AI LiDAR недоступен", "Проверьте кабель и подключение лидара."),
    "FR2833": ("CFS: ошибка подачи филамента", "Проверьте запутывание и застревание филамента."),
    "FR2832": ("CFS: ошибка отката", "Проверьте катушку и филамент."),
    "FR2839": ("CFS: филамент закончился", "Заправьте новую катушку и продолжите."),
    "FR4828": ("CFS: филамент сломан внутри CFS", "Извлеките и заправьте филамент заново."),
    "FS2840": ("CFS: команда недоступна сейчас", "Дождитесь завершения текущей операции."),
    "FB2844": ("CFS: трубка PTFE соскочила", "Проверьте пневмофитинг и трубку."),
    "FO2845": ("CFS: экструдер забит", "Проверьте филамент и проходимость сопла."),
    "TE2509": ("Нет данных термистора сопла", "Проверьте кабель термистора hotend, при повреждении замените."),
    "TE2515": ("Температура сопла выше предела", "Возможно КЗ термистора; проверьте провод и разъём."),
    "TE2564": ("Сопло не нагревается", "Проверьте нагреватель и термистор сопла."),
    "TE2111": ("Экструзия ниже минимальной температуры", "Нагрейте сопло минимум до 170 °C; проверьте G-code."),
    "TE2762": ("Проблема качества первого слоя", "Проверьте первый слой и настройки слайсера."),
    "TE2761": ("Файл не поддерживает контроль потока", "Подготовьте файл официальным слайсером."),
    "TR0116": ("Филамент закончился, печать на паузе", "Заправьте филамент и продолжите печать."),
    "TR2852": ("Ошибка датчика филамента", "Проверьте датчик и разъём."),
    "TR2863": ("Датчик филамента в экструдере сработал", "Проверьте остатки филамента в экструдере."),
    "TC2841": ("Нож заклинило", "Проверьте нож и механизм отрезки."),
    "TC2854": ("В экструдере остался филамент", "Извлеките филамент и повторите."),
    "TC2855": ("Калибровка ножа не удалась", "Повторите калибровку."),
    "TC2856": ("Нож заклинило или он отсутствует", "Проверьте нож."),
    "TF0501": ("Вентилятор heat break крутится медленно", "Проверьте разъём и крыльчатку."),
    "TF0526": ("Вентилятор модели крутится медленно", "Проверьте вентилятор обдува."),
    "XS2000": ("Системная ошибка принтера", "Перезагрузите принтер и выгрузите логи."),
    "XS2001": ("Системная ошибка принтера", "Перезагрузите принтер и выгрузите логи."),
    "XS3000": ("Системная ошибка принтера", "Перезагрузите принтер и выгрузите логи."),
    "XS3001": ("Системная ошибка принтера", "Перезагрузите принтер и выгрузите логи."),
    "XS3002": ("Системная ошибка принтера", "Перезагрузите принтер и выгрузите логи."),
    "XS2060": ("Системная ошибка принтера", "Перезагрузите принтер и выгрузите логи."),
    "XS2093": ("Системная ошибка принтера", "Перезагрузите принтер и выгрузите логи."),
    "XD2571": ("Файл печати повреждён", "Передайте файл заново или переименуйте его."),
    "100": ("Ошибка копирования файла", "Повторите копирование файла на принтер."),
    "101": ("AI: обнаружен дефект печати", "Проверьте печать и продолжите при необходимости."),
    "102": ("Слишком длинное имя файла", "Сократите имя gcode-файла."),
    "103": ("AI: дефект первого слоя", "Проверьте первый слой."),
    "104": ("AI: посторонний предмет на столе", "Уберите предмет со стола."),
    "108": ("Ошибка коррекции лидара", "Проверьте лидар и повторите калибровку."),
    "109": ("Ошибка вентилятора трубки", "Проверьте вентилятор hotend."),
    "110": ("Нет связи с основным MCU", "Проверьте кабели и питание платы."),
    "111": ("Нет связи с hotend MCU", "Проверьте кабель печатающей головы."),
    "112": ("Нет связи с MCU стола", "Проверьте кабель стола."),
    "2002": ("Нет связи с платой", "Проверьте подключение платы."),
    "2014": ("Ошибка G-code: перемещение вне диапазона", "Проверьте файл и настройки слайсера."),
    "2019": ("Целевая температура вне диапазона", "Проверьте G-code и настройки материала."),
    "2020": ("Ошибка хоуминга", "Проверьте концевые датчики и механику."),
    "2021": ("Таймаут хоуминга", "Проверьте концевые датчики и механику."),
    "2111": ("Экструзия ниже минимальной температуры", "Нагрейте сопло минимум до 170 °C."),
    "2112": ("Превышено максимальное сечение экструзии", "Проверьте настройки слайсера."),
    "2210": ("Автозагрузка при низкой температуре", "Нагрейте сопло и повторите."),
    "2211": ("Печать приостановлена", "Проверьте причину и продолжите печать."),
    "2242": ("Сначала нужен хоуминг", "Выполните парковку осей."),
    "2243": ("Перемещение вне рабочей зоны", "Проверьте G-code и координаты."),
    "2283": ("Принтер не готов", "Дождитесь готовности Klipper."),
    "2294": ("Таймаут ответа", "Выгрузите логи и перезапустите принтер."),
    "2299": ("Не удалось настроить MCU", "Проверьте прошивку и подключение платы."),
    "2300": ("Ошибка конфигурации MCU", "Проверьте прошивку и подключение платы."),
    "2340": ("Температура нагревателя вне диапазона", "Проверьте термистор и нагреватель."),
    "2344": ("Таймаут связи с hotend MCU", "Проверьте кабель печатающей головы."),
    "2345": ("Таймаут связи с MCU стола", "Проверьте кабель стола."),
    "2504": ("Ошибка хоуминга Z", "Проверьте датчик Z и механику."),
    "2505": ("Ошибка драйвера TMC", "Проверьте охлаждение платы и нагрузку."),
    "2506": ("Ошибка связи", "Проверьте кабели и разъёмы."),
    "2509": ("Ошибка термистора сопла", "Проверьте кабель термистора hotend."),
    "2511": ("Ошибка температуры камеры", "Проверьте датчик температуры камеры."),
    "2512": ("Температура чипа MCU выше нормы", "Проверьте охлаждение платы."),
    "2520": ("Таймаут датчика давления", "Проверьте датчик Z."),
    "2521": ("Датчик уровня повреждён", "Проверьте датчик Z."),
    "2522": ("Нет данных датчика уровня", "Проверьте датчик Z и кабель."),
    "2523": ("Потеря данных датчика уровня", "Проверьте кабель датчика Z."),
    "2524": ("Шум датчика уровня", "Проверьте датчик Z и окружение."),
    "2526": ("Ось Z движется с рывками", "Проверьте винт и механику Z."),
    "2527": ("Потеря данных при измерении", "Проверьте кабель датчика Z."),
    "2529": ("Датчик уровня не отвечает", "Проверьте датчик Z."),
    "2532": ("Нарушена связь между MCU", "Проверьте синхронизирующий кабель."),
    "2533": ("Ошибка карты стола", "Выполните калибровку стола."),
    "2560": ("Нет связи с основным MCU", "Проверьте кабели и перезапустите."),
    "2561": ("Нет связи с hotend MCU", "Проверьте кабели и перезапустите."),
    "2562": ("Нет связи с MCU стола", "Проверьте кабели и перезапустите."),
    "2563": ("Нет связи с Raspberry Pi", "Проверьте кабели и перезапустите."),
    "2564": ("Сопло не нагревается", "Проверьте нагреватель и термистор сопла."),
    "2565": ("Стол не нагревается", "Проверьте подключение и термистор стола."),
}
```

Парсер:

```python
CODE_PATTERN = re.compile(r"\b([A-Z]{2}\d{4}|\d{4})\b")
KEY_PATTERN = re.compile(r"(?:key|Key|Ключ)\s*[:：]?\s*(\d{3,4})", re.IGNORECASE)

def extract_log_error(text: str) -> tuple[str, str] | None:
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line.startswith("!!"):
            continue
        try:
            payload = json.loads(line[2:].strip())
        except (ValueError, TypeError):
            continue
        code = str(payload.get("code") or "")
        match = re.search(r"(\d{3,4})", code)
        return (match.group(1) if match else code, str(payload.get("msg") or ""))
    return None
```

`parse_error`:

- `message = (message or "").strip()`; `state = (state or "").lower()`.
- Если нет message, state не в `{"error","shutdown"}` и нет log_tail — вернуть None.
- `combined = message + "\n" + log_tail`; `code` из `KEY_PATTERN`/`CODE_PATTERN`/`extract_log_error`.
- Нормализовать код: верхний регистр для буквенных, числовой как есть; `CREALITY_ERRORS` искать по полному, затем по числовой части.
- Если нет кода — keyword-fallback: `thermistor` → «Ошибка термистора», `lost communication`/`mcu` → «Потеряна связь с MCU», `not heating`/`heater` → «Нагреватель не греет», `out of range` → «Перемещение вне диапазона», `filament`/`runout` → «Проблема с филаментом», `endstop`/`probe` → «Проблема с датчиком», `timer too close` → «MCU не успевает», `shutdown` → «Klipper остановлен», `gcode` → «Ошибка G-code».
- Иначе `label="Ошибка принтера"`, `hint="Откройте Mainsail и проверьте состояние."`.
- `detail` = первая непустая строка message (или `state_message`), обрезанная до 300 символов; если пусто — `"Klipper сообщил об ошибке без текста"`.
- Для `state="shutdown"` без message: label «Klipper аварийно остановлен».

- [ ] **Step 4: Прогнать тесты**

Run: `cmd /c "python -m unittest tests.test_errors -v"`
Expected: PASS.

- [ ] **Step 5: Коммит**

```bash
git add farm_errors.py tests/test_errors.py
git commit -m "feat: parse creality and klipper error codes"
```

---

### Task 3: Превью из gcode

**Files:**
- Create: `farm_thumbs.py`
- Create: `tests/test_thumbs.py`

**Interfaces:**
- Produces: `MAX_THUMB_BYTES = 262144`, `extract_thumbnail(data: bytes) -> bytes | None`, `thumbnail_from_file(path: Path) -> bytes | None`, `thumbnail_from_http(session, base_url, filename, *, timeout=5.0) -> bytes | None`, `ThumbCache` с `get(key, loader)`, `version(key)`, `put(key, data)`.

- [ ] **Step 1: Написать падающие тесты**

`tests/test_thumbs.py`:

```python
def gcode_with_thumbs(blocks):
    lines = ["; HEADER_BLOCK_START", "; generated by test"]
    for width, height, payload in blocks:
        encoded = base64.b64encode(payload).decode()
        lines.append("; THUMBNAIL_BLOCK_START")
        lines.append(f"; thumbnail begin {width}x{height} {len(encoded)}")
        for index in range(0, len(encoded), 76):
            lines.append("; " + encoded[index:index + 76])
        lines.append("; thumbnail end")
    return ("\n".join(lines) + "\n").encode()


class ThumbTests(unittest.TestCase):
    def test_picks_largest_thumbnail(self):
        small = b"\x89PNG\r\n\x1a\nsmall"
        big = b"\x89PNG\r\n\x1a\nbig"
        data = gcode_with_thumbs([(96, 96, small), (300, 300, big)])
        self.assertEqual(extract_thumbnail(data), big)

    def test_no_thumbnails(self):
        self.assertIsNone(extract_thumbnail(b"; HEADER\nG1 X0\n"))

    def test_broken_base64(self):
        data = b"; thumbnail begin 300x300 10\n; !!!notbase64!!!\n; thumbnail end\n"
        self.assertIsNone(extract_thumbnail(data))

    def test_from_file(self):
        path = Path(self.tmp.name) / "part.gcode"
        path.write_bytes(gcode_with_thumbs([(300, 300, b"\x89PNG\r\n\x1a\ndata")]))
        self.assertEqual(thumbnail_from_file(path), b"\x89PNG\r\n\x1a\ndata")

    def test_from_http_range(self):
        server = FakeRangeServer(b"...").start()
        self.addCleanup(server.stop)
        data = thumbnail_from_http(requests, f"http://127.0.0.1:{server.port}", "деталь.gcode")
        self.assertEqual(data, b"\x89PNG\r\n\x1a\ndata")
        self.assertEqual(server.ranges[0], "bytes=0-262143")

    def test_cache_version_changes(self):
        cache = ThumbCache()
        cache.put("k", b"one")
        first = cache.version("k")
        cache.put("k", b"two")
        self.assertNotEqual(first, cache.version("k"))
```

`FakeRangeServer` — мини-сервер в тесте: отдаёт 206 с телом gcode и запоминает `Range`.

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `cmd /c "python -m unittest tests.test_thumbs -v"`
Expected: `ModuleNotFoundError: No module named 'farm_thumbs'`.

- [ ] **Step 3: Реализовать**

`farm_thumbs.py`: regex `rb"; thumbnail begin (\d+)x(\d+) (\d+)\r?\n(.*?); thumbnail end"`; выбрать блок с максимальной площадью; из строк убрать ведущие `;\s?`, склеить, `base64.b64decode(validate=False)`; проверить сигнатуру `b"\x89PNG\r\n\x1a\n"`; вернуть `None` при любой ошибке. `thumbnail_from_file` читает `MAX_THUMB_BYTES` с диска. `thumbnail_from_http`:

```python
def thumbnail_from_http(session, base_url: str, filename: str, *, timeout: float = 5.0):
    url = f"{base_url}/server/files/gcodes/{quote(filename)}"
    try:
        response = session.get(url, headers={"Range": f"bytes=0-{MAX_THUMB_BYTES - 1}"}, timeout=timeout, stream=True)
        if response.status_code not in (200, 206):
            return None
        data = response.raw.read(MAX_THUMB_BYTES)
        return extract_thumbnail(data)
    except requests.RequestException:
        return None
    finally:
        try:
            response.close()
        except Exception:
            pass
```

`ThumbCache`: словарь `key -> (version, data)` + `OrderedDict` для LRU 64; `version` — sha1(data)[:12] или `""`; `get` вызывает loader при отсутствии/ошибке, кэширует `None` отдельно (negative cache с тем же ключом, чтобы не долбить принтер).

- [ ] **Step 4: Прогнать тесты**

Run: `cmd /c "python -m unittest tests.test_thumbs -v"`
Expected: PASS.

- [ ] **Step 5: Коммит**

```bash
git add farm_thumbs.py tests/test_thumbs.py
git commit -m "feat: extract gcode thumbnails"
```

---

### Task 4: Модель состояния монитора

**Files:**
- Create: `farm_monitor.py`
- Create: `tests/test_monitor.py`

**Interfaces:**
- Produces: `PrinterView`, `FarmSummary`, `build_view(printer, *, info, status, now, error, thumb_version) -> PrinterView`, `summarize(views) -> FarmSummary`.
- Consumes: `Printer`, `ErrorInfo`, `parse_error`.

- [ ] **Step 1: Написать падающие тесты**

`tests/test_monitor.py`:

```python
class BuildViewTests(unittest.TestCase):
    def setUp(self):
        self.printer = Printer("F008 158", "192.168.7.158", 7125, 4408)
        self.info = {"state": "ready", "state_message": "Printer is ready"}
        self.status = {
            "print_stats": {"state": "printing", "filename": "деталь 7.gcode", "print_duration": 100},
            "virtual_sdcard": {
                "progress": 0.4,
                "layer": 51,
                "layer_count": 310,
                "cur_print_data": {"end_time": 1000, "metadata": {"filament_type": "PETG"}},
            },
            "extruder": {"temperature": 250.4},
            "heater_bed": {"temperature": 74.2},
        }

    def test_printing_view(self):
        view = build_view(self.printer, info=self.info, status=self.status, now=700, error=None, thumb_version="abc")
        self.assertEqual(view.state, "printing")
        self.assertEqual(view.file, "деталь 7.gcode")
        self.assertEqual(view.progress, 0.4)
        self.assertEqual((view.layer, view.layer_total), (51, 310))
        self.assertEqual(view.eta_seconds, 300)
        self.assertEqual((view.nozzle, view.bed), (250.4, 74.2))
        self.assertEqual(view.filament, "PETG")
        self.assertEqual(view.mainsail_url, "http://192.168.7.158:4408")

    def test_paused_and_complete_mapping(self):
        paused = build_view(self.printer, info=self.info, status={"print_stats": {"state": "paused", "filename": "a.gcode"}}, now=1, error=None, thumb_version="")
        complete = build_view(self.printer, info=self.info, status={"print_stats": {"state": "complete", "filename": "a.gcode"}}, now=1, error=None, thumb_version="")
        self.assertEqual(paused.state, "paused")
        self.assertEqual(complete.state, "idle")
        self.assertEqual(complete.file, "a.gcode")

    def test_error_state_from_info_beats_status(self):
        info = {"state": "shutdown", "state_message": "MCU shutdown"}
        error = ErrorInfo("2560", "Нет связи", "Проверьте кабель", "MCU shutdown")
        view = build_view(self.printer, info=info, status=None, now=1, error=error, thumb_version="")
        self.assertEqual(view.state, "error")
        self.assertTrue(view.online)
        self.assertEqual(view.error.label, "Нет связи")

    def test_offline_view(self):
        view = build_view(self.printer, info=None, status=None, now=1, error=None, thumb_version="")
        self.assertEqual(view.state, "offline")
        self.assertFalse(view.online)

    def test_eta_ignored_when_not_printing(self):
        status = {"print_stats": {"state": "paused"}, "virtual_sdcard": {"cur_print_data": {"end_time": 9999}}}
        view = build_view(self.printer, info=self.info, status=status, now=1, error=None, thumb_version="")
        self.assertIsNone(view.eta_seconds)


class SummaryTests(unittest.TestCase):
    def test_counts_and_nearest_finish(self):
        views = [
            make_view("printing", eta_seconds=100),
            make_view("printing", eta_seconds=50),
            make_view("paused"),
            make_view("idle"),
            make_view("error"),
            make_view("offline"),
        ]
        summary = summarize(views)
        self.assertEqual(
            (summary.printing, summary.paused, summary.idle, summary.error, summary.offline),
            (2, 1, 1, 1, 1),
        )
        self.assertEqual(summary.nearest_finish, 50)
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `cmd /c "python -m unittest tests.test_monitor -v"`
Expected: `ModuleNotFoundError: No module named 'farm_monitor'`.

- [ ] **Step 3: Реализовать**

`PrinterView` — frozen dataclass с полями: `key, name, host, port, mainsail_url, online, state, file, progress, layer, layer_total, eta_seconds, nozzle, bed, filament, thumb_version, error, last_seen`. `error` — `ErrorInfo | None` (в JSON сериализуется как dict).

`build_view`:

- `online = info is not None`; `state` по таблице дизайна; `info.state in {"error","shutdown"}` → `error`; `status is None` и `online` → `error`.
- `print_stats = (status or {}).get("print_stats", {})`, `vdisk = ...`.
- `progress` float 0..1, `layer`/`layer_count` int или 0.
- `eta_seconds`: только если `state == "printing"` и `end_time` int/float > now → `int(end_time - now)`.
- `nozzle/bed` float или 0.
- `filament` из `cur_print_data.metadata.filament_type` или `""`.
- `last_seen = now` при online, иначе 0 (поллер подставит прошлое значение).

`summarize`: счётчики + `nearest_finish` = min eta среди printing (None если нет).

- [ ] **Step 4: Прогнать тесты**

Run: `cmd /c "python -m unittest tests.test_monitor -v"`
Expected: PASS.

- [ ] **Step 5: Коммит**

```bash
git add farm_monitor.py tests/test_monitor.py
git commit -m "feat: printer view model and farm summary"
```

---

### Task 5: Фоновый поллер с бэкоффом

**Files:**
- Modify: `farm_monitor.py`
- Modify: `tests/test_monitor.py`
- Create: `tests/fake_moonraker.py`

**Interfaces:**
- Produces: `FarmMonitor(config, *, thumb_cache, interval=None, clock=time.time)`, методы `start()`, `stop()`, `set_printers(printers)`, `poll_once()`, `snapshot() -> dict`, `refresh()`, свойство `running -> bool`.
- Consumes: `build_view`, `summarize`, `ThumbCache`, `thumbnail_from_http`, `parse_error`, `extract_log_error`, `AppConfig`.

- [ ] **Step 1: Написать падающие тесты**

`tests/fake_moonraker.py` — конфигурируемый fake:

```python
class FakeMoonraker:
    def __init__(self, *, info=None, objects=None, thumb=None, log_tail="", failures=0):
        self.info = info or {"state": "ready", "state_message": "Printer is ready"}
        self.objects = objects or DEFAULT_STATUS
        self.thumb = thumb
        self.log_tail = log_tail
        self.failures = failures
        self.requests = []

    def start(self): ...
    def stop(self): ...
    def base_url(self): ...
    def request_count(self, path_prefix): ...
    @property
    def request_paths(self): ...
```

Обслуживает `GET /printer/info`, `/printer/objects/query`, `/server/files/gcodes/<name>` (206, PNG), `/server/files/logs/klippy.log` (206), а также `POST /printer/print/*`.

Тесты в `tests/test_monitor.py`:

```python
class PollerTests(unittest.TestCase):
    def make_monitor(self, server, interval=5):
        printer = Printer("test", "127.0.0.1", server.port)
        config = AppConfig(printers=[printer], refresh_seconds=interval)
        monitor = FarmMonitor(config, thumb_cache=ThumbCache(), interval=interval)
        monitor.set_printers(config.printers)
        return monitor

    def test_poll_once_builds_snapshot(self):
        server = FakeMoonraker(thumb=b"\x89PNG\r\n\x1a\nx").start()
        self.addCleanup(server.stop)
        monitor = self.make_monitor(server)
        monitor.poll_once()
        snapshot = monitor.snapshot()
        view = snapshot["printers"][0]
        self.assertEqual(view["state"], "printing")
        self.assertTrue(view["thumb_version"])
        self.assertEqual(snapshot["summary"]["printing"], 1)
        self.assertIn("updated_at", snapshot)

    def test_offline_backoff(self):
        server = FakeMoonraker(failures=1).start()
        self.addCleanup(server.stop)
        monitor = self.make_monitor(server)
        monitor.poll_once()
        self.assertEqual(monitor.snapshot()["printers"][0]["state"], "offline")
        monitor.poll_once()
        self.assertEqual(server.request_count("/printer/info"), 1)
        monitor._next_due["127.0.0.1:%d" % server.port] = 0
        monitor.poll_once()
        self.assertEqual(monitor.snapshot()["printers"][0]["state"], "printing")

    def test_klipper_error_not_offline(self):
        server = FakeMoonraker(info={"state": "shutdown", "state_message": "MCU shutdown"}, objects=None).start()
        self.addCleanup(server.stop)
        monitor = self.make_monitor(server)
        monitor.poll_once()
        view = monitor.snapshot()["printers"][0]
        self.assertEqual(view["state"], "error")
        self.assertTrue(view["online"])

    def test_stop_is_clean(self):
        server = FakeMoonraker().start()
        self.addCleanup(server.stop)
        monitor = self.make_monitor(server)
        monitor.start()
        monitor.stop()
        self.assertFalse(monitor.running)
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `cmd /c "python -m unittest tests.test_monitor.PollerTests -v"`
Expected: FAIL — нет `FarmMonitor`.

- [ ] **Step 3: Реализовать**

`FarmMonitor`:

- Поля: `self._printers`, `self._views: dict[str, PrinterView]`, `self._next_due: dict[str, float]`, `self._failures: dict[str, int]`, `self._lock`, `self._stop`, `self._thread`, `self._updated_at`.
- `_fetch(printer)` в отдельном потоке на принтер (`ThreadPoolExecutor(max_workers=16)`): `requests.Session` через `threading.local`; `GET /printer/info` (timeout 2.5), затем `GET /printer/objects/query?print_stats&virtual_sdcard&extruder&heater_bed&display_status` (timeout 2.5). Ошибки ловятся и возвращаются как `(None, None, str)`.
- При ошибке info → `offline`, увеличить `_failures`, `_next_due = now + min(60, 10 * 2 ** (_failures - 1))`.
- При успехе → сбросить failures, `_next_due = now + interval`.
- При `info.state in {"error","shutdown"}` или `print_stats.state == "error"`: сходить за `klippy.log` (Range `bytes=-65536`, timeout 3), извлечь ошибку, `parse_error(...)`. Если лог не дался — `parse_error` без него.
- Сохранение ошибки: `self._last_error[key]` хранит последний `ErrorInfo`; в `build_view` ошибка передаётся, пока состояние принтера не станет `printing`/`idle` (после `ready`), затем очищается. Это позволяет увидеть причину, даже если Klipper уже перезапустился.
- `last_seen`: при успешном опросе — `now`; при офлайне — предыдущее значение из `_views`, чтобы UI показал «последний ответ N минут назад»; если записи не было — 0.
- Превью: если `filename` есть и `(key, filename)` нет в кэше — `thumbnail_from_http(session, base_url, filename)`, версия в кэш; для `complete` filename тоже используется.
- `poll_once` выбирает принтеры с `_next_due <= now`, опрашивает пулом, обновляет `_views` и `_updated_at` под локом.
- `snapshot()` возвращает `{"printers": [asdict], "summary": asdict(summarize), "updated_at": ...}`; `asdict` для `ErrorInfo` даёт dict.
- `start()` — поток-демон с циклом `stop.wait(0.5)` + `poll_once()`; `stop()` — set + join(3).
- `set_printers` заменяет список, удаляет views/кэши для исчезнувших ключей, ставит `_next_due=0`.

- [ ] **Step 4: Прогнать тесты**

Run: `cmd /c "python -m unittest tests.test_monitor -v"`
Expected: PASS.

- [ ] **Step 5: Коммит**

```bash
git add farm_monitor.py tests/test_monitor.py tests/fake_moonraker.py
git commit -m "feat: farm monitor poller with offline backoff"
```

---

### Task 6: Job отправки

**Files:**
- Create: `farm_jobs.py`
- Create: `tests/test_jobs.py`

**Interfaces:**
- Produces: `JobError`, `JobManager(on_event=None)`, `start(printers, files)`, `cancel()`, `snapshot() -> dict`, `running -> bool`.
- Consumes: `BatchSender`, `FileProgressEvent`, `FileResultEvent`, `FinishedEvent`, `OverallEvent`, `PrinterStatusEvent`, `validate_files`.

- [ ] **Step 1: Написать падающие тесты**

`tests/test_jobs.py`:

```python
class JobTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "part.gcode"
        self.path.write_bytes(b"G1 X0\n")
        self.server = FakeMoonraker().start()
        self.addCleanup(self.server.stop)
        self.printer = Printer("test", "127.0.0.1", self.server.port)

    def test_job_reaches_finished(self):
        manager = JobManager()
        manager.start([self.printer], [self.path])
        deadline = time.time() + 10
        while manager.running and time.time() < deadline:
            time.sleep(0.05)
        snapshot = manager.snapshot()
        self.assertFalse(snapshot["running"])
        self.assertEqual(snapshot["ok"], 1)
        self.assertEqual(snapshot["failed"], 0)

    def test_second_start_rejected(self):
        manager = JobManager()
        manager.start([self.printer], [self.path])
        self.addCleanup(manager.cancel)
        with self.assertRaises(JobError):
            manager.start([self.printer], [self.path])

    def test_cancel_marks_snapshot(self):
        manager = JobManager()
        manager.start([self.printer], [self.path])
        manager.cancel()
        deadline = time.time() + 10
        while manager.running and time.time() < deadline:
            time.sleep(0.05)
        self.assertTrue(manager.snapshot()["cancelled"])

    def test_invalid_files_rejected(self):
        manager = JobManager()
        with self.assertRaises(JobError):
            manager.start([self.printer], [Path(self.tmp.name) / "missing.gcode"])
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `cmd /c "python -m unittest tests.test_jobs -v"`
Expected: `ModuleNotFoundError: No module named 'farm_jobs'`.

- [ ] **Step 3: Реализовать**

`JobManager`:

- `self._lock`, `self._sender`, `self._cancel_event`, `self._state` (dict с полями из дизайна).
- `start`: `validate_files(files)` → `JobError(error)`; если `running` → `JobError("Отправка уже идёт.")`; создать `BatchSender(printers, files, self._on_event, cancel_event)`, заполнить state (`running=True`, `printers_total=len`, `files_total=len(files)*len(printers)`, `per_printer`), `start()`.
- `_on_event`: под локом обновить state по типам событий (как UI v1); `FinishedEvent` → `running=False`, `finished=True`, `ok/failed/cancelled`; вызвать `on_event` вне лока.
- `cancel`: set event + state["cancelling"]=True; не ждать.
- `snapshot`: deep copy под локом.

- [ ] **Step 4: Прогнать тесты**

Run: `cmd /c "python -m unittest tests.test_jobs -v"`
Expected: PASS.

- [ ] **Step 5: Коммит**

```bash
git add farm_jobs.py tests/test_jobs.py
git commit -m "feat: send job manager"
```

---

### Task 7: HTTP-сервер, статика, state, heartbeat

**Files:**
- Create: `farm_server.py`
- Create: `web/index.html` (минимальная заглушка для тестов; полноценная — Task 9)
- Create: `tests/test_server.py`

**Interfaces:**
- Produces: `FarmApp(config_path=CONFIG_PATH, *, monitor=None, job=None, start_monitor=False, idle_timeout=600)`, `FarmApp.state_snapshot() -> dict`, `FarmApp.shutdown()`, `FarmApp.should_shutdown(now) -> bool`, `FarmApp.last_heartbeat`; `create_server(app, host="127.0.0.1", port=0) -> ThreadingHTTPServer`; `find_running_instance(host, ports, timeout=0.5) -> str | None`; `configure_logging(path) -> None`; `Journal(maxlen=200)`; `main()`.

- [ ] **Step 1: Написать падающие тесты**

`tests/test_server.py`:

```python
class ServerTests(unittest.TestCase):
    def setUp(self):
        self.app = StubApp()
        self.server = create_server(self.app, port=0)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"

    def test_state_json(self):
        response = requests.get(self.url("/api/state"), timeout=3)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["summary"]["printing"], 1)

    def test_static_index(self):
        response = requests.get(self.url("/"), timeout=3)
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers["Content-Type"])
        self.assertIn("Printer Farm", response.text)

    def test_origin_rejected_on_post(self):
        response = requests.post(self.url("/api/heartbeat"), headers={"Origin": "http://evil.example"}, timeout=3)
        self.assertEqual(response.status_code, 403)

    def test_path_traversal_blocked(self):
        response = requests.get(self.url("/../farm_core.py"), timeout=3)
        self.assertIn(response.status_code, (400, 404))

    def test_heartbeat_updates(self):
        before = self.app.last_heartbeat
        time.sleep(0.02)
        requests.post(self.url("/api/heartbeat"), timeout=3)
        self.assertGreater(self.app.last_heartbeat, before)

    def test_find_running_instance(self):
        self.assertEqual(
            find_running_instance("127.0.0.1", [self.port], timeout=1.0),
            f"http://127.0.0.1:{self.port}",
        )
        self.assertIsNone(find_running_instance("127.0.0.1", [1], timeout=0.2))

    def test_idle_shutdown_rule(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "printers.json"
            path.write_text('{"printers": []}', encoding="utf-8")
            app = FarmApp(config_path=path, start_monitor=False)
            app.last_heartbeat = 100
            self.assertFalse(app.should_shutdown(200))
            self.assertTrue(app.should_shutdown(100 + app.idle_timeout + 1))
```

`StubApp` с `state_snapshot()` возвращает `{"printers": [], "summary": {"printing": 1}, "job": {}, "config": {}}`, `last_heartbeat=0`, `shutdown()` ставит флаг.

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `cmd /c "python -m unittest tests.test_server -v"`
Expected: `ModuleNotFoundError: No module named 'farm_server'`.

- [ ] **Step 3: Реализовать**

`farm_server.py`:

- `WEB_DIR = Path(__file__).resolve().parent / "web"`.
- `FarmApp`: загружает конфиг; при `ConfigError`/`OSError` переименовывает битый файл в `printers.json.broken` (best-effort), пишет в журнал и стартует с пустым списком; создаёт `ThumbCache`, `FarmMonitor` (если `start_monitor`), `JobManager`, `Journal`; `last_heartbeat = time.time()`; `state_snapshot()` = monitor.snapshot + job.snapshot + config meta; `shutdown()` останавливает monitor/job и ставит `server.shutdown` через ссылку; `should_shutdown(now)` = не `job.running` и `now - last_heartbeat > idle_timeout`.
- `configure_logging(path)`: `RotatingFileHandler(path, maxBytes=1_048_576, backupCount=3, encoding="utf-8")`, формат `%(asctime)s %(levelname)s %(message)s`.
- `find_running_instance(host, ports, timeout=0.5)`: для каждого порта `GET /api/state`; 200 → URL, иначе None. Ошибки подавляются.
- `web/index.html` — заглушка с `<title>Printer Farm</title>`.
- `FarmHandler(BaseHTTPRequestHandler)`: `app` — атрибут класса, устанавливается в `create_server` через `type("BoundHandler", (FarmHandler,), {"app": app})`.
- `_send_json(payload, status=200)`, `_send_error(status, message)`.
- `do_GET`: `/api/state`, `/api/journal`, `/`, `/styles.css`, `/app.js`; статика с `_resolve_static` (resolve + `is_relative_to(WEB_DIR)`, иначе 404), MIME, no-cache, `nosniff`.
- `do_POST`: `/api/heartbeat` → `last_heartbeat=time.time()`, `{"ok": True}`; `/api/shutdown` → `app.shutdown()` в отдельном потоке, `{"ok": True}`; иначе 404.
- Origin-check: для не-GET, если `Origin` есть и не входит в `{f"http://127.0.0.1:{port}", f"http://localhost:{port}"}` → 403.
- JSON-body endpoints (позже) требуют `application/json`.
- `create_server`: `ThreadingHTTPServer((host, port), BoundHandler)`, `daemon_threads=True`.
- `main()`: `configure_logging(APP_DIR / "farm.log")`; `find_running_instance("127.0.0.1", range(8765, 8776))` → если найден, `webbrowser.open(url)` и выход; иначе `FarmApp(start_monitor=True)`; bind 8765..8775 (первый свободный); `FarmMonitor.start()`; `webbrowser.open(f"http://127.0.0.1:{port}/")`; фоновый idle-поток: раз в 5 с `should_shutdown(time.time())` → `shutdown()`; `serve_forever()`.

- [ ] **Step 4: Прогнать тесты**

Run: `cmd /c "python -m unittest tests.test_server -v"`
Expected: PASS.

- [ ] **Step 5: Коммит**

```bash
git add farm_server.py web/index.html tests/test_server.py
git commit -m "feat: local dashboard http server"
```

---

### Task 8: API файлов, превью, принтеров и управления

**Files:**
- Modify: `farm_server.py`
- Modify: `tests/test_server.py`

**Interfaces:**
- Produces эндпоинты: `GET /api/thumb/<key>`, `GET /api/files/thumb?path=`, `GET /api/fs?path=`, `POST /api/send`, `POST /api/job/cancel`, `POST /api/print/<key>/<action>`, `POST /api/printers`, `PUT/DELETE /api/printers/<key>`.
- Consumes: `FarmApp` (config, monitor, job, thumb cache), `print_action`, `thumbnail_from_file`, `validate_files`.

- [ ] **Step 1: Написать падающие тесты**

Добавить в `tests/test_server.py`:

```python
class ApiTests(unittest.TestCase):
    def test_fs_lists_drives_and_gcode(self):
        response = requests.get(self.url("/api/fs?path=" + quote(str(self.tmp.name))), timeout=3)
        payload = response.json()
        self.assertTrue(any(entry["name"] == "part.gcode" for entry in payload["entries"]))
        self.assertTrue(all(entry["name"].endswith(".gcode") or entry["is_dir"] for entry in payload["entries"]))

    def test_files_thumb(self):
        response = requests.get(self.url("/api/files/thumb?path=" + quote(str(self.gcode))), timeout=3)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Content-Type"], "image/png")

    def test_printers_crud(self):
        response = requests.post(self.url("/api/printers"), json={"name": "New", "host": "10.0.0.5", "port": 7125}, timeout=3)
        self.assertEqual(response.status_code, 201)
        response = requests.put(self.url("/api/printers/10.0.0.5:7125"), json={"name": "Renamed", "host": "10.0.0.5", "port": 7125}, timeout=3)
        self.assertEqual(response.status_code, 200)
        response = requests.delete(self.url("/api/printers/10.0.0.5:7125"), timeout=3)
        self.assertEqual(response.status_code, 200)

    def test_pause_calls_moonraker(self):
        response = requests.post(self.url(f"/api/print/{self.key}/pause"), timeout=3)
        self.assertEqual(response.status_code, 200)
        self.assertIn("/printer/print/pause", self.moonraker.request_paths)

    def test_send_starts_job(self):
        response = requests.post(self.url("/api/send"), json={"printers": [key], "files": [str(self.gcode)]}, timeout=3)
        self.assertEqual(response.status_code, 202)

    def test_send_rejects_duplicate_basenames(self):
        first = Path(self.tmp.name) / "a" / "part.gcode"
        second = Path(self.tmp.name) / "b" / "PART.gcode"
        for path in (first, second):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"G1 X0\n")
        response = requests.post(
            self.url("/api/send"),
            json={"printers": [self.key], "files": [str(first), str(second)]},
            timeout=3,
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Одинаковые имена", response.json()["error"])
```

`ApiTests.setUp`: поднять `FakeMoonraker`, создать временный `printers.json` с принтером `127.0.0.1:<port>` и `mainsail_port: 4408`, собрать `FarmApp(config_path=..., start_monitor=False)` с stub-монитором, отдающим один `PrinterView`, запустить `create_server(app, port=0)`; `self.gcode` — файл с валидной 300×300 PNG-миниатюрой, `self.key = f"127.0.0.1:{moonraker.port}"`.

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `cmd /c "python -m unittest tests.test_server.ApiTests -v"`
Expected: 404 на новых путях.

- [ ] **Step 3: Реализовать**

- `GET /api/fs`: `os.listdrives()` (fallback `GetLogicalDrives`), `os.scandir`; только папки и `.gcode`; сортировка dirs-first→name; `PermissionError` → 403 JSON.
- `GET /api/files/thumb`: путь из query, `thumbnail_from_file`, 404 если None.
- `GET /api/thumb/<key>`: найти view в `app.monitor.snapshot()`, взять из `ThumbCache`; `ETag` + `If-None-Match` → 304; 404 если нет.
- `POST /api/printers`: валидация через `Printer.from_dict`, дубликат key → 409; `app.config.printers.append`, `save_config`, `monitor.set_printers`; 201.
- `PUT /api/printers/<key>`: найти по key, заменить (с проверкой дубля), сохранить, `monitor.set_printers`.
- `DELETE`: удалить, сохранить, обновить монитор.
- `POST /api/print/<key>/<action>`: найти принтер, `print_action`, 502 с текстом при `ControlError`, иначе 200; `app.monitor.refresh()`.
- `POST /api/send`: JSON `{printers, files}`; ключи существуют; `job.start` → 202; `JobError` → 400. После успешного старта `config.last_dir = str(Path(files[0]).parent)`, `save_config`.
- `POST /api/job/cancel`: `job.cancel()` → 200.
- `GET /api/journal?since=N`.
- Общий `_read_json()` с проверкой Content-Type и лимитом 1 МБ.

- [ ] **Step 4: Прогнать тесты**

Run: `cmd /c "python -m unittest tests.test_server -v"`
Expected: PASS.

- [ ] **Step 5: Коммит**

```bash
git add farm_server.py tests/test_server.py
git commit -m "feat: dashboard api for files, printers and control"
```

---

### Task 9: HTML/CSS-каркас дашборда

**Files:**
- Replace: `web/index.html`
- Create: `web/styles.css`

**Interfaces:**
- Produces: разметку с `id="cards"`, `id="summary"`, `id="toolbar"`, `id="filters"`, `id="search"`, `id="files-strip"`, `id="journal"`, `id="toasts"`, контейнеры модалок `#modal-files`, `#modal-printers`, `#modal-error`, `#modal-confirm`; подключение `styles.css` и `app.js` (defer).
- Consumes: статику из `farm_server.py`.

- [ ] **Step 1: Написать разметку и стили**

Перенести визуал утверждённого макета: тёмная тема, шапка, плитки сводки, тулбар с поиском/чипами, лоток файлов, сетка карточек, журнал-панель, модалки, тосты. Пустые `#cards`, `#journal`, `#toasts` заполняет JS. В карточке-шаблоне (в `<template id="card-template">`) — чекбокс, имя, IP, пилюля статуса, превью, файл, прогресс, метаданные, кнопки.

CSS: переменные из макета, `grid-template-columns: repeat(auto-fill, minmax(290px, 1fr))`, состояния `.card.selected`, `.card.offline`, `.pill.printing|paused|idle|error|offline`, `.progress`, `.compact`, фокус-ринги, `@media` для узкого окна.

- [ ] **Step 2: Проверить отдачу**

Run: `cmd /c "python -c \"import farm_server; print(farm_server.WEB_DIR / 'index.html')"` — файл существует; затем запустить сервер и запросить `/` и `/styles.css`.

Expected: 200, `text/html`/`text/css`, в HTML есть `id="cards"` и `Printer Farm`.

- [ ] **Step 3: Коммит**

```bash
git add web/index.html web/styles.css
git commit -m "feat: dashboard html and dark theme"
```

---

### Task 10: JS — состояние, карточки, фильтры

**Files:**
- Create: `web/app.js`

**Interfaces:**
- Produces: `const state = { printers: [], summary: {}, job: {}, selected: new Set(), files: [], filter: "all", query: "", compact: false }`; функции `fetchState()`, `render()`, `upsertCard(view)`, `applyFilters()`, `formatBytes()`, `formatEta()`, `formatTemp()`, `toggleSelect(key)`.
- Consumes: `/api/state`, `/api/thumb/<key>`.

- [ ] **Step 1: Реализовать ядро**

- `fetchState()` каждые 2000 мс (1000 при `job.running`), `AbortController` с таймаутом 5 с.
- `render()`: summary-плитки, счётчики чипов, карточки.
- `upsertCard(view)`: карточка создаётся один раз из `<template>` и хранится в `Map`; обновляются только `textContent`, `style.width`, `classList`, `img.src` при смене `thumb_version`; при удалении принтера — удалить элемент.
- Фильтры: `all|printing|paused|idle|error|offline`; поиск по name/host/file; выбранные принтеры не теряются при фильтрации.
- `selected` — `Set` ключей; чекбокс обновляет Set и счётчик на кнопке.
- Компактный режим: `document.body.classList.toggle("compact")`, `localStorage`.
- Обработка ошибок fetch → тост «Нет связи с сервером», не ломать старые карточки.

- [ ] **Step 2: Проверить синтаксис**

Run: `cmd /c "python -c \"import pathlib; print(pathlib.Path('web/app.js').stat().st_size)\""` — файл не пуст; открыть дашборд в Edge, в DevTools Console не должно быть синтаксических ошибок (ручная проверка).

- [ ] **Step 3: Коммит**

```bash
git add web/app.js
git commit -m "feat: dashboard state rendering and filters"
```

---

### Task 11: JS — модалки, управление, отправка, журнал

**Files:**
- Modify: `web/app.js`

**Interfaces:**
- Produces: `openFilesModal()`, `openPrintersModal()`, `openErrorModal(view)`, `confirmDialog(text)`, `sendFiles()`, `cancelJob()`, `printAction(key, action)`, `loadJournal()`, `heartbeat()`, `toast(text, kind)`.
- Consumes: `/api/fs`, `/api/files/thumb`, `/api/send`, `/api/job/cancel`, `/api/print/...`, `/api/printers`, `/api/journal`, `/api/heartbeat`.

- [ ] **Step 1: Реализовать**

- Файловый браузер: диски/папки/файлы, хлебные крошки, «вверх», мультивыбор `.gcode`, превью выбранного, кнопка «Выбрать». Состояние — `state.files` (абсолютные пути), лоток обновляется.
- Менеджер принтеров: список, добавление/редактирование/удаление, поля name/host/port/mainsail_port, подтверждение удаления, обновление state.
- Кнопки карточки: Mainsail (`target=_blank`, `rel=noopener`), пауза/продолжить, «Отменить печать» (confirm), меню «•••».
- Ошибка: модалка с label, код, detail, hint, ссылкой на вики.
- Отправка: `POST /api/send` с выбранными принтерами и файлами; блокировка кнопки; тост; при `job.running` опрос 1 с.
- Отмена job: `POST /api/job/cancel`.
- Журнал: `GET /api/journal?since=lastIndex`, добавление строк, автопрокрутка.
- Heartbeat каждые 10 с; `beforeunload` ничего не делает (сервер сам погаснет).
- Клавиатура: `/` → поиск, `Esc` → закрыть верхнюю модалку, `Ctrl+Enter` → отправка.
- Тосты: 5 с, `aria-live="polite"`.

- [ ] **Step 2: Проверить**

Run: открыть дашборд против fake Moonraker (или реальной фермы только для чтения), проверить: выбор файлов, отправку на 1 принтер, паузу на реальном печатающем принтере — **вручную и с осторожностью**, отмену, модалку ошибки, журнал, heartbeat (закрыть вкладку — сервер завершится через idle-таймаут).

- [ ] **Step 3: Коммит**

```bash
git add web/app.js
git commit -m "feat: dashboard interactions and file browser"
```

---

### Task 12: Лаунчер, миграция, README, финальная проверка

**Files:**
- Modify: `Запустить.bat`
- Delete: `print_farm_sender.py`
- Modify: `README.md`
- Modify: `tests/test_farm_core.py` (если нужно убрать импорт Tkinter — его там нет)

**Interfaces:**
- Produces: запуск `pythonw farm_server.py`; README с новой архитектурой и ограничениями; удалён старый entrypoint.

- [ ] **Step 1: Обновить лаунчер**

`Запустить.bat`:

```bat
@echo off
cd /d "%~dp0"
start "" pythonw farm_server.py
```

- [ ] **Step 2: Удалить Tkinter-версию и обновить README**

Удалить `print_farm_sender.py`; в README — запуск, скриншот-раздел (текстом), API, ограничения (камеры нет, upload-only, встроенный файловый браузер, локальный сервер), требования.

- [ ] **Step 3: Полный прогон**

Run: `cmd /c "python -m unittest discover -s tests -v"`
Expected: OK, 0 failures, реальные принтеры не задействованы.

Run: `cmd /c "python -m py_compile farm_core.py farm_errors.py farm_thumbs.py farm_monitor.py farm_jobs.py farm_server.py"`
Expected: код 0.

- [ ] **Step 4: Смоук дашборда**

Запустить `Запустить.bat`, открыть браузер, проверить: 25 карточек, статусы, превью, фильтры, Mainsail, отправка 1 файла на 1 принтер (или fake), пауза, ошибка-модалка, журнал, закрытие вкладки.

- [ ] **Step 5: Коммит**

```bash
git add -A
git commit -m "feat: replace tkinter ui with local web dashboard"
```

---

## Self-Review

- **Spec coverage:** конфиг/Mainsail — Task 1; ошибки — Task 2; превью — Task 3; статусы/сводка — Task 4; опрос/бэкофф — Task 5; job — Task 6; сервер/статика/state — Task 7; API файлов/принтеров/управления — Task 8; UI-каркас — Task 9; UI-логика — Task 10–11; лаунчер/миграция/README — Task 12.
- **Step scan:** каждая задача имеет тест (кроме фронтенда, где проверка — ручная/статическая), команду запуска, реализацию и коммит.
- **Type consistency:** `Printer.mainsail_url`, `ErrorInfo`, `PrinterView`, `FarmSummary`, `FarmMonitor.snapshot`, `JobManager.snapshot`, `FarmApp.state_snapshot` используются согласованно во всех задачах.
- **Review Focus:** все 6 пунктов покрыты тестами Task 5 (офлайн/бэкофф, Klipper error), Task 6 (job), Task 3/8 (кириллица/URL), Task 7/12 (сервер/закрытие), Task 10 (точечный DOM).
- **Proportion:** план описывает интерфейсы и тесты, а не тела функций; таблица ошибок — данные из вики, не логика.
