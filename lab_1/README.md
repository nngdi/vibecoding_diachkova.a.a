# Lab 1 — Study Planner Telegram Bot

Учебный Telegram-планировщик.

## Возможности
- добавление задач через кнопки;
- дата и время дедлайна;
- приоритет: высокий / средний / низкий;
- разделы «Сегодня», «Неделя», «Все задачи»;
- расписание занятий;
- напоминание за 30 минут до занятия;
- напоминание перед дедлайном;
- отметка задачи выполненной и удаление.

## Запуск в Google Colab

```python
!pip install -q "python-telegram-bot[job-queue]" nest_asyncio
```

```python
import os
from getpass import getpass
os.environ["BOT_TOKEN"] = getpass("Вставь токен Telegram-бота: ")
```

Затем запустить код из `bot.py`.

> Настоящий токен нельзя загружать в GitHub.
