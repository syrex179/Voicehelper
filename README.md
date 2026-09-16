# 🤖 JARVIS — Персональный голосовой помощник Windows

<p align="center">
  <img src="https://img.shields.io/badge/Platform-Windows-0078D4?style=for-the-badge&logo=windows&logoColor=white" alt="Windows">
  <img src="https://img.shields.io/badge/Python-3.8%2B-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/Architecture-Modular-8A2BE2?style=for-the-badge" alt="Modular Architecture">
  <img src="https://img.shields.io/badge/Status-In%20Development-orange?style=for-the-badge" alt="Status">
</p>

<p align="center">
  <strong>Твой персональный помощник для управления Windows.</strong>
</p>

<p align="center">
  Голосовое управление • Автоматизация • Плагины • Пользовательские сценарии
</p>

<p align="center">
  <a href="#-русская-версия">🇷🇺 Русская версия</a> •
  <a href="#-english-version">🇬🇧 English version</a>
</p>

---

# 🇷🇺 Русская версия

## 📖 О проекте

**JARVIS** — расширяемый голосовой помощник для Windows, предназначенный для управления приложениями, автоматизации повседневных задач и создания персонализированных рабочих сценариев.

В основе проекта находится модульное ядро, которое обрабатывает команды и передаёт их соответствующим плагинам.

Пользователь может создавать собственные **Skills (режимы)** — сохранённые сценарии, объединяющие несколько действий. Они доступны даже после перезапуска приложения.

### 🎯 Основные цели

- Упростить взаимодействие с Windows.
- Управлять приложениями с помощью голоса.
- Автоматизировать повторяющиеся действия.
- Создавать персональные рабочие сценарии.
- Обеспечить расширяемую архитектуру.
- Сохранить контролируемое выполнение команд.

---

## ✨ Возможности

### 🎙️ Голосовое управление

JARVIS позволяет взаимодействовать с компьютером с помощью голосовых команд.

- Распознавание речи.
- Поддержка ключевого слова «Джарвис».
- Синтез речи через Windows SAPI.
- Выбор языка распознавания.
- Настройка ключевого слова.
- Настройка скорости и громкости речи.
- Выбор микрофона.
- Тестирование микрофона.
- Фоновая обработка голосовых команд.
- Управление состояниями голосового диалога.

### Состояния голосового модуля

```text
LISTENING
    ↓
PROCESSING
    ↓
SPEAKING
    ↓
READY
```

Основное окно приложения не блокируется во время прослушивания и озвучивания ответов.

---

### 🧩 Модульная система плагинов

JARVIS построен на основе расширяемой архитектуры плагинов.

Каждый плагин отвечает за определённый набор функций и может управляться независимо.

#### Возможности Plugin Manager

- Автоматическое обнаружение плагинов.
- Регистрация команд.
- Включение и отключение плагинов.
- Перезагрузка плагинов.
- Изоляция ошибок загрузки.
- Независимое добавление новых возможностей.

Плагины не встроены непосредственно в основной маршрутизатор команд.

---

### 🧠 Skills — пользовательские режимы

Создавайте собственные сценарии, объединяющие несколько действий.

Пример:

> «Джарвис, создай режим Стрим: открой OBS, Discord и Telegram, поставь громкость 60».

JARVIS создаст сохраняемый режим, который можно запускать через интерфейс или голосовую команду.

#### Возможности менеджера режимов

- Создание режимов.
- Редактирование.
- Переименование.
- Изменение иконки.
- Дублирование.
- Удаление.
- Экспорт в JSON.
- Импорт из JSON.
- Запуск голосовой командой.
- Запуск кнопкой в интерфейсе.

Сохранённые режимы доступны после перезапуска приложения.

---

### 🎓 Обучение демонстрацией

JARVIS включает безопасный recorder для создания пользовательских сценариев на основе успешно выполненных действий.

Пример:

```text
Джарвис, начни обучение

→ Открой OBS
→ Открой Discord
→ Установи громкость 60

Джарвис, закончи обучение
```

После завершения обучения пользователь может сохранить последовательность действий как новый режим.

#### Ограничения recorder

Записываются только поддерживаемые структурированные действия, успешно прошедшие через Router.

Recorder не записывает:

- Нажатия клавиатуры.
- Движения мыши.
- Пароли.
- Произвольные shell-команды.
- Непроверенный пользовательский ввод.

---

### 🗣️ Локальный обработчик команд

JARVIS использует локальный парсер команд, который работает **без API-ключа**.

Поддерживаются:

- Команды с обращением «Джарвис» или «Jarvis».
- Запуск приложений.
- Открытие папок и сайтов.
- Управление громкостью.
- Скриншоты.
- Псевдонимы приложений.
- Режимы и Skills.
- Пользовательские команды.
- Комбинированные фразы.
- Числительные на русском языке для управления громкостью.

Пример:

```text
Открой OBS, Discord и Telegram
```

Парсер преобразует распознанную фразу в структурированную команду.

Пользовательский текст не выполняется напрямую как код или shell-команда.

> Парсер использует ограниченную систему команд и не является универсальной языковой моделью.

---

### 🔗 Пользовательские команды и псевдонимы

Создавайте собственные голосовые сокращения.

Пример:

```text
Я называю Telegram телегой
```

После подтверждения JARVIS сохранит псевдоним.

Можно также связать собственную фразу с существующим режимом:

```text
Создай команду «начать стрим»
для режима «Стрим»
```

Пользовательские команды сохраняются в:

```text
data/custom_commands.json
```

При конфликте существующих команд JARVIS запрашивает подтверждение перед заменой.

---

### 📁 Поиск файлов

JARVIS умеет искать файлы в разрешённых папках.

Примеры:

```text
Найди файл report
```

```text
Найди все pdf
```

#### Возможности

- Рекурсивный поиск.
- Поиск по имени.
- Поиск по расширению.
- Отображение пути.
- Отображение типа файла.
- Отображение размера.
- Отображение даты изменения.
- Открытие файла.
- Открытие папки с файлом.

По умолчанию поиск ограничен папками:

- Документы.
- Загрузки.
- Рабочий стол.

Полный диск не сканируется.

Настройки поиска находятся в конфигурации `settings.json`.

---

### 🖥️ Интеграция с Windows

JARVIS поддерживает выполнение различных системных действий.

- Запуск приложений.
- Открытие папок.
- Открытие сайтов.
- Управление громкостью.
- Отключение и включение звука.
- Управление воспроизведением медиа.
- Создание скриншотов.
- Отображение рабочего стола.
- Открытие папки «Загрузки».
- Открытие настроек.
- Автозапуск вместе с Windows.
- Сворачивание в системный трей.

#### Примеры команд

| Действие | Пример |
|---|---|
| Запуск приложения | «Открой Telegram» |
| Открытие сайта | «Открой YouTube» |
| Управление громкостью | «Громкость 50» |
| Управление медиа | «Следующий трек» |
| Скриншот | «Сделай скриншот» |
| Рабочий стол | «Покажи рабочий стол» |
| Открытие папки | «Открой загрузки» |

Выключение, перезапуск и блокировка компьютера требуют подтверждения в интерфейсе.

---

### ⚙️ Автозапуск

В настройках JARVIS можно включить автоматический запуск вместе с Windows.

Для этого используется раздел реестра:

```text
HKCU\Software\Microsoft\Windows\CurrentVersion\Run
```

Права администратора не требуются.

При закрытии главного окна приложение скрывается в системном трее.

Для полного завершения работы используйте пункт **Exit** в меню трея.

---

## 🏗️ Архитектура проекта

JARVIS разделён на несколько независимых компонентов.

### Схема обработки команд

```text
┌───────────────────────────┐
│        МИКРОФОН           │
└─────────────┬─────────────┘
              │
              ▼
┌───────────────────────────┐
│     РАСПОЗНАВАНИЕ РЕЧИ     │
│           STT             │
└─────────────┬─────────────┘
              │
              ▼
┌───────────────────────────┐
│         WAKE WORD         │
└─────────────┬─────────────┘
              │
              ▼
┌───────────────────────────┐
│       INTENT PARSER       │
└─────────────┬─────────────┘
              │
              ▼
┌───────────────────────────┐
│          ROUTER           │
└─────────────┬─────────────┘
              │
              ▼
┌───────────────────────────┐
│         REGISTRY          │
└─────────────┬─────────────┘
              │
              ▼
┌───────────────────────────┐
│          PLUGIN           │
└─────────────┬─────────────┘
              │
              ▼
┌───────────────────────────┐
│         UI / TTS          │
└───────────────────────────┘
```

---

## 📂 Структура проекта

```text
Voicehelper/
│
├── core/
│   ├── Assistant
│   ├── Router
│   ├── Registry
│   ├── Plugin Manager
│   ├── EventBus
│   ├── Permissions
│   ├── Memory
│   ├── Learning
│   ├── Skills
│   └── Voice Dialog
│
├── plugins/
│   └── Плагины приложения
│
├── speech/
│   ├── Speech-to-Text
│   ├── Wake Word
│   └── Text-to-Speech
│
├── ui/
│   ├── Главное окно
│   ├── Skills Manager
│   ├── Plugins Manager
│   ├── Settings
│   ├── Mode Cards
│   └── System Tray
│
├── data/
│   └── Данные приложения
│
├── tests/
│   └── Автоматические тесты
│
├── main.py
├── requirements.txt
├── .gitignore
└── README.md
```

> Структура проекта может изменяться по мере разработки.

---

## 🚀 Установка

### Требования

- Windows 10 или Windows 11.
- Python 3.8 или новее.
- Рабочий микрофон.
- Интернет для Google Speech Recognition.
- Установленные зависимости проекта.

Проект тестировался на Python 3.8.10 x64.

### 1. Клонирование репозитория

```bash
git clone https://github.com/syrex179/Voicehelper.git
cd Voicehelper
```

### 2. Создание виртуального окружения

```bash
python -m venv .venv
```

### 3. Активация окружения

PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

### 4. Установка зависимостей

```bash
python -m pip install -r requirements.txt
```

### 5. Запуск JARVIS

```bash
python main.py
```

После запуска:

1. Выберите язык.
2. Настройте ключевое слово.
3. Выберите микрофон.
4. Проверьте работу микрофона.
5. Нажмите «Слушать».

---

## 🎙️ Голосовая подсистема

JARVIS использует следующие технологии:

| Компонент | Технология |
|---|---|
| Распознавание речи | SpeechRecognition |
| Сервис STT | Google Speech Recognition |
| Синтез речи | Windows SAPI |
| Интеграция TTS | pywin32 |
| Работа с микрофоном | PyAudio |
| Фоновая обработка | Отдельный поток |

### Настройки голоса

- Выбор микрофона.
- Тестирование микрофона.
- Язык распознавания.
- Ключевое слово.
- Включение и отключение TTS.
- Скорость речи.
- Громкость речи.
- Проверка голоса.

Кнопка остановки голоса очищает очередь воспроизведения и прерывает SAPI, если это возможно.

---

## 🧩 Создание плагинов

Каждый плагин располагается в отдельной папке.

Пример:

```text
plugins/
└── example/
    ├── manifest.json
    └── plugin.py
```

Плагин должен реализовывать интерфейс, предусмотренный системой плагинов проекта.

Пример общей структуры:

```python
class Plugin:
    def get_commands(self):
        return []

    def execute(self, command):
        pass
```

> Перед созданием плагина ознакомьтесь с текущей реализацией Plugin Manager.

### Рекомендации

- Используйте структурированные команды.
- Проверяйте входные параметры.
- Не выполняйте непроверенный shell-код.
- Обрабатывайте ошибки.
- Не привязывайте плагин к внутренней логике Router.

---

## 🧪 Тестирование

Для тестирования используется стандартный `unittest`.

### Проверка компиляции

```bash
python -m compileall -q .
```

### Запуск тестов

```bash
python -m unittest discover -s tests -v
```

Автоматические тесты проверяют различные компоненты ядра, плагинов, Skills и голосовой подсистемы.

---

## 🔐 Безопасность и конфиденциальность

JARVIS использует контролируемую архитектуру выполнения команд.

### Основные принципы

- Голосовые команды преобразуются в структурированные намерения.
- Произвольные shell-команды не выполняются напрямую.
- Опасные системные действия требуют подтверждения.
- Черновики Skills не сохраняются без подтверждения.
- Recorder не записывает нажатия клавиш и движения мыши.
- Поиск файлов ограничен разрешёнными каталогами.

### Распознавание речи

Google Speech Recognition требует подключения к интернету.

При использовании внешних сервисов учитывайте вопросы конфиденциальности и обработки аудиоданных.

---

## ⚠️ Текущие ограничения

- Запись произвольных действий мыши и клавиатуры отсутствует по соображениям безопасности.
- Google Speech Recognition требует интернет-соединения.
- Работа микрофона зависит от разрешений Windows.
- Для некоторых плагинов ещё не реализованы специализированные настройки.
- Качество распознавания зависит от микрофона, языка и сети.
- Некоторые плагины требуют дополнительной настройки.

---

## 🛠️ Ручная проверка в Windows

Перед регулярным использованием рекомендуется проверить:

- [ ] Выбор и работу микрофона.
- [ ] Распознавание русской и украинской речи.
- [ ] Работу голосового вывода.
- [ ] Отзывчивость интерфейса.
- [ ] Работу системного трея.
- [ ] Запуск приложений голосом.
- [ ] Создание и запуск Skills.
- [ ] Автозапуск Windows.
- [ ] Сообщения об ошибках.

---

## 🗺️ План развития

- [ ] Новые плагины.
- [ ] Расширение голосовых команд.
- [ ] Дополнительные интеграции с Windows.
- [ ] Расширенные настройки интерфейса.
- [ ] Улучшение автоматизации.
- [ ] Расширение возможностей офлайн-распознавания речи.
- [ ] Дополнительные тесты.

---

## 👨‍💻 Разработчик

<p align="center">
  <strong>Syrex</strong>
</p>

<p align="center">
  <a href="https://github.com/syrex179">
    GitHub Profile
  </a>
</p>

---

# 🇬🇧 English Version

## 🤖 JARVIS — Personal Voice Assistant for Windows

**JARVIS** is an extensible Windows voice assistant designed to control applications, automate everyday tasks and create personalized workflows.

Its modular core routes commands to independent plugins, while user-created Skills remain available after restarting the application.

### 🎯 Project Goals

- Simplify everyday Windows interactions.
- Control applications using voice commands.
- Automate repetitive tasks.
- Create personalized workflows.
- Provide a modular architecture.
- Keep command execution controlled and predictable.

---

## ✨ Features

### 🎙️ Voice Control

- Speech-to-text recognition.
- Wake word support.
- Windows SAPI text-to-speech.
- Configurable speech language.
- Adjustable TTS rate and volume.
- Microphone selection and testing.
- Background voice processing.
- Voice dialog state management.

### 🧩 Plugin System

- Automatic plugin discovery.
- Command registration.
- Plugin enable/disable.
- Plugin reloading.
- Isolated plugin loading errors.
- Extensible command architecture.

### 🧠 Skills & Modes

Create reusable workflows that combine multiple actions.

Example:

```text
"Jarvis, create a Stream mode:
open OBS, Discord and Telegram,
then set the volume to 60."
```

Skills Manager supports:

- Creating modes.
- Editing modes.
- Renaming.
- Changing icons.
- Duplicating.
- Deleting.
- JSON export and import.
- Voice activation.
- Interface-based launching.

### 🎓 Learning by Demonstration

JARVIS includes a safe recorder for creating workflows from successful structured actions.

It does not record:

- Keyboard input.
- Mouse movements.
- Passwords.
- Arbitrary shell commands.
- Unverified user input.

### 🗣️ Local Command Parser

The local parser works without an API key.

It supports:

- Wake word prefixes.
- Application launching.
- Volume control.
- Screenshots.
- Aliases.
- Skills and modes.
- Custom commands.
- Multi-action phrases.

User input is never directly evaluated as code or shell input.

### 🔗 Custom Commands & Aliases

Create custom voice shortcuts and link phrases to existing Skills.

Custom commands are stored in:

```text
data/custom_commands.json
```

### 📁 File Search

JARVIS can search approved directories, including Documents, Downloads and Desktop.

Features include:

- Recursive search.
- Filename and extension filtering.
- File metadata display.
- Opening files.
- Opening containing folders.

### 🖥️ Windows Integration

- Application launching.
- Website and folder opening.
- System volume control.
- Media playback control.
- Screenshots.
- Show desktop.
- Windows autostart.
- System tray integration.

Shutdown, restart and lock actions require confirmation in the user interface.

---

## 🏗️ Architecture

JARVIS uses a modular architecture with separate components for core logic, plugins, speech processing and the user interface.

### Command Flow

```text
Microphone
    ↓
Speech-to-Text
    ↓
Wake Word
    ↓
Intent Parser
    ↓
Router
    ↓
Registry
    ↓
Plugin
    ↓
UI / TTS
```

### Project Structure

```text
Voicehelper/
│
├── core/
├── plugins/
├── speech/
├── ui/
├── data/
├── tests/
├── main.py
├── requirements.txt
└── README.md
```

---

## 🚀 Installation

### Requirements

- Windows 10 or Windows 11.
- Python 3.8 or newer.
- Working microphone.
- Internet connection for Google Speech Recognition.
- Project dependencies.

### Installation Steps

```bash
git clone https://github.com/syrex179/Voicehelper.git
cd Voicehelper
python -m venv .venv
```

PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

Install dependencies:

```bash
python -m pip install -r requirements.txt
```

Run JARVIS:

```bash
python main.py
```

---

## 🎙️ Voice Subsystem

| Component | Technology |
|---|---|
| Speech recognition | SpeechRecognition |
| Speech-to-text service | Google Speech Recognition |
| Text-to-speech | Windows SAPI |
| TTS integration | pywin32 |
| Audio input | PyAudio |
| Background processing | Dedicated thread |

JARVIS provides microphone selection, voice testing, language settings, wake word configuration and TTS controls.

---

## 🧩 Creating Plugins

Each plugin is stored in its own directory.

Example:

```text
plugins/
└── example/
    ├── manifest.json
    └── plugin.py
```

Plugins must implement the interface required by the project's plugin system.

Before creating a plugin, review the current Plugin Manager implementation.

---

## 🧪 Testing

Compile the project:

```bash
python -m compileall -q .
```

Run tests:

```bash
python -m unittest discover -s tests -v
```

The test suite covers core functionality, plugins, Skills and voice subsystem components.

---

## 🔐 Security & Privacy

JARVIS uses controlled command execution.

- Voice input is converted into structured intents.
- Arbitrary shell commands are not executed directly.
- Destructive system actions require confirmation.
- Skill drafts require confirmation before being saved.
- The learning recorder does not capture raw keyboard or mouse input.
- File search is restricted to approved directories.

Google Speech Recognition requires an internet connection.

Consider privacy implications when using third-party speech recognition services.

---

## ⚠️ Current Limitations

- Raw keyboard and mouse recording is intentionally not supported.
- Google Speech Recognition requires internet access.
- Microphone access depends on Windows permissions.
- Some plugins may require additional configuration.
- Voice recognition quality depends on the microphone, language and network connection.

---

## 🛠️ Manual Windows Verification

Before using JARVIS regularly, it is recommended to verify:

- [ ] Microphone selection and functionality.
- [ ] Russian and Ukrainian speech recognition.
- [ ] Voice output.
- [ ] User interface responsiveness.
- [ ] System tray functionality.
- [ ] Voice-based application launching.
- [ ] Skills creation and execution.
- [ ] Windows autostart.
- [ ] Error messages and logs.

---

## 🗺️ Roadmap

- [ ] Additional plugins.
- [ ] Expanded voice command support.
- [ ] More Windows integrations.
- [ ] Improved interface customization.
- [ ] More advanced automation workflows.
- [ ] Expanded offline speech capabilities.
- [ ] Additional tests and documentation.

---

## 👨‍💻 Developer

<p align="center">
  <strong>Syrex</strong>
</p>

<p align="center">
  <a href="https://github.com/syrex179">
    GitHub Profile
  </a>
</p>

---

<p align="center">
  <strong>JARVIS</strong><br>
  <em>Your personal Windows assistant.</em>
</p>

<p align="center">
  Made with ❤️ by Syrex
</p>
