import os
import json
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

TOKEN = os.environ.get("BOT_TOKEN")
TIMEZONE = ZoneInfo("Europe/Moscow")
DATA_FILE = "study_planner.json"

PRIORITY_NAMES = {1: "🔴 Высокая", 2: "🟡 Средняя", 3: "🟢 Низкая"}
WEEKDAY_NAMES = {
    0: "Понедельник", 1: "Вторник", 2: "Среда", 3: "Четверг",
    4: "Пятница", 5: "Суббота", 6: "Воскресенье"
}
WEEKDAY_SHORT = {0:"Пн",1:"Вт",2:"Ср",3:"Чт",4:"Пт",5:"Сб",6:"Вс"}

MAIN_MENU = ReplyKeyboardMarkup(
    [
        ["➕ Добавить задачу", "📅 Сегодня"],
        ["🗓 Неделя", "📚 Расписание"],
        ["📋 Все задачи", "➕ Добавить занятие"],
    ],
    resize_keyboard=True,
    is_persistent=True,
)


def load_data():
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_user(data, user_id):
    key = str(user_id)
    if key not in data:
        data[key] = {"tasks": [], "lessons": []}
    return data[key]


def next_id(items):
    return 1 if not items else max(x["id"] for x in items) + 1


def parse_time(text):
    try:
        return datetime.strptime(text.strip(), "%H:%M").time()
    except ValueError:
        return None


def parse_date(text):
    now = datetime.now(TIMEZONE)
    for fmt in ("%d.%m.%Y", "%d.%m"):
        try:
            d = datetime.strptime(text.strip(), fmt)
            if fmt == "%d.%m":
                d = d.replace(year=now.year)
                if d.date() < now.date():
                    d = d.replace(year=now.year + 1)
            return d.date()
        except ValueError:
            pass
    return None


def cancel_job(app, name):
    for job in app.job_queue.get_jobs_by_name(name):
        job.schedule_removal()


async def task_reminder(context: ContextTypes.DEFAULT_TYPE):
    task = context.job.data
    due = datetime.fromisoformat(task["datetime"])
    await context.bot.send_message(
        chat_id=context.job.chat_id,
        text=(
            "⏰ Напоминание о задаче!\n\n"
            f"📌 {task['text']}\n"
            f"⭐ {PRIORITY_NAMES[task['priority']]}\n"
            f"🕐 Дедлайн: {due.strftime('%d.%m в %H:%M')}"
        ),
    )


async def lesson_reminder(context: ContextTypes.DEFAULT_TYPE):
    lesson = context.job.data
    await context.bot.send_message(
        chat_id=context.job.chat_id,
        text=(
            "🎓 Скоро занятие!\n\n"
            f"📚 {lesson['name']}\n"
            f"🕐 Начало в {lesson['time']}\n\n"
            "⏰ До занятия осталось 30 минут."
        ),
    )
    schedule_lesson_job(context.application, context.job.chat_id, lesson)


def schedule_task_job(app, chat_id, task):
    if task.get("done"):
        return
    due = datetime.fromisoformat(task["datetime"])
    now = datetime.now(TIMEZONE)
    if due <= now:
        return
    reminder = due - timedelta(hours=1)
    if reminder <= now:
        reminder = now + timedelta(seconds=5)
    name = f"task_{chat_id}_{task['id']}"
    cancel_job(app, name)
    app.job_queue.run_once(task_reminder, reminder, chat_id=chat_id, data=task, name=name)


def next_lesson_datetime(lesson):
    now = datetime.now(TIMEZONE)
    t = datetime.strptime(lesson["time"], "%H:%M").time()
    days = (lesson["weekday"] - now.weekday()) % 7
    dt = datetime.combine(now.date() + timedelta(days=days), t, tzinfo=TIMEZONE)
    if dt - timedelta(minutes=30) <= now:
        dt += timedelta(days=7)
    return dt


def schedule_lesson_job(app, chat_id, lesson):
    dt = next_lesson_datetime(lesson)
    reminder = dt - timedelta(minutes=30)
    name = f"lesson_{chat_id}_{lesson['id']}"
    cancel_job(app, name)
    app.job_queue.run_once(lesson_reminder, reminder, chat_id=chat_id, data=lesson, name=name)


def date_keyboard():
    today = datetime.now(TIMEZONE).date()
    rows = []
    for i in range(7):
        d = today + timedelta(days=i)
        if i == 0:
            label = f"Сегодня · {d.strftime('%d.%m')}"
        elif i == 1:
            label = f"Завтра · {d.strftime('%d.%m')}"
        else:
            label = f"{WEEKDAY_SHORT[d.weekday()]} {d.strftime('%d.%m')}"
        rows.append([InlineKeyboardButton(label, callback_data=f"task_date:{d.isoformat()}")])
    rows += [
        [InlineKeyboardButton("📅 Другая дата", callback_data="task_date:custom")],
        [InlineKeyboardButton("❌ Отмена", callback_data="cancel")],
    ]
    return InlineKeyboardMarkup(rows)


def time_keyboard(prefix):
    times = ["08:00","10:00","12:00","14:00","16:00","18:00","20:00"]
    rows = []
    for i in range(0, len(times), 2):
        rows.append([
            InlineKeyboardButton(t, callback_data=f"{prefix}_time:{t}")
            for t in times[i:i+2]
        ])
    rows += [
        [InlineKeyboardButton("🕐 Другое время", callback_data=f"{prefix}_time:custom")],
        [InlineKeyboardButton("❌ Отмена", callback_data="cancel")],
    ]
    return InlineKeyboardMarkup(rows)


def priority_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔴 Высокая", callback_data="priority:1")],
        [InlineKeyboardButton("🟡 Средняя", callback_data="priority:2")],
        [InlineKeyboardButton("🟢 Низкая", callback_data="priority:3")],
        [InlineKeyboardButton("❌ Отмена", callback_data="cancel")],
    ])


def weekday_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Пн", callback_data="lesson_day:0"), InlineKeyboardButton("Вт", callback_data="lesson_day:1"), InlineKeyboardButton("Ср", callback_data="lesson_day:2")],
        [InlineKeyboardButton("Чт", callback_data="lesson_day:3"), InlineKeyboardButton("Пт", callback_data="lesson_day:4"), InlineKeyboardButton("Сб", callback_data="lesson_day:5")],
        [InlineKeyboardButton("Вс", callback_data="lesson_day:6")],
        [InlineKeyboardButton("❌ Отмена", callback_data="cancel")],
    ])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "👋 Привет! Я твой учебный планировщик.\n\n"
        "📌 храню задачи и дедлайны;\n"
        "⭐ учитываю важность;\n"
        "🎓 храню расписание;\n"
        "⏰ напоминаю о занятиях и задачах.\n\n"
        "Выбери действие кнопкой 👇",
        reply_markup=MAIN_MENU,
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 Выбери действие в меню. Для задачи нужно написать только название — "
        "дату, время и важность можно выбрать кнопками.",
        reply_markup=MAIN_MENU,
    )


async def begin_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    context.user_data["flow"] = "task_name"
    context.user_data["temp"] = {}
    await update.message.reply_text(
        "➕ Новая задача\n\nНапиши, что нужно сделать.\nНапример: Сдать лабораторную №1",
        reply_markup=ReplyKeyboardRemove(),
    )


async def begin_lesson(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    context.user_data["flow"] = "lesson_name"
    context.user_data["temp"] = {}
    await update.message.reply_text(
        "🎓 Новое занятие\n\nНапиши название предмета.",
        reply_markup=ReplyKeyboardRemove(),
    )


async def save_lesson_from_query(query, context, time_value):
    temp = context.user_data["temp"]
    data = load_data()
    user = get_user(data, query.from_user.id)
    lesson = {
        "id": next_id(user["lessons"]),
        "name": temp["name"],
        "weekday": temp["weekday"],
        "time": time_value,
    }
    user["lessons"].append(lesson)
    save_data(data)
    schedule_lesson_job(context.application, query.message.chat_id, lesson)
    context.user_data.clear()
    await query.message.reply_text(
        "✅ Занятие добавлено!\n\n"
        f"📚 {lesson['name']}\n"
        f"📅 {WEEKDAY_NAMES[lesson['weekday']]}\n"
        f"🕐 {lesson['time']}\n\n"
        "⏰ Напомню за 30 минут.",
        reply_markup=MAIN_MENU,
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data

    if data == "cancel":
        context.user_data.clear()
        await q.message.reply_text("❌ Действие отменено.", reply_markup=MAIN_MENU)
        return

    if data.startswith("task_date:"):
        value = data.split(":",1)[1]
        if value == "custom":
            context.user_data["flow"] = "task_custom_date"
            await q.message.reply_text("📅 Напиши дату, например 25.10 или 25.10.2026")
            return
        context.user_data["temp"]["date"] = value
        context.user_data["flow"] = "task_choose_time"
        await q.message.reply_text("🕐 Выбери время дедлайна:", reply_markup=time_keyboard("task"))
        return

    if data.startswith("task_time:"):
        value = data.split(":",1)[1]
        if value == "custom":
            context.user_data["flow"] = "task_custom_time"
            await q.message.reply_text("🕐 Напиши время, например 17:30")
            return
        context.user_data["temp"]["time"] = value
        context.user_data["flow"] = "task_choose_priority"
        await q.message.reply_text("⭐ Насколько важна задача?", reply_markup=priority_keyboard())
        return

    if data.startswith("priority:"):
        priority = int(data.split(":",1)[1])
        temp = context.user_data["temp"]
        d = datetime.fromisoformat(temp["date"]).date()
        t = datetime.strptime(temp["time"], "%H:%M").time()
        due = datetime.combine(d, t, tzinfo=TIMEZONE)
        if due <= datetime.now(TIMEZONE):
            context.user_data["flow"] = "task_choose_time"
            await q.message.reply_text("⚠️ Это время уже прошло. Выбери другое:", reply_markup=time_keyboard("task"))
            return
        data_all = load_data()
        user = get_user(data_all, q.from_user.id)
        task = {
            "id": next_id(user["tasks"]),
            "text": temp["text"],
            "datetime": due.isoformat(),
            "priority": priority,
            "done": False,
        }
        user["tasks"].append(task)
        save_data(data_all)
        schedule_task_job(context.application, q.message.chat_id, task)
        context.user_data.clear()
        await q.message.reply_text(
            "✅ Задача добавлена!\n\n"
            f"📌 {task['text']}\n📅 {due.strftime('%d.%m.%Y')}\n"
            f"🕐 {due.strftime('%H:%M')}\n⭐ {PRIORITY_NAMES[priority]}\n\n"
            "⏰ Напоминание включено.",
            reply_markup=MAIN_MENU,
        )
        return

    if data.startswith("lesson_day:"):
        wd = int(data.split(":",1)[1])
        context.user_data["temp"]["weekday"] = wd
        context.user_data["flow"] = "lesson_choose_time"
        await q.message.reply_text(
            f"📅 {WEEKDAY_NAMES[wd]}\n\nВо сколько начинается занятие?",
            reply_markup=time_keyboard("lesson"),
        )
        return

    if data.startswith("lesson_time:"):
        value = data.split(":",1)[1]
        if value == "custom":
            context.user_data["flow"] = "lesson_custom_time"
            await q.message.reply_text("🕐 Напиши время начала, например 13:30")
            return
        await save_lesson_from_query(q, context, value)
        return

    if data.startswith("done:") or data.startswith("delete:"):
        action, raw_id = data.split(":",1)
        task_id = int(raw_id)
        all_data = load_data()
        user = get_user(all_data, q.from_user.id)
        for task in list(user["tasks"]):
            if task["id"] == task_id:
                cancel_job(context.application, f"task_{q.message.chat_id}_{task_id}")
                if action == "done":
                    task["done"] = True
                    save_data(all_data)
                    await q.edit_message_text(f"✅ Выполнено\n\n📌 {task['text']}")
                else:
                    user["tasks"].remove(task)
                    save_data(all_data)
                    await q.edit_message_text(f"🗑 Задача удалена\n\n{task['text']}")
                return

    if data.startswith("lesson_delete:"):
        lesson_id = int(data.split(":",1)[1])
        all_data = load_data()
        user = get_user(all_data, q.from_user.id)
        for lesson in list(user["lessons"]):
            if lesson["id"] == lesson_id:
                user["lessons"].remove(lesson)
                save_data(all_data)
                cancel_job(context.application, f"lesson_{q.message.chat_id}_{lesson_id}")
                await q.edit_message_text(f"🗑 Занятие удалено\n\n{lesson['name']}")
                return


async def show_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data(); user = get_user(data, update.effective_user.id)
    tasks = [x for x in user["tasks"] if not x["done"]]
    tasks.sort(key=lambda x: (datetime.fromisoformat(x["datetime"]), x["priority"]))
    if not tasks:
        await update.message.reply_text("📭 Активных задач нет.", reply_markup=MAIN_MENU); return
    await update.message.reply_text("📋 Все активные задачи:")
    for task in tasks:
        due = datetime.fromisoformat(task["datetime"])
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Выполнено", callback_data=f"done:{task['id']}"),
            InlineKeyboardButton("🗑 Удалить", callback_data=f"delete:{task['id']}")
        ]])
        await update.message.reply_text(
            f"{PRIORITY_NAMES[task['priority']]}\n📌 {task['text']}\n"
            f"📅 {due.strftime('%d.%m.%Y')}\n🕐 {due.strftime('%H:%M')}",
            reply_markup=kb,
        )


async def show_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data=load_data(); user=get_user(data, update.effective_user.id)
    today=datetime.now(TIMEZONE).date()
    lessons=[x for x in user["lessons"] if x["weekday"]==today.weekday()]
    tasks=[]
    for x in user["tasks"]:
        if not x["done"] and datetime.fromisoformat(x["datetime"]).date()==today:
            tasks.append(x)
    text=f"📅 {WEEKDAY_NAMES[today.weekday()]}, {today.strftime('%d.%m.%Y')}\n\n🎓 ЗАНЯТИЯ\n"
    if lessons:
        for x in sorted(lessons,key=lambda z:z["time"]): text += f"• {x['time']} — {x['name']}\n"
    else: text += "Сегодня занятий нет.\n"
    text += "\n📌 ЗАДАЧИ\n"
    if tasks:
        for x in sorted(tasks,key=lambda z:(z["datetime"],z["priority"])):
            due=datetime.fromisoformat(x["datetime"])
            text += f"• {due.strftime('%H:%M')} {PRIORITY_NAMES[x['priority']]} — {x['text']}\n"
    else: text += "На сегодня задач нет."
    await update.message.reply_text(text, reply_markup=MAIN_MENU)


async def show_week(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data=load_data(); user=get_user(data, update.effective_user.id)
    today=datetime.now(TIMEZONE).date(); text="🗓 План на ближайшие 7 дней\n"; found=False
    for offset in range(7):
        day=today+timedelta(days=offset)
        lessons=[x for x in user["lessons"] if x["weekday"]==day.weekday()]
        tasks=[x for x in user["tasks"] if (not x["done"] and datetime.fromisoformat(x["datetime"]).date()==day)]
        if not lessons and not tasks: continue
        found=True; text += f"\n📅 {WEEKDAY_SHORT[day.weekday()]}, {day.strftime('%d.%m')}\n"
        for x in sorted(lessons,key=lambda z:z["time"]): text += f"🎓 {x['time']} — {x['name']}\n"
        for x in sorted(tasks,key=lambda z:(z["datetime"],z["priority"])):
            due=datetime.fromisoformat(x["datetime"])
            text += f"📌 {due.strftime('%H:%M')} {PRIORITY_NAMES[x['priority']]} — {x['text']}\n"
    if not found: text += "\nНа ближайшие 7 дней ничего не запланировано."
    await update.message.reply_text(text, reply_markup=MAIN_MENU)


async def show_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data=load_data(); user=get_user(data, update.effective_user.id)
    lessons=sorted(user["lessons"],key=lambda x:(x["weekday"],x["time"]))
    if not lessons:
        await update.message.reply_text("📚 Расписание пока пустое.\n\nНажми «➕ Добавить занятие».", reply_markup=MAIN_MENU); return
    await update.message.reply_text("📚 Расписание университета:")
    current=None
    for lesson in lessons:
        if lesson["weekday"] != current:
            current=lesson["weekday"]
            await update.message.reply_text(f"📅 {WEEKDAY_NAMES[current]}")
        kb=InlineKeyboardMarkup([[InlineKeyboardButton("🗑 Удалить занятие", callback_data=f"lesson_delete:{lesson['id']}")]])
        await update.message.reply_text(f"🕐 {lesson['time']}\n📚 {lesson['name']}", reply_markup=kb)


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text=update.message.text; flow=context.user_data.get("flow")

    if flow == "task_name":
        context.user_data["temp"]["text"] = text.strip()
        context.user_data["flow"] = "task_choose_date"
        await update.message.reply_text("Теперь выбери дату:", reply_markup=date_keyboard()); return

    if flow == "lesson_name":
        context.user_data["temp"]["name"] = text.strip()
        context.user_data["flow"] = "lesson_choose_day"
        await update.message.reply_text("📅 В какой день проходит занятие?", reply_markup=weekday_keyboard()); return

    if flow == "task_custom_date":
        d=parse_date(text)
        if not d:
            await update.message.reply_text("❌ Не понимаю дату. Например: 25.10"); return
        context.user_data["temp"]["date"] = d.isoformat()
        context.user_data["flow"] = "task_choose_time"
        await update.message.reply_text("🕐 Теперь выбери время:", reply_markup=time_keyboard("task")); return

    if flow == "task_custom_time":
        t=parse_time(text)
        if not t:
            await update.message.reply_text("❌ Используй формат HH:MM, например 17:30"); return
        context.user_data["temp"]["time"] = t.strftime("%H:%M")
        context.user_data["flow"] = "task_choose_priority"
        await update.message.reply_text("⭐ Выбери важность:", reply_markup=priority_keyboard()); return

    if flow == "lesson_custom_time":
        t=parse_time(text)
        if not t:
            await update.message.reply_text("❌ Используй формат HH:MM"); return
        temp=context.user_data["temp"]; all_data=load_data(); user=get_user(all_data, update.effective_user.id)
        lesson={"id":next_id(user["lessons"]),"name":temp["name"],"weekday":temp["weekday"],"time":t.strftime("%H:%M")}
        user["lessons"].append(lesson); save_data(all_data)
        schedule_lesson_job(context.application, update.effective_chat.id, lesson)
        context.user_data.clear()
        await update.message.reply_text(
            f"✅ Занятие добавлено!\n\n📚 {lesson['name']}\n📅 {WEEKDAY_NAMES[lesson['weekday']]}\n🕐 {lesson['time']}",
            reply_markup=MAIN_MENU,
        ); return

    if text == "➕ Добавить задачу": await begin_task(update, context); return
    if text == "➕ Добавить занятие": await begin_lesson(update, context); return
    if text == "📅 Сегодня": await show_today(update, context); return
    if text == "🗓 Неделя": await show_week(update, context); return
    if text == "📚 Расписание": await show_schedule(update, context); return
    if text == "📋 Все задачи": await show_tasks(update, context); return

    await update.message.reply_text("Выбери действие кнопкой 👇", reply_markup=MAIN_MENU)


async def post_init(application):
    await application.bot.set_my_commands([
        BotCommand("start", "Открыть главное меню"),
        BotCommand("help", "Помощь"),
    ])
    data=load_data()
    for user_id,user in data.items():
        chat_id=int(user_id)
        for task in user.get("tasks",[]): schedule_task_job(application,chat_id,task)
        for lesson in user.get("lessons",[]): schedule_lesson_job(application,chat_id,lesson)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    print("Ошибка:", context.error)


def main():
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN не задан. В Google Colab сначала сохраните токен в os.environ['BOT_TOKEN'].")
    app=(Application.builder().token(TOKEN).post_init(post_init).build())
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    app.add_error_handler(error_handler)
    print("✅ Учебный планировщик запущен!")
    app.run_polling(close_loop=False, drop_pending_updates=True)


if __name__ == "__main__":
    main()
