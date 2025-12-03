import logging

from telegram import Update, ReplyKeyboardRemove
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# импортируем токен и ID из cfg.py
from cfg import TELEGRAM_BOT_TOKEN, ADMIN_ID

# Шаги диалога
ASK_NAME, ASK_CONTACT, ASK_QUESTION = range(3)

# Логирование
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Команда /start – приветствие и начало сбора данных."""
    user_first_name = update.effective_user.first_name
    text = (
        f"Здравствуйте, {user_first_name}!\n\n"
        "Вы написали боту адвоката.\n"
        "Сейчас я задам несколько вопросов и передам их адвокату.\n\n"
        "Как к вам обращаться? (ФИО или просто имя)"
    )
    await update.message.reply_text(text)
    return ASK_NAME


async def ask_contact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Сохраняем имя и спрашиваем контакт."""
    context.user_data["name"] = update.message.text.strip()
    text = (
        "Спасибо!\n"
        "Оставьте, пожалуйста, удобный способ связи:\n"
        "• телефон\n"
        "• e-mail\n"
        "• или @username в Telegram"
    )
    await update.message.reply_text(text)
    return ASK_CONTACT


async def ask_question(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Сохраняем контакт и спрашиваем суть вопроса."""
    context.user_data["contact"] = update.message.text.strip()
    text = (
        "Опишите, пожалуйста, вашу ситуацию или вопрос. "
        "Не указывайте лишних персональных данных, только то, что необходимо."
    )
    await update.message.reply_text(text)
    return ASK_QUESTION


async def finish(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Сохраняем вопрос, отправляем маме и подтверждаем клиенту."""
    context.user_data["question"] = update.message.text.strip()

    name = context.user_data.get("name", "не указано")
    contact = context.user_data.get("contact", "не указано")
    question = context.user_data.get("question", "не указано")

    summary = (
        "📝 Новая заявка клиента:\n\n"
        f"👤 Имя: {name}\n"
        f"📞 Контакт: {contact}\n"
        f"❓ Вопрос:\n{question}"
    )

    # Отправляем маме-адвокату
    try:
        await context.bot.send_message(chat_id=int(ADMIN_ID), text=summary)
    except Exception as e:
        logger.error(f"Не удалось отправить сообщение администратору: {e}")

    # Подтверждаем пользователю
    await update.message.reply_text(
        "Спасибо! Ваш вопрос передан адвокату.\n"
        "С вами свяжутся по указанным контактам.",
        reply_markup=ReplyKeyboardRemove(),
    )

    context.user_data.clear()
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Команда /cancel – отмена диалога."""
    await update.message.reply_text(
        "Диалог отменён. Если захотите начать заново – отправьте /start.",
        reply_markup=ReplyKeyboardRemove(),
    )
    context.user_data.clear()
    return ConversationHandler.END


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /help."""
    text = (
        "Это бот адвоката.\n\n"
        "Доступные команды:\n"
        "/start – оставить обращение адвокату\n"
        "/cancel – отменить текущий диалог\n"
        "/help – показать это сообщение"
    )
    await update.message.reply_text(text)


def main() -> None:
    """Запуск бота."""
    
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("Не найден TELEGRAM_BOT_TOKEN в cfg.py")

    application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            ASK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_contact)],
            ASK_CONTACT: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_question)],
            ASK_QUESTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, finish)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("help", help_command))

    logger.info("Бот запущен")
    application.run_polling()


if __name__ == "__main__":
    main()
