            priority_text = PRIORITY_NAMES[row["priority"]]

            text += (
                "📌 "
                + row["text"]
                + " — "
                + priority_text
                + "\n"
                + shown_time
                + "\n"
            )

    if not found:
        text += "\nНичего не запланировано."

    await update.message.reply_text(
        text,
        reply_markup=OVERVIEW_MENU,
    )


# -------------------- Статистика --------------------

async def show_stats(update: Update):
    total, completed, active, high, lesson_count, lectures, practices = stats(
        update.effective_user.id
    )

    await update.message.reply_text(
        "📊 Статистика\n\n"
        "Всего задач: " + str(total) + "\n"
        "✅ Выполнено: " + str(completed) + "\n"
        "⏳ Активных: " + str(active) + "\n"
        "🔴 Высокий приоритет: " + str(high) + "\n\n"
        "🎓 Всего занятий: " + str(lesson_count) + "\n"
        "📖 Лекций: " + str(lectures) + "\n"
        "✏️ Практик: " + str(practices),
        reply_markup=OVERVIEW_MENU,
    )


# -------------------- История --------------------

async def show_history(update: Update):
    user_id = update.effective_user.id
    mode = get_user_timezone(user_id) or "moscow"
    rows = history(user_id)

    if not rows:
        await update.message.reply_text(
            "🕘 История пока пустая.",
            reply_markup=TASK_MENU,
        )
        return

    text = "🕘 Последние выполненные задачи\n"

    for row in rows:
        completed = ensure_moscow(datetime.fromisoformat(row["completed_at"]))
        completed_text = display_datetime(completed, mode)

        text += "\n✅ " + row["text"] + "\n" + completed_text + "\n"

    await update.message.reply_text(
        text,
        reply_markup=TASK_MENU,
    )


# -------------------- Ошибки --------------------

async def error_handler(update, context):
    logger.error(
        "Произошла ошибка",
        exc_info=context.error,
    )

    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "⚠️ Произошла ошибка. Попробуй ещё раз."
            )
        except Exception:
            logger.exception("Не удалось отправить сообщение об ошибке")


# -------------------- Запуск --------------------

async def post_init(application):
    init_db()

    await application.bot.set_my_commands(
        [
            BotCommand("start", "Открыть главное меню"),
            BotCommand("settings", "Настройки времени"),
        ]
    )

    logger.info("Telegram-бот успешно запущен")


application = (
    Application.builder()
    .token(TOKEN)
    .post_init(post_init)
    .build()
)

application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("settings", show_settings))
application.add_handler(CallbackQueryHandler(callbacks))
application.add_handler(
    MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        text_handler,
    )
)
application.add_error_handler(error_handler)


if __name__ == "__main__":
    logger.info("Запуск LAB 3...")
    logger.info("Источник данных: SQLite")
    logger.info("База данных: %s", DB_FILE)

    application.run_polling(
        drop_pending_updates=True
    )
