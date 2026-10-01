# 🎙️ ESP32-S3 Voice Control Satellite & Central Server System

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![ESP-IDF](https://img.shields.io/badge/ESP--IDF-v5.5-orange.svg)
![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)
![Home Assistant](https://img.shields.io/badge/Home%20Assistant-Integration-blue.svg)

Полностью локальная экосистема голосового сателлита для **Home Assistant** с аппаратной обработкой звука на ESP32-S3, нейросетевым детектором ключевых слов (ESP-SR WakeNet), потоковой передачей аудио по WebSocket, высокоточным распознаванием речи (Sherpa-ONNX GigaAM v2 Russian), мультирум-арбитражем и веб-панелью управления.

---

## 🔌 Схема подключения оборудования (Pinout & Wiring)

### 1. Подключение I2S Микрофона (SPH0645)

```text
┌───────────────────────────┐               ┌───────────────────────────┐
│     ESP32-S3 DevKit       │               │ SPH0645 I2S MEMS Mic      │
│                           │               │                           │
│                      3V3 ─┼───────────────┼─ 3V3                      │
│                      GND ─┼───────────────┼─ GND                      │
│     GPIO15 (I2S Mic WS)  ─┼───────────────┼─ WS / LRCLK               │
│     GPIO16 (I2S Mic BCLK)─┼───────────────┼─ BCLK                     │
│     GPIO17 (I2S Mic DIN) ─┼───────────────┼─ DATA / DOUT              │
│                           │         ┌─────┼─ SEL / L/R (Slot Select)  │
│                           │         │     └───────────────────────────┘
│                      GND ─┼─────────┴───── (GND = Left, 3V3 = Right)  │
└───────────────────────────┘
```

### 2. Подключение I2S Усилителя и Динамика (MAX98357A)

```text
┌───────────────────────────┐               ┌───────────────────────────┐
│     ESP32-S3 DevKit       │               │ MAX98357A I2S Amplifier   │
│                           │               │                           │
│                       5V ─┼───────────────┼─ VIN / VCC (5V recommended)│
│                      GND ─┼───────────────┼─ GND                      │
│    GPIO7 (I2S Spk BCLK)  ─┼───────────────┼─ BCLK                     │
│    GPIO8 (I2S Spk LRCK)  ─┼───────────────┼─ LRC / WS                 │
│    GPIO18 (I2S Spk DOUT) ─┼───────────────┼─ DIN                      │
│                           │               │                           │
│                           │               │    [  +  ]     [  -  ]    │
└───────────────────────────┘               └──────┬───────────┬────────┘
                                                   │           │
                                              ┌────┴───────────┴────┐
                                              │   Динамик 4Ω / 8Ω   │
                                              └─────────────────────┘
```

### 3. Сводная таблица контактов (Pinout Summary)

| Модуль | Пин модуля | Пин ESP32-S3 | Описание |
|---|---|---|---|
| **SPH0645 (Mic)** | 3V3 | 3V3 | Питание микрофона (3.3V) |
| | GND | GND | Общий провод |
| | BCLK | **GPIO 16** | I2S Bit Clock |
| | WS / LRCLK | **GPIO 15** | I2S Word Select / Frame Sync |
| | DATA / DOUT | **GPIO 17** | I2S Data In |
| | SEL / L/R | GND | Слот канала (GND = Left, 3V3 = Right) |
| **MAX98357A (Amp)**| VIN | 5V / 3V3 | Питание усилителя (рекомендуется 5V) |
| | GND | GND | Общий провод |
| | BCLK | **GPIO 7** | I2S Bit Clock (Speaker) |
| | LRC / WS | **GPIO 8** | I2S Word Select (Speaker) |
| | DIN | **GPIO 18** | I2S Data Out (Speaker) |
| | Out+ / Out- | Динамик | Подключение динамика 4Ω / 8Ω (3W) |
| **WS2812 LED** | DIN | **GPIO 48** | Встроенный RGB светодиод статуса |

---

## 📁 Структура проекта

```text
voice_satellite_export/
├── firmware/                 # Исходный код прошивки сателлита (ESP-IDF v5.5)
│   ├── main/
│   │   ├── main.c            # Логика I2S, WakeNet, WebServer captive portal, WS & NVS
│   │   ├── CMakeLists.txt
│   │   ├── idf_component.yml # Зависимости ESP-IDF (esp-sr, led_strip и др.)
│   │   └── Kconfig.projbuild
│   ├── CMakeLists.txt
│   ├── sdkconfig.defaults    # Настройки AFE, WakeNet models, PSRAM и 16MB Flash
│   └── partitions.csv        # Кастомная таблица разделов Flash
│
├── firmware_bin/             # Готовые скомпилированные бинарники прошивки
│   ├── wake_word_test.bin    # Прошивка сателлита
│   ├── bootloader.bin        # Загрузчик ESP32-S3
│   ├── partition-table.bin   # Таблица разделов Flash
│   ├── srmodels.bin          # Раздел нейросетевых моделей ESP-SR
│   ├── flash.bat             # Скрипт прошивки в 1 клик для Windows
│   ├── flash.sh              # Скрипт прошивки для Linux / macOS
│   └── README.md             # Инструкция по заливке бинарников
│
├── server/                   # Центральный сервер голосового управления (Python 3.10+)
│   ├── main.py               # HTTP & WebSocket сервер (aiohttp)
│   ├── audio_cleaner.py      # Цифровая очистка аудио (DC offset, High-Pass filter, Normalization)
│   ├── audio_store.py        # Сохранение WAV и расчет параметров сигнала
│   ├── ha_client.py          # Клиент Home Assistant WebSocket API
│   ├── pipeline.py           # Оркестратор VAD -> STT -> HA -> TTS
│   ├── stt.py                # Движок локального STT (Sherpa-ONNX GigaAM v2)
│   ├── tts.py                # Движок локального синтеза речи (Silero TTS)
│   ├── vad.py                # Детектор активности речи
│   └── web/
│       └── index.html        # Веб-панель управления и звукового мониторинга
│
├── scripts/                  # Вспомогательные скрипты
│   ├── download_stt_models.py# Загрузка и подготовка нейросетевой модели STT
│   └── test_satellite_sim.py # Симулятор сателлита для тестирования сервера
│
├── tests/                    # Модульные и интеграционные тесты
├── Dockerfile                # Dockerfile для сборки контейнера сервера
├── docker-compose.yml        # Docker Compose конфигурация
├── .env.example              # Шаблон конфигурации переменных окружения
├── run_server.py             # Скрипт запуска сервера
└── requirements.txt          # Зависимости Python
```

---

## ⚡ Быстрый старт: Заливка готовой прошивки (`firmware_bin/`)

Если вы не хотите устанавливать и настраивать ESP-IDF, используйте готовые скомпилированные бинарные файлы.

### 1. Установка `esptool`
Убедитесь, что установлен Python и выполните:
```bash
pip install esptool
```

### 2. Заливка прошивки в ESP32-S3

* **Windows:**
  ```cmd
  cd firmware_bin
  flash.bat COM3
  ```
  *(Замените `COM3` на номер вашего COM-порта).*

* **Linux / macOS:**
  ```bash
  cd firmware_bin
  chmod +x flash.sh
  ./flash.sh /dev/ttyACM0
  ```

### 3. Первичная настройка сателлита (Wi-Fi Captive Portal)
1. После прошивки сателлит перезагрузится в режим конфигурации (индикатор мигает **ЖЕЛТЫМ**).
2. Подключитесь с телефона или компьютера к Wi-Fi сети **`ESP32-Voice-Satellite`**.
3. В автоматически открывшемся окне (или в браузере по адресу `http://192.168.4.1`):
   - Укажите SSID и пароль вашей Wi-Fi сети.
   - Укажите IP-адрес центрального сервера (например, `ws://192.168.1.50:8765/ws_satellite`).
   - Укажите идентификатор устройства (`device_id`) и комнаты (`area_id`).
4. Нажмите **Save Configuration**. Сателлит сохранится в NVS и автоматически подключится к серверу.

---

## 🛠️ Сборка прошивки из исходников (`firmware/`)

Для доработки или пересборки прошивки потребуется **ESP-IDF v5.x**:

1. Настройте окружение ESP-IDF в терминале.
2. Перейдите в каталог `firmware`:
   ```bash
   cd firmware
   ```
3. Установите целевую архитектуру и соберите проект:
   ```bash
   idf.py set-target esp32s3
   idf.py build
   ```
4. Прошейте устройство и откройте монитор порта:
   ```bash
   idf.py -p COM3 flash monitor
   ```

---

## 🚀 Развертывание Центрального Сервера (`server/`)

### Вариант А: Локальный запуск на ПК / Сервере

1. **Создайте виртуальное окружение Python и установите зависимости:**
   ```bash
   python -m venv .venv
   # Windows:
   .venv\Scripts\activate
   # Linux/macOS:
   source .venv/bin/activate

   pip install -r requirements.txt
   ```

2. **Загрузите нейросетевую модель STT (Sherpa-ONNX GigaAM v2 Russian):**
   ```bash
   python scripts/download_stt_models.py
   ```

3. **Создайте конфигурационный файл `.env`:**
   Скопируйте пример:
   ```bash
   cp .env.example .env
   ```
   Отредактируйте `.env`:
   - `HA_URL`: URL вашего Home Assistant (например, `http://192.168.1.100:8123`)
   - `HA_TOKEN`: Долговечный токен доступа (Long-Lived Access Token) из Home Assistant

4. **Запустите сервер:**
   ```bash
   python run_server.py
   ```

### Вариант Б: Запуск в Docker

1. Подготовьте `.env` файл на основе `.env.example`.
2. Запустите контейнер через Docker Compose:
   ```bash
   docker-compose up -d --build
   ```

---

## 🌐 Веб-панель управления и мониторинга

После запуска сервера откройте в браузере:
👉 **`http://localhost:8765/`** (или IP адрес вашего сервера)

### Возможности веб-интерфейса:
* **Мониторинг сателлитов онлайн**: Статус подключения, сигнал Wi-Fi, текущее состояние (IDLE, STREAMING, PLAYBACK).
* **Удаленное изменение параметров в реальном времени**:
  - Чувствительность микрофона (`mic_gain`)
  - Громкость динамика (`speaker_volume`)
  - Порог срабатывания VAD (`vad_multiplier`)
  - Переключение режимов работы микрофона (Left / Right / Auto)
  - Фильтрация постоянной составляющей (`DC removal`)
* **Удаленная перезагрузка сателлита**.
* **Аудио-диагностика**: Прослушивание записей, спектрограмма, индикатор уровня сигнала и искажений.

---

## 🏠 Интеграция с Home Assistant

1. В Home Assistant откройте ваш профиль пользователя (`/profile`).
2. Внизу страницы нажмите **Создать токен долгосрочного доступа** (Long-Lived Access Token).
3. Скопируйте токен в файл `.env` вашего сервера:
   ```env
   HA_URL=http://192.168.1.100:8123
   HA_TOKEN=ваш_токен_здесь
   ```
4. Сервер автоматически распознает команды (например, *"включи свет в гостиной"*, *"какая температура в спальне"*, *"выключи все розетки"*) и отправляет их в API Home Assistant.

---

## 🔒 Безопасность и приватность

- **Никаких облачных сервисов**: Обработка голоса, Wake Word, STT и синтез речи (TTS) на 100% происходят локально в вашей сети.
- **Приватность записи**: Сателлит начинает передачу звукового потока на сервер **только** после локального срабатывания аппаратно-нейросетевого детектора ESP-SR WakeNet на устройстве.

---

## 📜 Лицензия

Проект распространяется под лицензией MIT. Подробности см. в файле [LICENSE](LICENSE).

