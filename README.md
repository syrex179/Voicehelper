# JARVIS — персональный голосовой помощник Windows

JARVIS — расширяемый Windows-ассистент: его стабильное ядро маршрутизирует команды к отдельным плагинам, а пользовательские сценарии сохраняются как Skills (режимы) и переживают перезапуск.

## Требования и запуск

Поддерживается Python 3.8+ на Windows. Создайте виртуальное окружение и установите опциональные интеграции:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python main.py
```

После установки requirements доступны STT с микрофона (`SpeechRecognition`/`PyAudio`), Windows SAPI TTS (`pywin32`), системная громкость (`pycaw`), скриншоты (`Pillow`) и системный трей (`pystray`). Все пакеты проверены в Python 3.8.10 x64. Нажмите «Слушать»: JARVIS слушает фразы с wake word «Джарвис» и передаёт распознанный текст в router. Выбор языка и wake word сохраняются в Settings.

## Архитектура

`core/` содержит Assistant, Router, Registry, Plugin Manager, EventBus, Permissions, Memory, Learning, Skills и конечный автомат voice-dialog. `plugins/` добавляет возможности; они не вшиты в router. `speech/` содержит STT, wake-word и TTS; `ui/` — настоящее окно Tkinter, менеджеры Skills/Plugins/Settings, mode cards и tray. Данные находятся в `data/` и создаются при первом запуске.

Поток: микрофон → STT → Wake Word → Intent Parser → Router → Registry → Plugin → UI/TTS.

## Команды

- «Открой Telegram», «Запусти калькулятор», «Открой браузер».
- «Открой YouTube», «Найди на YouTube котиков».
- «Громкость 50», «Громче», «Выключи звук».
- «Пауза», «Следующий трек», «Покажи рабочий стол», «Открой настройки».
- «Сделай скриншот», «Открой загрузки».

Выключение, перезапуск и блокировка требуют подтверждения в UI. Код не выполняет произвольные голосовые shell-команды.

## Skills и режимы

В окне выберите «Создать режим», затем опишите сценарий, например: «Когда я говорю стрим, открой OBS, Discord и Telegram, потом поставь громкость 60». JARVIS создаст сохраняемый режим с подходящей иконкой; его можно запускать кнопкой или фразой-триггером.

Голосовой вариант: «Джарвис, создай новый режим» → название → действия → «Да». Для изменения используйте «измени режим Стрим», для удаления — «забудь режим Стрим». «Я называю Telegram телегой» создаёт сохраняемый alias после подтверждения. Skills manager умеет переименовать, изменить иконку, дублировать, экспортировать в JSON, импортировать и удалить skill.

### Обучение демонстрацией и custom commands

«Джарвис, начни обучение» запускает **безопасный recorder**: он записывает только успешные структурированные действия, прошедшие через Router (открытие приложений/папок/браузера/YouTube, громкость, media keys, screenshot и show desktop). Он намеренно не записывает нажатия клавиатуры, мышь, пароли или shell-команды. Завершите «Джарвис, закончи обучение», назовите режим, выберите emoji и подтвердите сохранение. «Отмени обучение» полностью очищает черновик.

Custom command связывает фразу с существующим режимом: «создай команду "начать стрим" для режима Стрим» → подтверждение. Команда сохраняется в `data/custom_commands.json`; повторное создание той же фразы явно предлагает заменить mapping.

## Local natural-language parser

JARVIS works without an API key. Its local parser normalizes an optional “Джарвис/Jarvis” prefix and supports common open/run variants, volume percentages, screenshots, aliases, modes, custom commands, and multi-action phrases such as «открой OBS, Discord и Telegram». Parser output is structured as an intent, parameters/entities, and (for multi-action phrases) a list of registered command actions. It never evaluates user text as code or shell input.

Skills may be drafted in one phrase: «создай режим Стрим: открой OBS и Discord, поставь громкость 60». The draft remains out of persistent Memory until confirmation. A short custom-command flow also works: «создай команду начать стрим» → «запускать режим Стрим» → confirmation. Alias replacement is explicit when a phrase already maps to another target.

While a Skill draft is active, JARVIS accumulates supported actions across replies. Say «добавь …», «покажи действия», then «сохрани» for a preview and confirmation; «отмена» discards the draft. Existing Skills use the same copy-on-edit flow, so an unconfirmed edit never changes the saved Mode.

Draft editing supports safe action removal, clear, move to beginning/end, move before/after another action, and swapping numbered actions. Russian number words are parsed locally for volume (for example, «семьдесят пять» → 75); values outside 0–100 are rejected. These are intentionally narrow command grammars, not a claim of general natural-language understanding.

### Поиск файлов

«Найди файл report», «найди все pdf» запускают безопасный рекурсивный поиск только по разрешённым папкам (по умолчанию Documents, Downloads, Desktop), без сканирования всего диска. Окно результатов показывает путь, тип, размер и дату; из него можно открыть файл или его папку. Параметры находятся в `settings.json → plugins → files` (`roots`, `max_results`).

### Autostart

В Settings доступен **Start JARVIS with Windows**. Он создаёт/удаляет запись только в `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`, без прав администратора. Изменение применяется при сохранении Settings.

Закрытие основного окна скрывает его в трее; выбирайте **Exit** в меню JARVIS tray, чтобы завершить программу. Если значок не появился, убедитесь, что `pystray` и `Pillow` установлены и Windows отображает значки области уведомлений.

## Плагины

Каждая папка в `plugins/` имеет `manifest.json` и `plugin.py`. Plugin Manager автоматически изолирует ошибку загрузки одного плагина от остальных. Для нового плагина создайте:

```text
plugins/obs/
├── manifest.json
└── plugin.py
```

`plugin.py` экспортирует класс `Plugin(AssistantPlugin)`, реализующий `get_commands()` и `execute(command)`. Плагин не должен выполнять непроверенный текст пользователя как команду оболочки. В окне Plugins доступны реальные Enable/Disable/Reload: отключённый plugin удаляет свои intents из registry.

## Проверка

```powershell
python -m compileall -q .
python -m unittest discover -s tests -v
```

Тесты используют стандартный `unittest`, поэтому отдельный test runner не нужен.

## Текущие ограничения

Нет raw-записи мыши/клавиатуры — это сознательное ограничение безопасности. Настройки Files и выбранный microphone сохраняются; специализированные формы настройки для остальных plugins ещё не реализованы. Для реального STT нужен доступ Windows к выбранному микрофону и интернет для Google Speech Recognition.

## Voice subsystem

JARVIS uses `SpeechRecognition` with Google Speech for STT and Windows SAPI (`pywin32`) for TTS. The listener runs in one background thread: **LISTENING → PROCESSING → SPEAKING → READY**. The main window never waits for microphone or speech synthesis. Settings now includes a persisted microphone choice, a short microphone stream test, speech language, wake word, TTS enable/rate/volume, and a voice test. The Stop voice button clears queued output and interrupts SAPI when possible.

### Automated tested

The unit suite mocks hardware and verifies microphone selection persistence, unavailable-device handling, wake-word-gated STT → controller → assistant → TTS routing, safe listener shutdown, and all existing core/plugin/skill tests.

### Requires physical Windows check

Before relying on voice daily, manually select and test the intended microphone, press **Test Voice**, confirm the tray UI stays responsive during listening/speaking, and test actual Russian/Ukrainian recognition. Google Speech requires network access; an unavailable or busy microphone is handled as an error without terminating JARVIS.
