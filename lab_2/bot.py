import os
import sqlite3
import nest_asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from telegram import (
    Update, ReplyKeyboardMarkup, ReplyKeyboardRemove,
    InlineKeyboardMarkup, InlineKeyboardButton, BotCommand
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters
)

nest_asyncio.apply()

TOKEN = os.environ["BOT_TOKEN"]
DB_FILE = "planner.db"

MOSCOW = ZoneInfo("Europe/Moscow")
BEIJING = ZoneInfo("Asia/Shanghai")

PRIORITY_NAMES = {
    1: "🔴 Высокая",
    2: "🟡 Средняя",
    3: "🟢 Низкая",
}

LESSON_TYPES = {
    "lecture": "📖 Лекция",
    "practice": "✏️ Практика",
}

WEEKDAY_NAMES = {
    0: "Понедельник", 1: "Вторник", 2: "Среда",
    3: "Четверг", 4: "Пятница", 5: "Суббота",
    6: "Воскресенье",
}

MAIN_MENU = ReplyKeyboardMarkup(
    [
        ["➕ Добавить задачу", "📅 Сегодня"],
        ["🗓 Неделя", "📚 Расписание"],
        ["📋 Все задачи", "➕ Добавить занятие"],
        ["📊 Статистика", "🕘 История"],
    ],
    resize_keyboard=True,
    is_persistent=True,
)

# -------------------- SQLite --------------------

def connect():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                text TEXT NOT NULL,
                due_datetime TEXT NOT NULL,
                priority INTEGER NOT NULL,
                done INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                completed_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS lessons (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                lesson_type TEXT NOT NULL,
                weekday INTEGER NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        conn.commit()

def add_task(user_id, text, due, priority):
    with connect() as conn:
        cur = conn.execute("""
            INSERT INTO tasks
            (user_id, text, due_datetime, priority, done, created_at)
            VALUES (?, ?, ?, ?, 0, ?)
        """, (user_id, text, due, priority, datetime.now(MOSCOW).isoformat()))
        conn.commit()
        return cur.lastrowid

def get_task(user_id, task_id):
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM tasks WHERE user_id=? AND id=?",
            (user_id, task_id)
        ).fetchone()

def active_tasks(user_id):
    with connect() as conn:
        return conn.execute("""
            SELECT * FROM tasks
            WHERE user_id=? AND done=0
            ORDER BY due_datetime, priority
        """, (user_id,)).fetchall()

def complete_task(user_id, task_id):
    with connect() as conn:
        conn.execute("""
            UPDATE tasks
            SET done=1, completed_at=?
            WHERE user_id=? AND id=?
        """, (datetime.now(MOSCOW).isoformat(), user_id, task_id))
        conn.commit()

def delete_task(user_id, task_id):
    with connect() as conn:
        conn.execute(
            "DELETE FROM tasks WHERE user_id=? AND id=?",
            (user_id, task_id)
        )
        conn.commit()

def history(user_id):
    with connect() as conn:
        return conn.execute("""
            SELECT * FROM tasks
            WHERE user_id=? AND done=1
            ORDER BY completed_at DESC
            LIMIT 10
        """, (user_id,)).fetchall()

def add_lesson(user_id, name, lesson_type, weekday, start_time, end_time):
    with connect() as conn:
        cur = conn.execute("""
            INSERT INTO lessons
            (user_id, name, lesson_type, weekday, start_time, end_time, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id, name, lesson_type, weekday,
            start_time, end_time, datetime.now(MOSCOW).isoformat()
        ))
        conn.commit()
        return cur.lastrowid

def get_lesson(user_id, lesson_id):
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM lessons WHERE user_id=? AND id=?",
            (user_id, lesson_id)
        ).fetchone()

def lessons(user_id):
    with connect() as conn:
        return conn.execute("""
            SELECT * FROM lessons
            WHERE user_id=?
            ORDER BY weekday, start_time
        """, (user_id,)).fetchall()

def delete_lesson(user_id, lesson_id):
    with connect() as conn:
        conn.execute(
            "DELETE FROM lessons WHERE user_id=? AND id=?",
            (user_id, lesson_id)
        )
        conn.commit()

def stats(user_id):
    with connect() as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE user_id=?", (user_id,)
        ).fetchone()[0]
        completed = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE user_id=? AND done=1", (user_id,)
        ).fetchone()[0]
        active = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE user_id=? AND done=0", (user_id,)
        ).fetchone()[0]
        high = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE user_id=? AND done=0 AND priority=1",
            (user_id,)
        ).fetchone()[0]
        all_lessons = conn.execute(
            "SELECT COUNT(*) FROM lessons WHERE user_id=?", (user_id,)
        ).fetchone()[0]
        lectures = conn.execute(
            "SELECT COUNT(*) FROM lessons WHERE user_id=? AND lesson_type='lecture'",
            (user_id,)
        ).fetchone()[0]
        practices = conn.execute(
            "SELECT COUNT(*) FROM lessons WHERE user_id=? AND lesson_type='practice'",
            (user_id,)
        ).fetchone()[0]

    return total, completed, active, high, all_lessons, lectures, practices

# -------------------- Time helpers --------------------

def parse_time(value):
    try:
        return datetime.strptime(value.strip(), "%H:%M").time()
    except ValueError:
        return None

def parse_date(value):
    now = datetime.now(MOSCOW)
    for fmt in ("%d.%m.%Y", "%d.%m"):
        try:
            dt = datetime.strptime(value.strip(), fmt)
            if fmt == "%d.%m":
                dt = dt.replace(year=now.year)
                if dt.date() < now.date():
                    dt = dt.replace(year=now.year + 1)
            return dt.date()
        except ValueError:
            pass
    return None

def dual_time(dt_moscow):
    bj = dt_moscow.astimezone(BEIJING)
    return (
        f"🇷🇺 МСК: {dt_moscow.strftime('%d.%m.%Y %H:%M')}\n"
        f"🇨🇳 Пекин: {bj.strftime('%d.%m.%Y %H:%M')}"
    )

def next_lesson_start(row):
    now = datetime.now(MOSCOW)
    t = parse_time(row["start_time"])
    days = (row["weekday"] - now.weekday()) % 7
    dt = datetime.combine(now.date() + timedelta(days=days), t, tzinfo=MOSCOW)
    if dt <= now:
        dt += timedelta(days=7)
    return dt

def lesson_text(row, date=None):
    if date is None:
        start = next_lesson_start(row)
    else:
        start = datetime.combine(date, parse_time(row["start_time"]), tzinfo=MOSCOW)

    end = datetime.combine(start.date(), parse_time(row["end_time"]), tzinfo=MOSCOW)
    if end <= start:
        end += timedelta(days=1)

    bj_start = start.astimezone(BEIJING)
    bj_end = end.astimezone(BEIJING)

    return (
        f"🇷🇺 МСК: {WEEKDAY_NAMES[start.weekday()]}, "
        f"{start.strftime('%d.%m %H:%M')}–{end.strftime('%H:%M')}\n"
        f"🇨🇳 Пекин: {WEEKDAY_NAMES[bj_start.weekday()]}, "
        f"{bj_start.strftime('%d.%m %H:%M')}–{bj_end.strftime('%H:%M')}"
    )

# -------------------- Keyboards --------------------

def date_keyboard():
    today = datetime.now(MOSCOW).date()
    rows = []
    for i in range(7):
        d = today + timedelta(days=i)
        rows.append([InlineKeyboardButton(
            d.strftime("%d.%m"),
            callback_data=f"task_date:{d.isoformat()}"
        )])
    rows.append([InlineKeyboardButton("📅 Другая дата", callback_data="task_date:custom")])
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(rows)

def time_keyboard(prefix):
    values = ["08:00", "10:00", "12:00", "14:00", "16:00", "18:00", "20:00"]
    rows = []
    for i in range(0, len(values), 2):
        rows.append([
            InlineKeyboardButton(
                v, callback_data=f"{prefix}_time:{v}"
            ) for v in values[i:i+2]
        ])
    rows.append([InlineKeyboardButton("🕐 Другое время", callback_data=f"{prefix}_time:custom")])
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(rows)

def priority_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔴 Высокая", callback_data="priority:1")],
        [InlineKeyboardButton("🟡 Средняя", callback_data="priority:2")],
        [InlineKeyboardButton("🟢 Низкая", callback_data="priority:3")],
        [InlineKeyboardButton("❌ Отмена", callback_data="cancel")],
    ])

def lesson_type_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📖 Лекция", callback_data="lesson_type:lecture"),
            InlineKeyboardButton("✏️ Практика", callback_data="lesson_type:practice"),
        ],
        [InlineKeyboardButton("❌ Отмена", callback_data="cancel")],
    ])

def weekday_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Пн", callback_data="lesson_day:0"),
            InlineKeyboardButton("Вт", callback_data="lesson_day:1"),
            InlineKeyboardButton("Ср", callback_data="lesson_day:2"),
        ],
        [
            InlineKeyboardButton("Чт", callback_data="lesson_day:3"),
            InlineKeyboardButton("Пт", callback_data="lesson_day:4"),
            InlineKeyboardButton("Сб", callback_data="lesson_day:5"),
        ],
        [InlineKeyboardButton("Вс", callback_data="lesson_day:6")],
        [InlineKeyboardButton("❌ Отмена", callback_data="cancel")],
    ])

# -------------------- Telegram handlers --------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "👋 Учебный планировщик — Lab 2\n\n"
        "🗄 Данные хранятся в SQLite.\n"
        "🇷🇺 Время вводится по Москве.\n"
        "🇨🇳 Бот показывает также время Пекина.",
        reply_markup=MAIN_MENU,
    )

async def begin_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    context.user_data["flow"] = "task_name"
    context.user_data["temp"] = {}
    await update.message.reply_text("Напиши название задачи:", reply_markup=ReplyKeyboardRemove())

async def begin_lesson(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    context.user_data["flow"] = "lesson_name"
    context.user_data["temp"] = {}
    await update.message.reply_text("Напиши название предмета:", reply_markup=ReplyKeyboardRemove())

async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data

    if data == "cancel":
        context.user_data.clear()
        await q.message.reply_text("❌ Отменено.", reply_markup=MAIN_MENU)
        return

    if data.startswith("task_date:"):
        value = data.split(":", 1)[1]
        if value == "custom":
            context.user_data["flow"] = "task_custom_date"
            await q.message.reply_text("Введи дату: 25.10 или 25.10.2026")
            return
        context.user_data["temp"]["date"] = value
        await q.message.reply_text("Выбери время:", reply_markup=time_keyboard("task"))
        return

    if data.startswith("task_time:"):
        value = data.split(":", 1)[1]
        if value == "custom":
            context.user_data["flow"] = "task_custom_time"
            await q.message.reply_text("Введи время, например 17:30")
            return
        context.user_data["temp"]["time"] = value
        await q.message.reply_text("Выбери важность:", reply_markup=priority_keyboard())
        return

    if data.startswith("priority:"):
        priority = int(data.split(":", 1)[1])
        temp = context.user_data["temp"]
        date = datetime.fromisoformat(temp["date"]).date()
        due = datetime.combine(date, parse_time(temp["time"]), tzinfo=MOSCOW)

        if due <= datetime.now(MOSCOW):
            await q.message.reply_text("❌ Это время уже прошло.")
            return

        try:
            task_id = add_task(q.from_user.id, temp["text"], due.isoformat(), priority)
            context.user_data.clear()
            await q.message.reply_text(
                "✅ Задача сохранена в SQLite!\n\n"
                f"#{task_id} {temp['text']}\n"
                f"{PRIORITY_NAMES[priority]}\n\n"
                f"{dual_time(due)}",
                reply_markup=MAIN_MENU,
            )
        except sqlite3.Error:
            await q.message.reply_text("⚠️ Не удалось сохранить задачу в базе.")
        return

    if data.startswith("lesson_type:"):
        context.user_data["temp"]["lesson_type"] = data.split(":", 1)[1]
        await q.message.reply_text("Выбери день недели:", reply_markup=weekday_keyboard())
        return

    if data.startswith("lesson_day:"):
        context.user_data["temp"]["weekday"] = int(data.split(":", 1)[1])
        await q.message.reply_text("Выбери время начала:", reply_markup=time_keyboard("lesson_start"))
        return

    if data.startswith("lesson_start_time:"):
        value = data.split(":", 1)[1]
        if value == "custom":
            context.user_data["flow"] = "lesson_custom_start"
            await q.message.reply_text("Введи время начала, например 13:30")
            return
        context.user_data["temp"]["start_time"] = value
        await q.message.reply_text("До скольки идёт занятие?", reply_markup=time_keyboard("lesson_end"))
        return

    if data.startswith("lesson_end_time:"):
        value = data.split(":", 1)[1]
        if value == "custom":
            context.user_data["flow"] = "lesson_custom_end"
            await q.message.reply_text("Введи время окончания, например 15:30")
            return

        temp = context.user_data["temp"]
        if parse_time(value) <= parse_time(temp["start_time"]):
            await q.message.reply_text("❌ Окончание должно быть позже начала.")
            return

        try:
            lesson_id = add_lesson(
                q.from_user.id, temp["name"], temp["lesson_type"],
                temp["weekday"], temp["start_time"], value
            )
            row = get_lesson(q.from_user.id, lesson_id)
            context.user_data.clear()
            await q.message.reply_text(
                "✅ Занятие сохранено в SQLite!\n\n"
                f"📚 {row['name']}\n"
                f"{LESSON_TYPES[row['lesson_type']]}\n\n"
                f"{lesson_text(row)}",
                reply_markup=MAIN_MENU,
            )
        except sqlite3.Error:
            await q.message.reply_text("⚠️ Не удалось сохранить занятие.")
        return

    if data.startswith("done:"):
        task_id = int(data.split(":", 1)[1])
        row = get_task(q.from_user.id, task_id)
        if row:
            complete_task(q.from_user.id, task_id)
            await q.edit_message_text(f"✅ Выполнено\n\n{row['text']}")
        return

    if data.startswith("delete:"):
        task_id = int(data.split(":", 1)[1])
        row = get_task(q.from_user.id, task_id)
        if row:
            delete_task(q.from_user.id, task_id)
            await q.edit_message_text(f"🗑 Задача удалена\n\n{row['text']}")
        return

    if data.startswith("lesson_delete:"):
        lesson_id = int(data.split(":", 1)[1])
        row = get_lesson(q.from_user.id, lesson_id)
        if row:
            delete_lesson(q.from_user.id, lesson_id)
            await q.edit_message_text(f"🗑 Занятие удалено\n\n{row['name']}")

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    flow = context.user_data.get("flow")

    if flow == "task_name":
        context.user_data["temp"]["text"] = text
        await update.message.reply_text("Выбери дату:", reply_markup=date_keyboard())
        return

    if flow == "lesson_name":
        context.user_data["temp"]["name"] = text
        await update.message.reply_text("Выбери тип:", reply_markup=lesson_type_keyboard())
        return

    if flow == "task_custom_date":
        date = parse_date(text)
        if not date:
            await update.message.reply_text("❌ Неверная дата.")
            return
        context.user_data["temp"]["date"] = date.isoformat()
        await update.message.reply_text("Выбери время:", reply_markup=time_keyboard("task"))
        return

    if flow == "task_custom_time":
        t = parse_time(text)
        if not t:
            await update.message.reply_text("❌ Формат HH:MM.")
            return
        context.user_data["temp"]["time"] = t.strftime("%H:%M")
        await update.message.reply_text("Выбери важность:", reply_markup=priority_keyboard())
        return

    if flow == "lesson_custom_start":
        t = parse_time(text)
        if not t:
            await update.message.reply_text("❌ Формат HH:MM.")
            return
        context.user_data["temp"]["start_time"] = t.strftime("%H:%M")
        await update.message.reply_text("До скольки идёт занятие?", reply_markup=time_keyboard("lesson_end"))
        return

    if flow == "lesson_custom_end":
        end = parse_time(text)
        if not end:
            await update.message.reply_text("❌ Формат HH:MM.")
            return
        temp = context.user_data["temp"]
        if end <= parse_time(temp["start_time"]):
            await update.message.reply_text("❌ Окончание должно быть позже начала.")
            return

        lesson_id = add_lesson(
            update.effective_user.id, temp["name"], temp["lesson_type"],
            temp["weekday"], temp["start_time"], end.strftime("%H:%M")
        )
        row = get_lesson(update.effective_user.id, lesson_id)
        context.user_data.clear()
        await update.message.reply_text(
            "✅ Занятие сохранено в SQLite!\n\n"
            f"📚 {row['name']}\n"
            f"{LESSON_TYPES[row['lesson_type']]}\n\n"
            f"{lesson_text(row)}",
            reply_markup=MAIN_MENU,
        )
        return

    if text == "➕ Добавить задачу":
        await begin_task(update, context)
    elif text == "➕ Добавить занятие":
        await begin_lesson(update, context)
    elif text == "📋 Все задачи":
        await show_tasks(update)
    elif text == "📚 Расписание":
        await show_schedule(update)
    elif text == "📅 Сегодня":
        await show_today(update)
    elif text == "🗓 Неделя":
        await show_week(update)
    elif text == "📊 Статистика":
        await show_stats(update)
    elif text == "🕘 История":
        await show_history(update)
    else:
        await update.message.reply_text("Выбери действие кнопкой 👇", reply_markup=MAIN_MENU)

async def show_tasks(update: Update):
    rows = active_tasks(update.effective_user.id)
    if not rows:
        await update.message.reply_text("📭 Активных задач нет.", reply_markup=MAIN_MENU)
        return

    await update.message.reply_text("📋 Все активные задачи:")
    for row in rows:
        due = datetime.fromisoformat(row["due_datetime"])
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Выполнено", callback_data=f"done:{row['id']}"),
            InlineKeyboardButton("🗑 Удалить", callback_data=f"delete:{row['id']}"),
        ]])
        await update.message.reply_text(
            f"{PRIORITY_NAMES[row['priority']]}\n"
            f"📌 {row['text']}\n\n{dual_time(due)}",
            reply_markup=kb,
        )

async def show_schedule(update: Update):
    rows = lessons(update.effective_user.id)
    if not rows:
        await update.message.reply_text("📚 Расписание пустое.", reply_markup=MAIN_MENU)
        return

    await update.message.reply_text("📚 Расписание\n🇷🇺 Москва → 🇨🇳 Пекин")
    for row in rows:
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("🗑 Удалить занятие", callback_data=f"lesson_delete:{row['id']}")
        ]])
        await update.message.reply_text(
            f"📚 {row['name']}\n"
            f"{LESSON_TYPES[row['lesson_type']]}\n\n"
            f"{lesson_text(row)}",
            reply_markup=kb,
        )

async def show_today(update: Update):
    user_id = update.effective_user.id
    today = datetime.now(MOSCOW).date()
    task_rows = [
        r for r in active_tasks(user_id)
        if datetime.fromisoformat(r["due_datetime"]).date() == today
    ]
    lesson_rows = [r for r in lessons(user_id) if r["weekday"] == today.weekday()]

    text = f"📅 Сегодня, {today.strftime('%d.%m.%Y')}\n\n🎓 ЗАНЯТИЯ\n"
    if lesson_rows:
        for row in lesson_rows:
            text += f"\n{row['name']} — {LESSON_TYPES[row['lesson_type']]}\n{lesson_text(row, today)}\n"
    else:
        text += "Нет занятий.\n"

    text += "\n📌 ЗАДАЧИ\n"
    if task_rows:
        for row in task_rows:
            text += f"\n{PRIORITY_NAMES[row['priority']]} {row['text']}\n{dual_time(datetime.fromisoformat(row['due_datetime']))}\n"
    else:
        text += "Нет задач."

    await update.message.reply_text(text, reply_markup=MAIN_MENU)

async def show_week(update: Update):
    user_id = update.effective_user.id
    start = datetime.now(MOSCOW).date()
    task_rows = active_tasks(user_id)
    lesson_rows = lessons(user_id)
    text = "🗓 План на 7 дней\n"
    found = False

    for offset in range(7):
        day = start + timedelta(days=offset)
        day_tasks = [r for r in task_rows if datetime.fromisoformat(r["due_datetime"]).date() == day]
        day_lessons = [r for r in lesson_rows if r["weekday"] == day.weekday()]
        if not day_tasks and not day_lessons:
            continue
        found = True
        text += f"\n📅 {WEEKDAY_NAMES[day.weekday()]}, {day.strftime('%d.%m')}\n"
        for row in day_lessons:
            text += f"🎓 {row['name']} — {LESSON_TYPES[row['lesson_type']]}\n{lesson_text(row, day)}\n"
        for row in day_tasks:
            text += f"📌 {row['text']} — {PRIORITY_NAMES[row['priority']]}\n{dual_time(datetime.fromisoformat(row['due_datetime']))}\n"

    if not found:
        text += "\nНичего не запланировано."

    await update.message.reply_text(text, reply_markup=MAIN_MENU)

async def show_stats(update: Update):
    total, completed, active, high, lesson_count, lectures, practices = stats(update.effective_user.id)
    await update.message.reply_text(
        "📊 Статистика\n\n"
        f"Всего задач: {total}\n"
        f"✅ Выполнено: {completed}\n"
        f"⏳ Активных: {active}\n"
        f"🔴 Высокий приоритет: {high}\n\n"
        f"🎓 Всего занятий: {lesson_count}\n"
        f"📖 Лекций: {lectures}\n"
        f"✏️ Практик: {practices}",
        reply_markup=MAIN_MENU,
    )

async def show_history(update: Update):
    rows = history(update.effective_user.id)
    if not rows:
        await update.message.reply_text("🕘 История пока пустая.", reply_markup=MAIN_MENU)
        return

    text = "🕘 Последние выполненные задачи\n"
    for row in rows:
        completed = datetime.fromisoformat(row["completed_at"])
        text += f"\n✅ {row['text']}\n{completed.strftime('%d.%m.%Y %H:%M')}\n"

    await update.message.reply_text(text, reply_markup=MAIN_MENU)

async def error_handler(update, context):
    print("Ошибка:", context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text("⚠️ Произошла ошибка. Попробуй ещё раз.")
        except Exception:
            pass

async def post_init(application):
    init_db()
    await application.bot.set_my_commands([
        BotCommand("start", "Открыть главное меню"),
    ])

application = (
    Application.builder()
    .token(TOKEN)
    .post_init(post_init)
    .build()
)

application.add_handler(CommandHandler("start", start))
application.add_handler(CallbackQueryHandler(callbacks))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
application.add_error_handler(error_handler)

print("✅ LAB 2 запущена")
print("🗄 Источник данных: SQLite")
print("📊 Статистика и история включены")

application.run_polling(
    close_loop=False,
    drop_pending_updates=True,
)
