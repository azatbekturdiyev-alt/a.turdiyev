import asyncio
import logging
import os
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton as Btn,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)
from dotenv import load_dotenv

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
WEBAPP_URL = os.getenv("WEBAPP_URL", "").strip()  # https://... адрес мини-сайта
APK_PATH = Path(__file__).parent / "app-release.apk"
FILE_ID_CACHE = Path(__file__).parent / ".apk_file_id"

dp = Dispatcher()


# ---------- Тексты ----------
START_TEXT = (
    "Привет! Это бот @azatrdv 👋\n"
    "Здесь вы можете ознакомиться с моими проектами, услугами "
    "или связаться со мной."
)

CONTACT_TEXT = (
    "📬 <b>Связаться</b>\n\n"
    "📞 Телефон: +998 877701799\n"
    "✈️ Telegram: @azatrdv, @nsvef\n"
    "📸 Instagram: azat.trd"
)

ABOUT = {
    "sosed": (
        "🏠 <b>sosed.uz</b> — шеринг вещей и взаимопомощь рядом с вами!\n\n"
        "Удобный сервис в Telegram и на сайте, который объединяет соседей "
        "по районам. Делитесь тем, чем не пользуетесь, берите во временное "
        "пользование нужные инструменты (бесплатно или за символическую "
        "плату) и выручайте друг друга в пару кликов."
    ),
    "auction": (
        "🎮 <b>auction game</b> — это Telegram-игра «Аукцион», азартная "
        "дуэль с друзьями, где вы торгуетесь за случайные лоты, соревнуясь "
        "в стратегической выдержке и распределении бюджета!\n\n"
        "Собирайте сильнейшие команды по супергероям Marvel/DC или известным "
        "футболистам вслепую и узнайте, чей состав окажется мощнее."
    ),
    "kylix": (
        "👕 <b>kylix.uz</b> — это интернет-магазин с большим выбором "
        "товаров: одежда, обувь, кепки и многое другое с удобной доставкой."
    ),
}

PROJECT_TITLES = {
    "sosed": "🏠 <b>sosed.uz</b>",
    "auction": "🎮 <b>auction game</b>",
    "kylix": "👕 <b>kylix.uz</b>",
}

SERVICES = {
    "site": "🌐 Создать сайт",
    "stars": "⭐️ Купить Telegram Stars",
    "nft": "🎁 Купить NFT Gift",
    "premium": "💎 Купить Telegram Premium",
}


# ---------- Клавиатуры ----------
def kb(*rows: list[Btn]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=list(rows))


def main_menu() -> InlineKeyboardMarkup:
    rows = [
        [Btn(text="🛠 Услуги", callback_data="services")],
        [Btn(text="🚀 Мои проекты", callback_data="projects")],
        [Btn(text="📬 Связаться", callback_data="contact")],
    ]
    if WEBAPP_URL.startswith("https://"):
        rows.append([Btn(text="🌐 Веб", web_app=WebAppInfo(url=WEBAPP_URL))])
    else:
        rows.append([Btn(text="🌐 Веб", callback_data="web_soon")])
    return kb(*rows)


def back_main() -> list[Btn]:
    return [Btn(text="⬅️ Назад в меню", callback_data="menu")]


def services_menu() -> InlineKeyboardMarkup:
    rows = [[Btn(text=t, callback_data=f"srv_{k}")] for k, t in SERVICES.items()]
    rows.append(back_main())
    return kb(*rows)


def projects_menu() -> InlineKeyboardMarkup:
    return kb(
        [Btn(text="🏠 sosed.uz", callback_data="p_sosed")],
        [Btn(text="🎮 auction game", callback_data="p_auction")],
        [Btn(text="👕 kylix.uz", callback_data="p_kylix")],
        back_main(),
    )


def back_projects() -> list[Btn]:
    return [Btn(text="⬅️ Назад в проекты", callback_data="projects")]


def project_menu(key: str) -> InlineKeyboardMarkup:
    if key == "sosed":
        rows = [
            [Btn(text="❓ Что такое sosed.uz?", callback_data="about_sosed")],
            [Btn(text="🌐 Сайт", url="https://aztrdv.adaf.workers.dev/")],
            [Btn(text="📥 Скачать", callback_data="dl_sosed")],
            [Btn(text="✈️ Телеграм бот", url="https://t.me/localtasks_bot")],
            [Btn(text="✈️ Телеграм канал", url="https://t.me/sosed_uzz")],
            [Btn(text="📸 Инстаграм", url="https://www.instagram.com/sosed_uz")],
        ]
    elif key == "auction":
        rows = [
            [Btn(text="❓ Что такое auction game?", callback_data="about_auction")],
            [Btn(text="🎮 Играть", url="https://t.me/auction_hub_bot")],
            [Btn(text="✈️ Телеграм", url="https://t.me/auctiongame_az")],
            [Btn(text="📸 Инстаграм", url="https://www.instagram.com/auction_game")],
        ]
    else:
        rows = [
            [Btn(text="❓ Что такое kylix.uz?", callback_data="about_kylix")],
            [Btn(text="✈️ Телеграм", url="https://t.me/kylixuz")],
            [Btn(text="📸 Инстаграм", url="https://www.instagram.com/kylix.uz")],
        ]
    rows.append(back_projects())
    return kb(*rows)


# ---------- Утилиты ----------
async def show(cb: CallbackQuery, text: str, markup: InlineKeyboardMarkup):
    """Редактирует текущее сообщение; если нельзя — шлёт новое."""
    try:
        await cb.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            pass
        else:
            await cb.message.answer(text, reply_markup=markup)
    await cb.answer()


# ---------- Хендлеры ----------
@dp.message(CommandStart())
async def start(m: Message):
    await m.answer(START_TEXT, reply_markup=main_menu())


@dp.callback_query(F.data == "menu")
async def menu(cb: CallbackQuery):
    await show(cb, START_TEXT, main_menu())


@dp.callback_query(F.data == "web_soon")
async def web_soon(cb: CallbackQuery):
    await cb.answer("Веб-раздел скоро появится", show_alert=True)


@dp.callback_query(F.data == "contact")
async def contact(cb: CallbackQuery):
    markup = kb(
        [Btn(text="✈️ @azatrdv", url="https://t.me/azatrdv")],
        [Btn(text="✈️ @nsvef", url="https://t.me/nsvef")],
        [Btn(text="📸 Instagram", url="https://www.instagram.com/azat.trd")],
        back_main(),
    )
    await show(cb, CONTACT_TEXT, markup)


@dp.callback_query(F.data == "services")
async def services(cb: CallbackQuery):
    await show(cb, "🛠 <b>Услуги</b>\nВыберите, что вас интересует:", services_menu())


@dp.callback_query(F.data.startswith("srv_"))
async def service_stub(cb: CallbackQuery):
    key = cb.data.removeprefix("srv_")
    title = SERVICES.get(key, "Услуга")
    markup = kb(
        [Btn(text="✍️ Написать мне", url="https://t.me/azatrdv")],
        [Btn(text="⬅️ Назад к услугам", callback_data="services")],
    )
    await show(
        cb,
        f"{title}\n\nРаздел в разработке. Напишите мне — всё решим лично.",
        markup,
    )


@dp.callback_query(F.data == "projects")
async def projects(cb: CallbackQuery):
    await show(cb, "🚀 <b>Мои проекты</b>\nВыберите проект:", projects_menu())


@dp.callback_query(F.data.in_({"p_sosed", "p_auction", "p_kylix"}))
async def project(cb: CallbackQuery):
    key = cb.data.removeprefix("p_")
    await show(cb, PROJECT_TITLES[key], project_menu(key))


@dp.callback_query(F.data.startswith("about_"))
async def about(cb: CallbackQuery):
    key = cb.data.removeprefix("about_")
    markup = kb([Btn(text="⬅️ Назад", callback_data=f"p_{key}")])
    await show(cb, ABOUT[key], markup)


@dp.callback_query(F.data == "dl_sosed")
async def download(cb: CallbackQuery):
    await cb.answer("Отправляю файл…")
    if FILE_ID_CACHE.exists():
        doc = FILE_ID_CACHE.read_text().strip()
    elif APK_PATH.exists():
        doc = FSInputFile(APK_PATH, filename="sosed.apk")
    else:
        await cb.message.answer("Файл пока недоступен. Напишите мне: @azatrdv")
        return

    sent = await cb.message.answer_document(doc, caption="📥 sosed.uz для Android")
    if not FILE_ID_CACHE.exists():
        FILE_ID_CACHE.write_text(sent.document.file_id)


async def main():
    if not BOT_TOKEN:
        raise SystemExit("Укажите BOT_TOKEN в файле .env")
    logging.basicConfig(level=logging.INFO)
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
