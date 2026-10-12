import os
import sqlite3
import logging

from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    BotCommand,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# -------------------- Настройки --------------------

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("BOT_TOKEN")

if not TOKEN:
    raise ValueError(
        "BOT_TOKEN не найден. Добавьте токен в переменные окружения."
    )

DATA_PATH = os.getenv("RAILWAY_VOLUME_MOUNT_PATH", ".")
os.makedirs(DATA_PATH, exist_ok=True)

DB_FILE = os.path.join(DATA_PATH, "planner.db")

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
    0: "Понедельник",
    1: "Вторник",
    2: "Среда",
    3: "Четверг",
    4: "Пятница",
    5: "Суббота",
    6: "Воскресенье",
}

MAIN_MENU = ReplyKeyboardMarkup(
    [
        ["➕ Добавить задачу", "📅 Сегодня"],
        ["🗓 Неделя", "📚 Расписание"],
        ["📋 Все задачи", "➕ Добавить занятие"],
        ["📊 Статистика", "🕘 История"],
        ["⚙️ Настройки"],
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

        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_settings (
                user_id INTEGER PRIMARY KEY,
                timezone TEXT NOT NULL DEFAULT 'moscow'
            )
        """)

        conn.commit()

    logger.info("База данных инициализирована: %s", DB_FILE)


def get_user_timezone(user_id):
    with connect() as conn:
        row = conn.execute(
            "SELECT timezone FROM user_settings WHERE user_id=?",
            (user_id,),
        ).fetchone()

    if row:
        return row["timezone"]

    return None


def set_user_timezone(user_id, timezone):
    with connect() as conn:
        conn.execute("""
            INSERT INTO user_settings (user_id, timezone)
            VALUES (?, ?)
            ON CONFLICT(user_id)
            DO UPDATE SET timezone=excluded.timezone
        """, (user_id, timezone))

        conn.commit()


# -------------------- Задачи --------------------

def add_task(user_id, text, due, priority):
    with connect() as conn:
        cur = conn.execute("""
            INSERT INTO tasks
            (user_id, text, due_datetime, priority, done, created_at)
            VALUES (?, ?, ?, ?, 0, ?)
        """, (
            user_id,
            text,
            due,
            priority,
            datetime.now(MOSCOW).isoformat(),
        ))

        conn.commit()
        return cur.lastrowid


def get_task(user_id, task_id):
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM tasks WHERE user_id=? AND id=?",
            (user_id, task_id),
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
        """, (
            datetime.now(MOSCOW).isoformat(),
            user_id,
            task_id,
        ))

        conn.commit()


def delete_task(user_id, task_id):
    with connect() as conn:
        conn.execute(
            "DELETE FROM tasks WHERE user_id=? AND id=?",
            (user_id, task_id),
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


# -------------------- Расписание --------------------

def add_lesson(
    user_id,
    name,
    lesson_type,
    weekday,
    start_time,
    end_time,
):
    with connect() as conn:
        cur = conn.execute("""
            INSERT INTO lessons
            (
                user_id,
                name,
                lesson_type,
                weekday,
                start_time,
                end_time,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id,
            name,
            lesson_type,
            weekday,
            start_time,
            end_time,
            datetime.now(MOSCOW).isoformat(),
        ))

        conn.commit()
        return cur.lastrowid


def get_lesson(user_id, lesson_id):
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM lessons WHERE user_id=? AND id=?",
            (user_id, lesson_id),
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
            (user_id, lesson_id),
        )

        conn.commit()


# -------------------- Статистика --------------------

def stats(user_id):
    with connect() as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE user_id=?",
            (user_id,),
        ).fetchone()[0]

        completed = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE user_id=? AND done=1",
            (user_id,),
        ).fetchone()[0]

        active = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE user_id=? AND done=0",
            (user_id,),
        ).fetchone()[0]

        high = conn.execute(
            """
            SELECT COUNT(*)
            FROM tasks
            WHERE user_id=? AND done=0 AND priority=1
            """,
            (user_id,),
        ).fetchone()[0]

        all_lessons = conn.execute(
            "SELECT COUNT(*) FROM lessons WHERE user_id=?",
            (user_id,),
        ).fetchone()[0]

        lectures = conn.execute(
            """
            SELECT COUNT(*)
            FROM lessons
            WHERE user_id=? AND lesson_type='lecture'
            """,
            (user_id,),
        ).fetchone()[0]

        practices = conn.execute(
            """
            SELECT COUNT(*)
            FROM lessons
            WHERE user_id=? AND lesson_type='practice'
            """,
            (user_id,),
        ).fetchone()[0]

    return (
        total,
        completed,
        active,
        high,
        all_lessons,
        lectures,
        practices,
    )


# -------------------- Часовые пояса --------------------

def timezone_object(timezone):
    if timezone == "beijing":
        return BEIJING

    return MOSCOW


def timezone_name(timezone):
    if timezone == "beijing":
        return "🇨🇳 Пекин"

    return "🇷🇺 Москва"


def parse_time(value):
    try:
        return datetime.strptime(
            value.strip(),
            "%H:%M",
        ).time()

    except ValueError:
        return None


def parse_date(value, timezone):
    now = datetime.now(
        timezone_object(timezone)
    )

    for fmt in ("%d.%m.%Y", "%d.%m"):
        try:
            dt = datetime.strptime(
                value.strip(),
                fmt,
            )

            if fmt == "%d.%m":
                dt = dt.replace(
                    year=now.year
                )

                if dt.date() < now.date():
                    dt = dt.replace(
                        year=now.year + 1
                    )

            return dt.date()

        except ValueError:
            pass

    return None


def ensure_moscow(dt):
    if dt.tzinfo is None:
        return dt.replace(
            tzinfo=MOSCOW
        )

    return dt.astimezone(
        MOSCOW
    )


def display_datetime(
    dt_moscow,
    timezone,
):
    dt_moscow = ensure_moscow(
        dt_moscow
    )

    local_dt = dt_moscow.astimezone(
        timezone_object(timezone)
    )

    date_text = local_dt.strftime(
        "%d.%m.%Y %H:%M"
    )

    return (
        f"{timezone_name(timezone)}: "
        f"{date_text}"
    )


# -------------------- Работа с расписанием --------------------

def next_lesson_start(row):
    now = datetime.now(MOSCOW)

    start_time = parse_time(
        row["start_time"]
    )

    days = (
        row["weekday"]
        - now.weekday()
    ) % 7

    start = datetime.combine(
        now.date()
        + timedelta(days=days),
        start_time,
        tzinfo=MOSCOW,
    )

    if start <= now:
        start += timedelta(
            days=7
        )

    return start


def lesson_end(row, start):
    end = datetime.combine(
        start.date(),
        parse_time(
            row["end_time"]
        ),
        tzinfo=MOSCOW,
    )

    if end <= start:
        end += timedelta(
            days=1
        )

    return end


def lesson_input_to_moscow(
    weekday,
    start_time,
    end_time,
    timezone,
):
    tz = timezone_object(
        timezone
    )

    reference_monday = date(
        2024,
        1,
        1,
    )

    local_date = (
        reference_monday
        + timedelta(days=weekday)
    )

    start_local = datetime.combine(
        local_date,
        parse_time(start_time),
        tzinfo=tz,
    )

    end_local = datetime.combine(
        local_date,
        parse_time(end_time),
        tzinfo=tz,
    )

    start_moscow = (
        start_local.astimezone(
            MOSCOW
        )
    )

    end_moscow = (
        end_local.astimezone(
            MOSCOW
        )
    )

    return (
        start_moscow.weekday(),
        start_moscow.strftime("%H:%M"),
        end_moscow.strftime("%H:%M"),
    )


def lesson_for_local_date(
    row,
    local_date,
    timezone,
):
    tz = timezone_object(
        timezone
    )

    local_midnight = datetime.combine(
        local_date,
        datetime.min.time(),
        tzinfo=tz,
    )

    moscow_date = (
        local_midnight
        .astimezone(MOSCOW)
        .date()
    )

    for offset in range(-1, 3):
        candidate_date = (
            moscow_date
            + timedelta(days=offset)
        )

        if (
            candidate_date.weekday()
            != row["weekday"]
        ):
            continue

        start = datetime.combine(
            candidate_date,
            parse_time(
                row["start_time"]
            ),
            tzinfo=MOSCOW,
        )

        end = lesson_end(
            row,
            start,
        )

        if (
            start
            .astimezone(tz)
            .date()
            == local_date
        ):
            return start, end

    return None


def lesson_text(
    row,
    timezone,
    local_date=None,
):
    if local_date is None:
        start = next_lesson_start(
            row
        )

        end = lesson_end(
            row,
            start,
        )

    else:
        interval = lesson_for_local_date(
            row,
            local_date,
            timezone,
        )

        if interval is None:
            return None

        start, end = interval

    tz = timezone_object(
        timezone
    )

    local_start = (
        start.astimezone(tz)
    )

    local_end = (
        end.astimezone(tz)
    )

    weekday_text = WEEKDAY_NAMES[
        local_start.weekday()
    ]

    start_text = local_start.strftime(
        "%d.%m %H:%M"
    )

    end_text = local_end.strftime(
        "%H:%M"
    )

    return (
        f"{timezone_name(timezone)}: "
        f"{weekday_text}, "
        f"{start_text}–{end_text}"
    )


# -------------------- Кнопки --------------------

def timezone_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🇷🇺 Москва",
                    callback_data=(
                        "timezone:moscow"
                    ),
                ),
                InlineKeyboardButton(
                    "🇨🇳 Пекин",
                    callback_data=(
                        "timezone:beijing"
                    ),
                ),
            ]
        ]
    )


def date_keyboard(timezone):
    today = datetime.now(
        timezone_object(timezone)
    ).date()

    rows = []

    for i in range(7):
        current_date = (
            today
            + timedelta(days=i)
        )

        rows.append(
            [
                InlineKeyboardButton(
                    current_date.strftime(
                        "%d.%m"
                    ),
                    callback_data=(
                        "task_date:"
                        + current_date.isoformat()
                    ),
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                "📅 Другая дата",
                callback_data=(
                    "task_date:custom"
                ),
            )
        ]
    )

    rows.append(
        [
            InlineKeyboardButton(
                "❌ Отмена",
                callback_data="cancel",
            )
        ]
    )

    return InlineKeyboardMarkup(
        rows
    )


def time_keyboard(prefix):
    values = [
        "08:00",
        "10:00",
        "12:00",
        "14:00",
        "16:00",
        "18:00",
        "20:00",
    ]

    rows = []

    for i in range(
        0,
        len(values),
        2,
    ):
        current_row = []

        for value in values[
            i:i + 2
        ]:
            current_row.append(
                InlineKeyboardButton(
                    value,
                    callback_data=(
                        prefix
                        + "_time:"
                        + value
                    ),
                )
            )

        rows.append(
            current_row
        )

    rows.append(
        [
            InlineKeyboardButton(
                "🕐 Другое время",
                callback_data=(
                    prefix
                    + "_time:custom"
                ),
            )
        ]
    )

    rows.append(
        [
            InlineKeyboardButton(
                "❌ Отмена",
                callback_data="cancel",
            )
        ]
    )

    return InlineKeyboardMarkup(
        rows
    )


def priority_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🔴 Высокая",
                    callback_data=(
                        "priority:1"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    "🟡 Средняя",
                    callback_data=(
                        "priority:2"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    "🟢 Низкая",
                    callback_data=(
                        "priority:3"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    "❌ Отмена",
                    callback_data="cancel",
                )
            ],
        ]
    )


def lesson_type_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "📖 Лекция",
                    callback_data=(
                        "lesson_type:lecture"
                    ),
                ),
                InlineKeyboardButton(
                    "✏️ Практика",
                    callback_data=(
                        "lesson_type:practice"
                    ),
                ),
            ],
            [
                InlineKeyboardButton(
                    "❌ Отмена",
                    callback_data="cancel",
                )
            ],
        ]
    )


def weekday_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Пн",
                    callback_data=(
                        "lesson_day:0"
                    ),
                ),
                InlineKeyboardButton(
                    "Вт",
                    callback_data=(
                        "lesson_day:1"
                    ),
                ),
                InlineKeyboardButton(
                    "Ср",
                    callback_data=(
                        "lesson_day:2"
                    ),
                ),
            ],
            [
                InlineKeyboardButton(
                    "Чт",
                    callback_data=(
                        "lesson_day:3"
                    ),
                ),
                InlineKeyboardButton(
                    "Пт",
                    callback_data=(
                        "lesson_day:4"
                    ),
                ),
                InlineKeyboardButton(
                    "Сб",
                    callback_data=(
                        "lesson_day:5"
                    ),
                ),
            ],
            [
                InlineKeyboardButton(
                    "Вс",
                    callback_data=(
                        "lesson_day:6"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    "❌ Отмена",
                    callback_data="cancel",
                )
            ],
        ]
    )


# -------------------- Start --------------------

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    context.user_data.clear()

    user_id = (
        update.effective_user.id
    )

    timezone = get_user_timezone(
        user_id
    )

    if timezone is None:
        await update.message.reply_text(
            "👋 Добро пожаловать "
            "в учебный планировщик!\n\n"
            "Сначала выбери, "
            "какое время тебе показывать:",
            reply_markup=(
                timezone_keyboard()
            ),
        )

        return

    timezone_text = (
        timezone_name(timezone)
    )

    await update.message.reply_text(
        "👋 Учебный планировщик\n\n"
        f"🕐 Выбранное время: "
        f"{timezone_text}",
        reply_markup=MAIN_MENU,
    )


async def show_settings(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    timezone = (
        get_user_timezone(
            update.effective_user.id
        )
        or "moscow"
    )

    timezone_text = (
        timezone_name(timezone)
    )

    await update.message.reply_text(
        "⚙️ Настройки\n\n"
        f"Сейчас выбрано: "
        f"{timezone_text}\n\n"
        "Выбери часовой пояс:",
        reply_markup=(
            timezone_keyboard()
        ),
    )


async def begin_task(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    context.user_data.clear()

    context.user_data[
        "flow"
    ] = "task_name"

    context.user_data[
        "temp"
    ] = {}

    timezone = (
        get_user_timezone(
            update.effective_user.id
        )
        or "moscow"
    )

    timezone_text = (
        timezone_name(timezone)
    )

    await update.message.reply_text(
        "Напиши название задачи:\n\n"
        f"🕐 Время: {timezone_text}",
        reply_markup=(
            ReplyKeyboardRemove()
        ),
    )


async def begin_lesson(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    context.user_data.clear()

    context.user_data[
        "flow"
    ] = "lesson_name"

    context.user_data[
        "temp"
    ] = {}

    timezone = (
        get_user_timezone(
            update.effective_user.id
        )
        or "moscow"
    )

    timezone_text = (
        timezone_name(timezone)
    )

    await update.message.reply_text(
        "Напиши название предмета:\n\n"
        f"🕐 Время: {timezone_text}",
        reply_markup=(
            ReplyKeyboardRemove()
        ),
    )


# -------------------- Callback --------------------

async def callbacks(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    q = update.callback_query

    await q.answer()

    data = q.data


    if data.startswith(
        "timezone:"
    ):
        timezone = data.split(
            ":",
            1,
        )[1]

        set_user_timezone(
            q.from_user.id,
            timezone,
        )

        context.user_data.clear()

        timezone_text = (
            timezone_name(
                timezone
            )
        )

        await q.message.reply_text(
            f"✅ Выбрано время: "
            f"{timezone_text}",
            reply_markup=MAIN_MENU,
        )

        return


    if data == "cancel":
        context.user_data.clear()

        await q.message.reply_text(
            "❌ Отменено.",
            reply_markup=MAIN_MENU,
        )

        return


    if data.startswith(
        "task_date:"
    ):
        value = data.split(
            ":",
            1,
        )[1]

        if value == "custom":
            context.user_data[
                "flow"
            ] = "task_custom_date"

            await q.message.reply_text(
                "Введи дату: "
                "25.10 или 25.10.2026"
            )

            return

        context.user_data[
            "temp"
        ]["date"] = value

        await q.message.reply_text(
            "Выбери время:",
            reply_markup=(
                time_keyboard(
                    "task"
                )
            ),
        )

        return


    if data.startswith(
        "task_time:"
    ):
        value = data.split(
            ":",
            1,
        )[1]

        if value == "custom":
            context.user_data[
                "flow"
            ] = "task_custom_time"

            await q.message.reply_text(
                "Введи время, "
                "например 17:30"
            )

            return

        context.user_data[
            "temp"
        ]["time"] = value

        await q.message.reply_text(
            "Выбери важность:",
            reply_markup=(
                priority_keyboard()
            ),
        )

        return


    if data.startswith(
        "priority:"
    ):
        priority = int(
            data.split(
                ":",
                1,
            )[1]
        )

        temp = (
            context.user_data[
                "temp"
            ]
        )

        timezone = (
            get_user_timezone(
                q.from_user.id
            )
            or "moscow"
        )

        tz = timezone_object(
            timezone
        )

        selected_date = (
            datetime
            .fromisoformat(
                temp["date"]
            )
            .date()
        )

        selected_time = (
            parse_time(
                temp["time"]
            )
        )

        due_local = (
            datetime.combine(
                selected_date,
                selected_time,
                tzinfo=tz,
            )
        )

        if (
            due_local
            <= datetime.now(tz)
        ):
            await q.message.reply_text(
                "❌ Это время уже прошло."
            )

            return

        due_moscow = (
            due_local.astimezone(
                MOSCOW
            )
        )

        try:
            task_id = add_task(
                q.from_user.id,
                temp["text"],
                due_moscow.isoformat(),
                priority,
            )

            shown_time = (
                display_datetime(
                    due_moscow,
                    timezone,
                )
            )

            task_text = (
                temp["text"]
            )

            priority_text = (
                PRIORITY_NAMES[
                    priority
                ]
            )

            context.user_data.clear()

            await q.message.reply_text(
                "✅ Задача сохранена!\n\n"
                f"#{task_id} "
                f"{task_text}\n"
                f"{priority_text}\n\n"
                f"{shown_time}",
                reply_markup=MAIN_MENU,
            )

        except sqlite3.Error:
            logger.exception(
                "Ошибка сохранения "
                "задачи"
            )

            await q.message.reply_text(
                "⚠️ Не удалось "
                "сохранить задачу."
            )

        return


    if data.startswith(
        "lesson_type:"
    ):
        lesson_type = data.split(
            ":",
            1,
        )[1]

        context.user_data[
            "temp"
        ]["lesson_type"] = (
            lesson_type
        )

        await q.message.reply_text(
            "Выбери день недели:",
            reply_markup=(
                weekday_keyboard()
            ),
        )

        return


    if data.startswith(
        "lesson_day:"
    ):
        weekday = int(
            data.split(
                ":",
                1,
            )[1]
        )

        context.user_data[
            "temp"
        ]["weekday"] = (
            weekday
        )

        await q.message.reply_text(
            "Выбери время начала:",
            reply_markup=(
                time_keyboard(
                    "lesson_start"
                )
            ),
        )

        return


    if data.startswith(
        "lesson_start_time:"
    ):
        value = data.split(
            ":",
            1,
        )[1]

        if value == "custom":
            context.user_data[
                "flow"
            ] = (
                "lesson_custom_start"
            )

            await q.message.reply_text(
                "Введи время начала, "
                "например 13:30"
            )

            return

        context.user_data[
            "temp"
        ]["start_time"] = (
            value
        )

        await q.message.reply_text(
            "До скольки идёт "
            "занятие?",
            reply_markup=(
                time_keyboard(
                    "lesson_end"
                )
            ),
        )

        return


    if data.startswith(
        "lesson_end_time:"
    ):
        value = data.split(
            ":",
            1,
        )[1]

        if value == "custom":
            context.user_data[
                "flow"
            ] = (
                "lesson_custom_end"
            )

            await q.message.reply_text(
                "Введи время окончания, "
                "например 15:30"
            )

            return

        temp = (
            context.user_data[
                "temp"
            ]
        )

        if (
            parse_time(value)
            <= parse_time(
                temp[
                    "start_time"
                ]
            )
        ):
            await q.message.reply_text(
                "❌ Окончание должно "
                "быть позже начала."
            )

            return

        timezone = (
            get_user_timezone(
                q.from_user.id
            )
            or "moscow"
        )

        (
            moscow_weekday,
            moscow_start,
            moscow_end,
        ) = lesson_input_to_moscow(
            temp["weekday"],
            temp["start_time"],
            value,
            timezone,
        )

        try:
            lesson_id = add_lesson(
                q.from_user.id,
                temp["name"],
                temp["lesson_type"],
                moscow_weekday,
                moscow_start,
                moscow_end,
            )

            row = get_lesson(
                q.from_user.id,
                lesson_id,
            )

            shown_lesson = (
                lesson_text(
                    row,
                    timezone,
                )
            )

            lesson_name = (
                row["name"]
            )

            lesson_type = (
                LESSON_TYPES[
                    row[
                        "lesson_type"
                    ]
                ]
            )

            context.user_data.clear()

            await q.message.reply_text(
                "✅ Занятие сохранено!\n\n"
                f"📚 {lesson_name}\n"
                f"{lesson_type}\n\n"
                f"{shown_lesson}",
                reply_markup=MAIN_MENU,
            )

        except sqlite3.Error:
            logger.exception(
                "Ошибка сохранения "
                "занятия"
            )

            await q.message.reply_text(
                "⚠️ Не удалось "
                "сохранить занятие."
            )

        return


    if data.startswith(
        "done:"
    ):
        task_id = int(
            data.split(
                ":",
                1,
            )[1]
        )

        row = get_task(
            q.from_user.id,
            task_id,
        )

        if row:
            complete_task(
                q.from_user.id,
                task_id,
            )

            task_text = (
                row["text"]
            )

            await q.edit_message_text(
                "✅ Выполнено\n\n"
                f"{task_text}"
            )

        return


    if data.startswith(
        "delete:"
    ):
        task_id = int(
            data.split(
                ":",
                1,
            )[1]
        )

        row = get_task(
            q.from_user.id,
            task_id,
        )

        if row:
            delete_task(
                q.from_user.id,
                task_id,
            )

            task_text = (
                row["text"]
            )

            await q.edit_message_text(
                "🗑 Задача удалена\n\n"
                f"{task_text}"
            )

        return


    if data.startswith(
        "lesson_delete:"
    ):
        lesson_id = int(
            data.split(
                ":",
                1,
            )[1]
        )

        row = get_lesson(
            q.from_user.id,
            lesson_id,
        )

        if row:
            delete_lesson(
                q.from_user.id,
                lesson_id,
            )

            lesson_name = (
                row["name"]
            )

            await q.edit_message_text(
                "🗑 Занятие удалено\n\n"
                f"{lesson_name}"
            )

        return


# -------------------- Text handler --------------------

async def text_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    text = (
        update.message.text.strip()
    )

    user_id = (
        update.effective_user.id
    )

    timezone = (
        get_user_timezone(
            user_id
        )
        or "moscow"
    )

    flow = (
        context.user_data.get(
            "flow"
        )
    )


    if flow == "task_name":
        context.user_data[
            "temp"
        ]["text"] = text

        await update.message.reply_text(
            "Выбери дату:",
            reply_markup=(
                date_keyboard(
                    timezone
                )
            ),
        )

        return


    if flow == "lesson_name":
        context.user_data[
            "temp"
        ]["name"] = text

        await update.message.reply_text(
            "Выбери тип:",
            reply_markup=(
                lesson_type_keyboard()
            ),
        )

        return


    if flow == "task_custom_date":
        selected_date = parse_date(
            text,
            timezone,
        )

        if not selected_date:
            await update.message.reply_text(
                "❌ Неверная дата.\n"
                "Пример: "
                "25.10 или 25.10.2026"
            )

            return

        context.user_data[
            "temp"
        ]["date"] = (
            selected_date.isoformat()
        )

        await update.message.reply_text(
            "Выбери время:",
            reply_markup=(
                time_keyboard(
                    "task"
                )
            ),
        )

        return


    if flow == "task_custom_time":
        selected_time = (
            parse_time(text)
        )

        if not selected_time:
            await update.message.reply_text(
                "❌ Формат HH:MM.\n"
                "Например: 17:30"
            )

            return

        context.user_data[
            "temp"
        ]["time"] = (
            selected_time.strftime(
                "%H:%M"
            )
        )

        await update.message.reply_text(
            "Выбери важность:",
            reply_markup=(
                priority_keyboard()
            ),
        )

        return


    if flow == "lesson_custom_start":
        selected_time = (
            parse_time(text)
        )

        if not selected_time:
            await update.message.reply_text(
                "❌ Формат HH:MM."
            )

            return

        context.user_data[
            "temp"
        ]["start_time"] = (
            selected_time.strftime(
                "%H:%M"
            )
        )

        await update.message.reply_text(
            "До скольки идёт "
            "занятие?",
            reply_markup=(
                time_keyboard(
                    "lesson_end"
                )
            ),
        )

        return


    if flow == "lesson_custom_end":
        end = parse_time(text)

        if not end:
            await update.message.reply_text(
                "❌ Формат HH:MM."
            )

            return

        temp = (
            context.user_data[
                "temp"
            ]
        )

        if (
            end
            <= parse_time(
                temp[
                    "start_time"
                ]
            )
        ):
            await update.message.reply_text(
                "❌ Окончание должно "
                "быть позже начала."
            )

            return

        (
            moscow_weekday,
            moscow_start,
            moscow_end,
        ) = lesson_input_to_moscow(
            temp["weekday"],
            temp["start_time"],
            end.strftime("%H:%M"),
            timezone,
        )

        lesson_id = add_lesson(
            user_id,
            temp["name"],
            temp["lesson_type"],
            moscow_weekday,
            moscow_start,
            moscow_end,
        )

        row = get_lesson(
            user_id,
            lesson_id,
        )

        shown_lesson = (
            lesson_text(
                row,
                timezone,
            )
        )

        lesson_name = (
            row["name"]
        )

        lesson_type = (
            LESSON_TYPES[
                row[
                    "lesson_type"
                ]
            ]
        )

        context.user_data.clear()

        await update.message.reply_text(
            "✅ Занятие сохранено!\n\n"
            f"📚 {lesson_name}\n"
            f"{lesson_type}\n\n"
            f"{shown_lesson}",
            reply_markup=MAIN_MENU,
        )

        return


    if text == "➕ Добавить задачу":
        await begin_task(
            update,
            context,
        )

    elif text == "➕ Добавить занятие":
        await begin_lesson(
            update,
            context,
        )

    elif text == "📋 Все задачи":
        await show_tasks(
            update
        )

    elif text == "📚 Расписание":
        await show_schedule(
            update
        )

    elif text == "📅 Сегодня":
        await show_today(
            update
        )

    elif text == "🗓 Неделя":
        await show_week(
            update
        )

    elif text == "📊 Статистика":
        await show_stats(
            update
        )

    elif text == "🕘 История":
        await show_history(
            update
        )

    elif text == "⚙️ Настройки":
        await show_settings(
            update,
            context,
        )

    else:
        await update.message.reply_text(
            "Выбери действие "
            "кнопкой 👇",
            reply_markup=MAIN_MENU,
        )


# -------------------- Все задачи --------------------

async def show_tasks(
    update: Update,
):
    user_id = (
        update.effective_user.id
    )

    timezone = (
        get_user_timezone(
            user_id
        )
        or "moscow"
    )

    rows = active_tasks(
        user_id
    )

    if not rows:
        await update.message.reply_text(
            "📭 Активных задач нет.",
            reply_markup=MAIN_MENU,
        )

        return

    await update.message.reply_text(
        "📋 Все активные задачи:"
    )

    for row in rows:
        due = ensure_moscow(
            datetime.fromisoformat(
                row[
                    "due_datetime"
                ]
            )
        )

        shown_time = (
            display_datetime(
                due,
                timezone,
            )
        )

        priority_text = (
            PRIORITY_NAMES[
                row["priority"]
            ]
        )

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "✅ Выполнено",
                        callback_data=(
                            "done:"
                            + str(
                                row["id"]
                            )
                        ),
                    ),
                    InlineKeyboardButton(
                        "🗑 Удалить",
                        callback_data=(
                            "delete:"
                            + str(
                                row["id"]
                            )
                        ),
                    ),
                ]
            ]
        )

        await update.message.reply_text(
            f"{priority_text}\n"
            f"📌 {row['text']}\n\n"
            f"{shown_time}",
            reply_markup=keyboard,
        )


# -------------------- Расписание --------------------

async def show_schedule(
    update: Update,
):
    user_id = (
        update.effective_user.id
    )

    timezone = (
        get_user_timezone(
            user_id
        )
        or "moscow"
    )

    rows = lessons(
        user_id
    )

    if not rows:
        await update.message.reply_text(
            "📚 Расписание пустое.",
            reply_markup=MAIN_MENU,
        )

        return

    rows = sorted(
        rows,
        key=next_lesson_start,
    )

    timezone_text = (
        timezone_name(
            timezone
        )
    )

    await update.message.reply_text(
        "📚 Расписание\n"
        f"🕐 {timezone_text}"
    )

    for row in rows:
        shown_lesson = (
            lesson_text(
                row,
                timezone,
            )
        )

        lesson_type = (
            LESSON_TYPES[
                row[
                    "lesson_type"
                ]
            ]
        )

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "🗑 Удалить занятие",
                        callback_data=(
                            "lesson_delete:"
                            + str(
                                row["id"]
                            )
                        ),
                    )
                ]
            ]
        )

        await update.message.reply_text(
            f"📚 {row['name']}\n"
            f"{lesson_type}\n\n"
            f"{shown_lesson}",
            reply_markup=keyboard,
        )


# -------------------- Сегодня --------------------

async def show_today(
    update: Update,
):
    user_id = (
        update.effective_user.id
    )

    timezone = (
        get_user_timezone(
            user_id
        )
        or "moscow"
    )

    tz = timezone_object(
        timezone
    )

    today = datetime.now(
        tz
    ).date()

    task_rows = []

    for row in active_tasks(
        user_id
    ):
        due = ensure_moscow(
            datetime.fromisoformat(
                row[
                    "due_datetime"
                ]
            )
        )

        if (
            due.astimezone(
                tz
            ).date()
            == today
        ):
            task_rows.append(
                row
            )

    lesson_rows = []

    for row in lessons(
        user_id
    ):
        info = lesson_text(
            row,
            timezone,
            today,
        )

        if info:
            lesson_rows.append(
                (
                    row,
                    info,
                )
            )

    timezone_text = (
        timezone_name(
            timezone
        )
    )

    text = (
        f"📅 Сегодня, "
        f"{today.strftime('%d.%m.%Y')}\n"
        f"🕐 {timezone_text}\n\n"
        "🎓 ЗАНЯТИЯ\n"
    )

    if lesson_rows:
        for row, info in lesson_rows:
            lesson_type = (
                LESSON_TYPES[
                    row[
                        "lesson_type"
                    ]
                ]
            )

            text += (
                f"\n{row['name']} — "
                f"{lesson_type}\n"
                f"{info}\n"
            )

    else:
        text += "Нет занятий.\n"

    text += "\n📌 ЗАДАЧИ\n"

    if task_rows:
        for row in task_rows:
            due = ensure_moscow(
                datetime.fromisoformat(
                    row[
                        "due_datetime"
                    ]
                )
            )

            shown_time = (
                display_datetime(
                    due,
                    timezone,
                )
            )

            priority_text = (
                PRIORITY_NAMES[
                    row["priority"]
                ]
            )

            text += (
                f"\n{priority_text} "
                f"{row['text']}\n"
                f"{shown_time}\n"
            )

    else:
        text += "Нет задач."

    await update.message.reply_text(
        text,
        reply_markup=MAIN_MENU,
    )


# -------------------- Неделя --------------------

async def show_week(
    update: Update,
):
    user_id = (
        update.effective_user.id
    )

    timezone = (
        get_user_timezone(
            user_id
        )
        or "moscow"
    )

    tz = timezone_object(
        timezone
    )

    start = datetime.now(
        tz
    ).date()

    task_rows = active_tasks(
        user_id
    )

    lesson_rows = lessons(
        user_id
    )

    timezone_text = (
        timezone_name(
            timezone
        )
    )

    text = (
        "🗓 План на 7 дней\n"
        f"🕐 {timezone_text}\n"
    )

    found = False

    for offset in range(7):
        day = (
            start
            + timedelta(
                days=offset
            )
        )

        day_tasks = []

        for row in task_rows:
            due = ensure_moscow(
                datetime.fromisoformat(
                    row[
                        "due_datetime"
                    ]
                )
            )

            if (
                due.astimezone(
                    tz
                ).date()
                == day
            ):
                day_tasks.append(
                    row
                )

        day_lessons = []

        for row in lesson_rows:
            info = lesson_text(
                row,
                timezone,
                day,
            )

            if info:
                day_lessons.append(
                    (
                        row,
                        info,
                    )
                )

        if (
            not day_tasks
            and not day_lessons
        ):
            continue

        found = True

        weekday_text = (
            WEEKDAY_NAMES[
                day.weekday()
            ]
        )

        day_text = day.strftime(
            "%d.%m"
        )

        text += (
            f"\n📅 {weekday_text}, "
            f"{day_text}\n"
        )

        for row, info in day_lessons:
            lesson_type = (
                LESSON_TYPES[
                    row[
                        "lesson_type"
                    ]
                ]
            )

            text += (
                f"🎓 {row['name']} — "
                f"{lesson_type}\n"
                f"{info}\n"
            )

        for row in day_tasks:
            due = ensure_moscow(
                datetime.fromisoformat(
                    row[
                        "due_datetime"
                    ]
                )
            )

            shown_time = (
                display_datetime(
                    due,
                    timezone,
                )
            )

            priority_text = (
                PRIORITY_NAMES[
                    row["priority"]
                ]
            )

            text += (
                f"📌 {row['text']} — "
                f"{priority_text}\n"
                f"{shown_time}\n"
            )

    if not found:
        text += (
            "\nНичего не запланировано."
        )

    await update.message.reply_text(
        text,
        reply_markup=MAIN_MENU,
    )


# -------------------- Статистика --------------------

async def show_stats(
    update: Update,
):
    (
        total,
        completed,
        active,
        high,
        lesson_count,
        lectures,
        practices,
    ) = stats(
        update.effective_user.id
    )

    await update.message.reply_text(
        "📊 Статистика\n\n"
        f"Всего задач: {total}\n"
        f"✅ Выполнено: {completed}\n"
        f"⏳ Активных: {active}\n"
        f"🔴 Высокий приоритет: "
        f"{high}\n\n"
        f"🎓 Всего занятий: "
        f"{lesson_count}\n"
        f"📖 Лекций: "
        f"{lectures}\n"
        f"✏️ Практик: "
        f"{practices}",
        reply_markup=MAIN_MENU,
    )


# -------------------- История --------------------

async def show_history(
    update: Update,
):
    user_id = (
        update.effective_user.id
    )

    timezone = (
        get_user_timezone(
            user_id
        )
        or "moscow"
    )

    tz = timezone_object(
        timezone
    )

    rows = history(
        user_id
    )

    if not rows:
        await update.message.reply_text(
            "🕘 История пока пустая.",
            reply_markup=MAIN_MENU,
        )

        return

    text = (
        "🕘 Последние выполненные "
        "задачи\n"
    )

    for row in rows:
        completed = ensure_moscow(
            datetime.fromisoformat(
                row[
                    "completed_at"
                ]
            )
        )

        completed_local = (
            completed.astimezone(
                tz
            )
        )

        completed_text = (
            completed_local.strftime(
                "%d.%m.%Y %H:%M"
            )
        )

        text += (
            f"\n✅ {row['text']}\n"
            f"{completed_text}\n"
        )

    await update.message.reply_text(
        text,
        reply_markup=MAIN_MENU,
    )


# -------------------- Ошибки --------------------

async def error_handler(
    update,
    context,
):
    logger.error(
        "Произошла ошибка",
        exc_info=context.error,
    )

    if (
        isinstance(
            update,
            Update,
        )
        and update.effective_message
    ):
        try:
            await (
                update
                .effective_message
                .reply_text(
                    "⚠️ Произошла ошибка. "
                    "Попробуй ещё раз."
                )
            )

        except Exception:
            logger.exception(
                "Не удалось отправить "
                "сообщение об ошибке"
            )


# -------------------- Запуск --------------------

async def post_init(
    application,
):
    init_db()

    await application.bot.set_my_commands(
        [
            BotCommand(
                "start",
                "Открыть главное меню",
            ),
            BotCommand(
                "settings",
                "Настройки часового пояса",
            ),
        ]
    )

    logger.info(
        "Telegram-бот успешно запущен"
    )


application = (
    Application.builder()
    .token(TOKEN)
    .post_init(post_init)
    .build()
)

application.add_handler(
    CommandHandler(
        "start",
        start,
    )
)

application.add_handler(
    CommandHandler(
        "settings",
        show_settings,
    )
)

application.add_handler(
    CallbackQueryHandler(
        callbacks
    )
)

application.add_handler(
    MessageHandler(
        filters.TEXT
        & ~filters.COMMAND,
        text_handler,
    )
)

application.add_error_handler(
    error_handler
)


if __name__ == "__main__":
    logger.info(
        "Запуск LAB 3..."
    )

    logger.info(
        "Источник данных: SQLite"
    )

    logger.info(
        "База данных: %s",
        DB_FILE,
    )

    application.run_polling(
        drop_pending_updates=True
    )
