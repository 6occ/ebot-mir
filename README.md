# Ebot — Grid Trading Bot (KAS/USDC)

## 📌 Описание
Ebot — это торговый бот для биржи MEXC, работающий в сеточном режиме.
Поддерживает buy/sell, отчёты в Telegram и работу в нескольких сервисах
(systemd). Код разделён на модули: `buy.py`, `sell.py`, `sync.py`, 
`candles.py`, `report.py`, `ebot.py`.

## ⚙️ Установка и запуск
1. Клонировать репозиторий:
   git clone git@github.com:6occ/ebot.git /opt/Ebot
   cd /opt/Ebot
2. Установить зависимости:
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
3. Настроить конфиг:
   cp config.example.py config.py
   # внести ключи API, Telegram токен и стартовые параметры
4. Запустить сервисы:
   sudo systemctl enable --now ebot.service
   sudo systemctl enable --now ebot-candles.service

## 📊 Конфигурация
Основные параметры находятся в `config.py`:
- START_CAPITAL_USD = 1000
- PAIR = "KASUSDC"
- BUY/SELL стратегии (см. комментарии внутри конфига)
- Интервалы синхронизации, лимиты, квантование

## 🧩 Сервисы
- ebot.service — оркестратор (buy/sell/sync/report)
- ebot-candles.service — сбор минутных свечей через WS+HTTP
- report.py — формирует отчёты в Telegram каждые 30 мин

## 📈 Отчёты
Бот присылает в Telegram:
- Баланс USDC/KAS
- Текущую позицию
- Среднюю цену входа
- Unrealized PNL
- PNL за 1 час, 24 часа и общий от старта

Формат:
PNL
1 час: +3.25$ (0.3%)
24 часа: -12.07$ (1.2%)
Всего: +300.05$ (30%)

## 🛠️ Разработка
- Линтеры: ruff + pyflakes
- CI/CD: push → GitHub → mirror
- Тесты проводятся на `dev`-ветке, затем merge в `main`

## 📋 TODO / планы
- Защита на медвежьем рынке
- Автоуплотнение ордеров при переполнении
- Дополнительные режимы buy/sell
- Улучшенный risk management
