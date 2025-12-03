import logging
import re
import sqlite3
from datetime import datetime, timedelta
from html import unescape
from pathlib import Path
from typing import Dict, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from cfg import TELEGRAM_BOT_TOKEN, ADMIN_ID

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [["ℹ️ О нас", "✉️ Оставить обращение"]], resize_keyboard=True
)
ABOUT_URL = "http://advpankratova.ru/"
DB_PATH = Path("DataBase") / "advbot.db"

TIME_SLOTS = [
    "08:00-10:00",
    "10:00-12:00",
    "12:00-14:00",
    "16:00-18:00",
    "18:00-20:00",
    "20:00-22:00",
]

ARTICLE_OPTIONS = ["228", "159", "158", "105", "Другая"]

ABOUT_CACHE: Optional[str] = None


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS emergency_calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                username TEXT,
                full_name TEXT,
                phone TEXT,
                address TEXT,
                coordinates TEXT,
                article TEXT,
                created_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS consultations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                username TEXT,
                full_name TEXT,
                city TEXT,
                phone TEXT,
                urgency TEXT,
                article TEXT,
                description TEXT,
                preferred_date TEXT,
                preferred_time TEXT,
                created_at TEXT
            )
            """
        )


def fetch_about_info() -> str:
    global ABOUT_CACHE
    if ABOUT_CACHE:
        return ABOUT_CACHE

    description = None
    try:
        request = Request(ABOUT_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(request, timeout=10) as response:
            html = response.read().decode("utf-8", errors="ignore")
        meta_match = re.search(
            r'<meta[^>]+name=["\']description["\'][^>]*content=["\'](.*?)["\']',
            html,
            re.IGNORECASE | re.DOTALL,
        )
        if meta_match:
            description = unescape(meta_match.group(1)).strip()
    except (HTTPError, URLError, TimeoutError) as exc:  # pragma: no cover - сеть
        logger.warning("Не удалось получить данные с сайта: %s", exc)

    ABOUT_CACHE = (
        description
        or "Адвокат Панкратова А.В. предоставляет квалифицированную правовую помощь,"
        " работает с уголовными и гражданскими делами и сопровождает клиентов на всех стадиях защиты."
    )
    return ABOUT_CACHE


def checkbox(value: Optional[str]) -> str:
    return "✅" if value else "⬜️"


def user_link(update: Update) -> str:
    user = update.effective_user
    display_name = user.full_name or user.first_name or "пользователь"
    return f"<a href=\"tg://user?id={user.id}\">{display_name}</a>"


def show_requests_menu(update: Update, text: str) -> None:
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🚨 Экстренный вызов", callback_data="emergency_open")],
            [InlineKeyboardButton("📨 Обратиться к адвокату", callback_data="consult_open")],
        ]
    )
    if update.callback_query:
        update.callback_query.answer()
        update.callback_query.edit_message_text(text, reply_markup=keyboard)
    else:
        update.message.reply_text(text, reply_markup=keyboard)


def emergency_summary(data: Dict[str, Optional[str]]) -> str:
    return (
        "🚨 Экстренный вызов\n\n"
        f"Номер: {data.get('phone') or 'не указан'}\n"
        f"Адрес/координаты: {data.get('address') or data.get('coordinates') or 'не указаны'}\n"
        f"Статья: {data.get('article') or 'не указана'}\n\n"
        "Выберите, что добавить или отправьте заявку."
    )


def emergency_keyboard(data: Dict[str, Optional[str]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    f"{checkbox(data.get('phone'))} Указать номер", callback_data="emergency_phone"
                )
            ],
            [
                InlineKeyboardButton(
                    f"{checkbox(data.get('address') or data.get('coordinates'))} Указать адрес",
                    callback_data="emergency_address",
                )
            ],
            [
                InlineKeyboardButton(
                    f"{checkbox(data.get('article'))} Указать статью",
                    callback_data="emergency_article_menu",
                )
            ],
            [InlineKeyboardButton("📤 Отправить экстренный вызов", callback_data="emergency_submit")],
            [InlineKeyboardButton("⬅️ Вернуться к выбору", callback_data="back_to_requests")],
        ]
    )


def article_keyboard(prefix: str) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(option, callback_data=f"{prefix}_{option}")]
        for option in ARTICLE_OPTIONS
    ]
    buttons.append([InlineKeyboardButton("⬅️ Назад", callback_data=f"{prefix}_back")])
    return InlineKeyboardMarkup(buttons)


def consultation_date_keyboard() -> InlineKeyboardMarkup:
    today = datetime.now().date()
    options = [today + timedelta(days=i) for i in range(0, 5)]
    buttons = [
        [
            InlineKeyboardButton(
                date.strftime("%d.%m (%A)"), callback_data=f"consult_date_{date.isoformat()}"
            )
        ]
        for date in options
    ]
    return InlineKeyboardMarkup(buttons)


def consultation_time_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(slot, callback_data=f"consult_time_{slot}")] for slot in TIME_SLOTS]
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.clear()
    greeting = (
        "Здравствуйте! Вас приветствует официальный бот для связи с адвокатом Панкратовой А.В.\n\n"
        "Выберите действие ниже."
    )
    await update.message.reply_text(greeting, reply_markup=MAIN_KEYBOARD)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "Доступные действия:\n"
        "• ℹ️ О нас – краткая информация об адвокате.\n"
        "• ✉️ Оставить обращение – экстренный вызов или заявка на консультацию.\n"
        "Команда /start возвращает основное меню."
    )
    await update.message.reply_text(text, reply_markup=MAIN_KEYBOARD)


async def handle_main_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text.strip()
    if text == "ℹ️ О нас":
        about = fetch_about_info()
        await update.message.reply_text(about, reply_markup=MAIN_KEYBOARD)
    elif text == "✉️ Оставить обращение":
        show_requests_menu(update, "Выберите формат обращения:")
    else:
        await update.message.reply_text(
            "Пожалуйста, воспользуйтесь кнопками меню или командой /help.",
            reply_markup=MAIN_KEYBOARD,
        )


async def open_emergency(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    query.answer()
    context.user_data["emergency"] = {"phone": None, "address": None, "coordinates": None, "article": None}
    context.user_data.pop("flow", None)
    query.edit_message_text(
        emergency_summary(context.user_data["emergency"]),
        reply_markup=emergency_keyboard(context.user_data["emergency"]),
    )


async def emergency_request_contact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data["flow"] = "emergency_phone"
    keyboard = ReplyKeyboardMarkup(
        [[KeyboardButton("📱 Поделиться номером", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(
        "Укажите номер телефона или поделитесь контактом.",
        reply_markup=keyboard,
    )


async def emergency_request_address(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data["flow"] = "emergency_address"
    keyboard = ReplyKeyboardMarkup(
        [[KeyboardButton("📍 Отправить геопозицию", request_location=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(
        "Пришлите адрес текстом или отправьте геопозицию.", reply_markup=keyboard
    )


async def open_emergency_articles(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(
        "Выберите статью:", reply_markup=article_keyboard("emergency_article")
    )


async def select_emergency_article(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, _, value = query.data.partition("emergency_article_")
    if value == "back":
        await open_emergency(update, context)
        return
    if value == "Другая":
        context.user_data["flow"] = "emergency_article_custom"
        await query.answer()
        await query.message.reply_text("Введите номер статьи или краткое описание.")
        return
    context.user_data.setdefault("emergency", {})["article"] = value
    context.user_data.pop("flow", None)
    await query.answer("Сохранено")
    await query.message.reply_text(
        "Статья сохранена.",
        reply_markup=emergency_keyboard(context.user_data["emergency"]),
    )


async def submit_emergency(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    query.answer()
    data = context.user_data.get("emergency", {})
    user = update.effective_user
    message = (
        "🚨 Экстренный вызов\n\n"
        f"От: {user.full_name}\n"
        f"Профиль: {user_link(update)}\n"
        f"Телефон: {data.get('phone') or 'не указан'}\n"
        f"Адрес/координаты: {data.get('address') or data.get('coordinates') or 'не указаны'}\n"
        f"Статья: {data.get('article') or 'не указана'}"
    )

    try:
        await context.bot.send_message(
            chat_id=int(ADMIN_ID), text=message, parse_mode=ParseMode.HTML
        )
    except Exception as exc:  # pragma: no cover - внешнее взаимодействие
        logger.error("Не удалось отправить экстренный вызов админу: %s", exc)

    save_emergency_data(user, data)
    context.user_data.clear()
    await query.message.reply_text(
        "Спасибо! Экстренный вызов передан адвокату.", reply_markup=MAIN_KEYBOARD
    )


async def back_to_requests(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    show_requests_menu(update, "Выберите формат обращения:")


async def open_consultation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    query.answer()
    context.user_data["flow"] = "consult"
    context.user_data["consult_step"] = "city"
    context.user_data["consult_data"] = {
        "city": None,
        "phone": None,
        "urgency": None,
        "article": None,
        "description": None,
        "preferred_date": None,
        "preferred_time": None,
    }
    await query.message.reply_text("Укажите город, откуда вы обращаетесь.", reply_markup=MAIN_KEYBOARD)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    flow = context.user_data.get("flow")
    if flow == "consult":
        await handle_consult_text(update, context)
    elif flow in {"emergency_phone", "emergency_address", "emergency_article_custom"}:
        await handle_emergency_text(update, context)
    else:
        await handle_main_buttons(update, context)


async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    phone = update.message.contact.phone_number
    flow = context.user_data.get("flow")
    if flow == "emergency_phone":
        context.user_data.setdefault("emergency", {})["phone"] = phone
        context.user_data.pop("flow", None)
        await update.message.reply_text(
            "Телефон сохранен.",
            reply_markup=emergency_keyboard(context.user_data["emergency"]),
        )
    elif flow == "consult" and context.user_data.get("consult_step") == "phone":
        context.user_data["consult_data"]["phone"] = phone
        context.user_data["consult_step"] = "urgency"
        await ask_urgency(update, context)
    else:
        await update.message.reply_text("Контакт сохранен.", reply_markup=MAIN_KEYBOARD)


async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    flow = context.user_data.get("flow")
    if flow == "emergency_address":
        coords = f"{update.message.location.latitude},{update.message.location.longitude}"
        context.user_data.setdefault("emergency", {})["coordinates"] = coords
        context.user_data.pop("flow", None)
        await update.message.reply_text(
            "Координаты сохранены.",
            reply_markup=emergency_keyboard(context.user_data["emergency"]),
        )
    else:
        await update.message.reply_text("Локация сохранена.", reply_markup=MAIN_KEYBOARD)


async def handle_emergency_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    flow = context.user_data.get("flow")
    text = update.message.text.strip()
    data = context.user_data.setdefault("emergency", {})
    if flow == "emergency_phone":
        data["phone"] = text
    elif flow == "emergency_address":
        data["address"] = text
    elif flow == "emergency_article_custom":
        data["article"] = text
    context.user_data.pop("flow", None)
    await update.message.reply_text(
        "Данные сохранены.", reply_markup=emergency_keyboard(data)
    )


async def handle_consult_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    step = context.user_data.get("consult_step")
    data = context.user_data.get("consult_data", {})
    text = update.message.text.strip()

    if step == "city":
        data["city"] = text
        context.user_data["consult_step"] = "phone"
        keyboard = ReplyKeyboardMarkup(
            [[KeyboardButton("📱 Поделиться номером", request_contact=True)]],
            resize_keyboard=True,
            one_time_keyboard=True,
        )
        await update.message.reply_text(
            "Укажите номер телефона или поделитесь контактом.", reply_markup=keyboard
        )
    elif step == "phone":
        data["phone"] = text
        context.user_data["consult_step"] = "urgency"
        await ask_urgency(update, context)
    elif step == "description":
        data["description"] = text
        context.user_data["consult_step"] = "date"
        await update.message.reply_text(
            "Выберите удобную дату для связи:", reply_markup=consultation_date_keyboard()
        )
    else:
        await update.message.reply_text(
            "Уточните, пожалуйста, данные согласно шагам обращения.",
            reply_markup=MAIN_KEYBOARD,
        )


async def ask_urgency(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔥 Очень срочно", callback_data="consult_urgency_Очень срочно")],
            [InlineKeyboardButton("⚡ Срочно", callback_data="consult_urgency_Срочно")],
            [InlineKeyboardButton("⏳ Не спешу", callback_data="consult_urgency_Не спешу")],
        ]
    )
    await update.message.reply_text("Выберите степень срочности:", reply_markup=keyboard)


async def handle_consult_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query.data.startswith("consult_urgency_"):
        await set_consult_urgency(update, context)
    elif query.data.startswith("consult_article_"):
        await set_consult_article(update, context)
    elif query.data.startswith("consult_date_"):
        await set_consult_date(update, context)
    elif query.data.startswith("consult_time_"):
        await set_consult_time(update, context)


async def set_consult_urgency(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, _, value = query.data.partition("consult_urgency_")
    context.user_data["consult_data"]["urgency"] = value
    context.user_data["consult_step"] = "article"
    await query.answer("Срочность сохранена")
    await query.message.reply_text(
        "Выберите статью обращения:", reply_markup=article_keyboard("consult_article")
    )


async def set_consult_article(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, _, value = query.data.partition("consult_article_")
    if value == "back":
        await query.answer()
        await query.message.reply_text(
            "Выберите статью обращения:", reply_markup=article_keyboard("consult_article")
        )
        return
    if value == "Другая":
        context.user_data["consult_step"] = "article_custom"
        await query.answer()
        await query.message.reply_text("Укажите статью обращения.")
        return
    context.user_data["consult_data"]["article"] = value
    context.user_data["consult_step"] = "description"
    await query.answer("Статья сохранена")
    await query.message.reply_text("Кратко опишите суть проблемы.")


async def set_consult_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, _, value = query.data.partition("consult_date_")
    context.user_data["consult_data"]["preferred_date"] = value
    context.user_data["consult_step"] = "time"
    await query.answer("Дата сохранена")
    await query.message.reply_text(
        "Выберите удобный интервал времени:", reply_markup=consultation_time_keyboard()
    )


async def set_consult_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, _, value = query.data.partition("consult_time_")
    data = context.user_data.get("consult_data", {})
    data["preferred_time"] = value
    context.user_data["consult_step"] = None
    await query.answer("Время сохранено")

    await finalize_consultation(update, context)


async def finalize_consultation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    data = context.user_data.get("consult_data", {})
    user = update.effective_user
    message = (
        "📨 Новая заявка на консультацию\n\n"
        f"От: {user.full_name}\n"
        f"Профиль: {user_link(update)}\n"
        f"Город: {data.get('city') or 'не указан'}\n"
        f"Телефон: {data.get('phone') or 'не указан'}\n"
        f"Срочность: {data.get('urgency') or 'не указана'}\n"
        f"Статья: {data.get('article') or 'не указана'}\n"
        f"Описание: {data.get('description') or 'не указано'}\n"
        f"Дата связи: {data.get('preferred_date') or 'не выбрана'}\n"
        f"Время связи: {data.get('preferred_time') or 'не выбрано'}"
    )
    try:
        await context.bot.send_message(
            chat_id=int(ADMIN_ID), text=message, parse_mode=ParseMode.HTML
        )
    except Exception as exc:  # pragma: no cover - внешнее взаимодействие
        logger.error("Не удалось отправить заявку админу: %s", exc)

    save_consultation_data(user, data)
    context.user_data.clear()
    await update.callback_query.message.reply_text(
        "Спасибо! Заявка передана адвокату.", reply_markup=MAIN_KEYBOARD
    )


async def handle_consult_article_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if context.user_data.get("consult_step") == "article_custom":
        context.user_data["consult_data"]["article"] = update.message.text.strip()
        context.user_data["consult_step"] = "description"
        await update.message.reply_text("Кратко опишите суть проблемы.")
        return True
    return False


def save_emergency_data(user, data: Dict[str, Optional[str]]) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO emergency_calls (user_id, username, full_name, phone, address, coordinates, article, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user.id,
                user.username,
                user.full_name,
                data.get("phone"),
                data.get("address"),
                data.get("coordinates"),
                data.get("article"),
                datetime.utcnow().isoformat(),
            ),
        )


def save_consultation_data(user, data: Dict[str, Optional[str]]) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO consultations (
                user_id, username, full_name, city, phone, urgency, article, description, preferred_date, preferred_time, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user.id,
                user.username,
                user.full_name,
                data.get("city"),
                data.get("phone"),
                data.get("urgency"),
                data.get("article"),
                data.get("description"),
                data.get("preferred_date"),
                data.get("preferred_time"),
                datetime.utcnow().isoformat(),
            ),
        )


async def handle_callback_queries(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    data = update.callback_query.data
    if data == "emergency_open":
        await open_emergency(update, context)
    elif data == "emergency_phone":
        await emergency_request_contact(update, context)
    elif data == "emergency_address":
        await emergency_request_address(update, context)
    elif data == "emergency_article_menu":
        await open_emergency_articles(update, context)
    elif data.startswith("emergency_article_"):
        await select_emergency_article(update, context)
    elif data == "emergency_submit":
        await submit_emergency(update, context)
    elif data == "back_to_requests":
        await back_to_requests(update, context)
    elif data == "consult_open":
        await open_consultation(update, context)
    elif data.startswith("consult_"):
        await handle_consult_callbacks(update, context)
    else:
        await update.callback_query.answer()


async def handle_text_preprocess(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await handle_consult_article_text(update, context):
        return
    await handle_text(update, context)


def main() -> None:
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("Не найден TELEGRAM_BOT_TOKEN в cfg.py")

    init_db()

    application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CallbackQueryHandler(handle_callback_queries))
    application.add_handler(MessageHandler(filters.CONTACT, handle_contact))
    application.add_handler(MessageHandler(filters.LOCATION, handle_location))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_preprocess))

    logger.info("Бот запущен")
    application.run_polling()


if __name__ == "__main__":
    main()
