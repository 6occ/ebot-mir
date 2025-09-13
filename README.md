# Ebot

Spot-бот под MEXC для пары KAS/USDC.

## Быстрый старт
1. Скопируйте `config.example.py` в `config.py` и заполните ключи API.
2. Создайте и активируйте venv, установите зависимости (если есть requirements).
3. Запустите `ebot.service` (systemd) или отдельные скрипты (`buy.py`, `sell.py`, `sync.py`, `report.py`).

> В репозитории **нет** реальных ключей и локальных БД.  
> Файлы `*.db`, `logs/`, `venv/` и `config.py` игнорируются.
