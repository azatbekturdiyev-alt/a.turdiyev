import asyncio
import html
import json
import logging
import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BotCommand,
    BotCommandScopeDefault,
    CallbackQuery,
    InlineKeyboardButton as _Btn,
    InlineKeyboardMarkup,
    Message,
)
from dotenv import load_dotenv

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or 0)  # ваш Telegram ID
ANON_LINK = os.getenv("ANON_LINK", "").strip()  # ваша ссылка в анонимном боте
TGSTORE_URL = "https://t.me/tgstore_hub_bot"  # раздел TG STORE ведёт в этого бота
APK_URL = os.getenv("APK_URL", "").strip()  # прямая ссылка на APK в GitHub Releases
ICON_PACK = os.getenv("ICON_PACK", "TgAndroidIcons").strip()  # набор эмодзи-иконок
DB_PATH = Path(os.getenv("DB_PATH") or Path(__file__).parent / "users.db")  # путь к базе (на Render — на постоянном диске)

dp = Dispatcher()


# ---------- Иконки (кастомные эмодзи) ----------
# Эмодзи, которые бот показывает пользователям, и которые можно заменить иконками
USED = {"🛠", "🚀", "📬", "🌐", "⭐", "🎁", "💎", "🏠", "🎮", "👕", "✈", "📸",
        "❓", "📥", "⬅", "💳", "📢", "🕵", "✍", "📞"}
ICON_MAP: dict[str, str] = {}  # обычное эмодзи -> id кастомного эмодзи


def norm(e: str) -> str:
    return e.replace("\ufe0f", "")


# Есть ли у владельца бота Telegram Premium (тогда работают иконки из набора)
PREMIUM = False

_EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\u2300-\u23FF\uFE0F\u200D]+ ?")


def strip_emoji(text: str) -> str:
    """Убирает эмодзи (флаги языков не трогает)."""
    return _EMOJI_RE.sub("", text)


def Btn(text: str, **kw) -> _Btn:
    """Кнопка. С Premium: иконка из набора. Без Premium: без эмодзи."""
    if not PREMIUM:
        return _Btn(text=strip_emoji(text) or text, **kw)
    head, _, rest = text.partition(" ")
    cid = ICON_MAP.get(norm(head))
    if cid and rest:
        return _Btn(text=rest, icon_custom_emoji_id=cid, **kw)
    return _Btn(text=text, **kw)


# Кнопки главного меню без Premium (обычные эмодзи)
MAIN_PLAIN = {
    "services": "◾", "projects": "👾", "contact": "📞",
    "cards": "💳", "channels": "📢", "anon": "✉️",
}


def main_btn(key: str, lang: str) -> list[_Btn]:
    text = t(f"btn_{key}", lang)
    # TG STORE сразу открывает другого бота, остальные — внутренние разделы
    extra = {"url": TGSTORE_URL} if key == "services" else {"callback_data": key}
    if PREMIUM:
        return [Btn(text=text, **extra)]
    return [_Btn(text=f"{MAIN_PLAIN[key]} {strip_emoji(text)}", **extra)]


def deco(text: str) -> str:
    """С Premium: эмодзи -> иконки из набора. Без Premium: эмодзи убираются."""
    if not PREMIUM:
        return strip_emoji(text).strip()
    if not ICON_MAP:
        return text
    keys = sorted(ICON_MAP, key=len, reverse=True)
    pat = re.compile("|".join(re.escape(k) + "\ufe0f?" for k in keys))
    return pat.sub(
        lambda m: f'<tg-emoji emoji-id="{ICON_MAP[norm(m.group(0))]}">{m.group(0)}</tg-emoji>',
        text,
    )


async def detect_premium(bot: Bot) -> bool:
    flag = os.getenv("OWNER_PREMIUM", "").strip().lower()
    if flag in ("1", "true", "yes", "да"):
        return True
    if flag in ("0", "false", "no", "нет"):
        return False
    if ADMIN_ID:
        try:
            member = await bot.get_chat_member(ADMIN_ID, ADMIN_ID)
            return bool(getattr(member.user, "is_premium", False))
        except Exception:
            logging.exception("Не удалось определить Premium у владельца")
    return False


async def load_icons(bot: Bot):
    try:
        pack = await bot.get_sticker_set(ICON_PACK)
        for st in pack.stickers:
            if st.custom_emoji_id and st.emoji and norm(st.emoji) in USED:
                ICON_MAP.setdefault(norm(st.emoji), st.custom_emoji_id)
    except Exception:
        logging.exception("Не удалось загрузить набор иконок %s", ICON_PACK)
    # ручные правки: icons.json вида {"🏠": "5368324170671202286"}
    f = Path(__file__).parent / "icons.json"
    if f.exists():
        for k, v in json.loads(f.read_text(encoding="utf-8")).items():
            ICON_MAP[norm(k)] = str(v)
    logging.info("Иконок подключено: %d", len(ICON_MAP))


# ---------- База пользователей ----------
def detect_lang(u) -> str:
    code = (u.language_code or "").lower()
    if code.startswith("uz"):
        return "uz"
    if code.startswith("en"):
        return "en"
    return "ru"


def db_init():
    with sqlite3.connect(DB_PATH) as c:
        c.execute(
            "CREATE TABLE IF NOT EXISTS users ("
            "id INTEGER PRIMARY KEY, name TEXT, username TEXT, joined TEXT, lang TEXT)"
        )
        c.execute(
            "CREATE TABLE IF NOT EXISTS reply_map ("
            "admin_msg_id INTEGER PRIMARY KEY, user_id INTEGER)"
        )
        cols = [r[1] for r in c.execute("PRAGMA table_info(users)")]
        if "lang" not in cols:
            c.execute("ALTER TABLE users ADD COLUMN lang TEXT")


def db_add_user(u) -> bool:
    """Возвращает True, если пользователь новый."""
    with sqlite3.connect(DB_PATH) as c:
        if c.execute("SELECT 1 FROM users WHERE id=?", (u.id,)).fetchone():
            return False
        c.execute(
            "INSERT INTO users VALUES (?,?,?,?,?)",
            (u.id, u.full_name, u.username or "",
             datetime.now().strftime("%d.%m.%Y %H:%M"), detect_lang(u)),
        )
        return True


def db_all_users():
    with sqlite3.connect(DB_PATH) as c:
        return c.execute("SELECT id, name, username, joined FROM users ORDER BY rowid").fetchall()


def db_map(admin_msg_id: int, user_id: int):
    with sqlite3.connect(DB_PATH) as c:
        c.execute("INSERT OR REPLACE INTO reply_map VALUES (?,?)", (admin_msg_id, user_id))


def db_target(admin_msg_id: int):
    with sqlite3.connect(DB_PATH) as c:
        row = c.execute("SELECT user_id FROM reply_map WHERE admin_msg_id=?", (admin_msg_id,)).fetchone()
    return row[0] if row else None


def get_lang(u) -> str:
    with sqlite3.connect(DB_PATH) as c:
        row = c.execute("SELECT lang FROM users WHERE id=?", (u.id,)).fetchone()
    return row[0] if row and row[0] else detect_lang(u)


def set_lang(u, lang: str):
    db_add_user(u)
    with sqlite3.connect(DB_PATH) as c:
        c.execute("UPDATE users SET lang=? WHERE id=?", (lang, u.id))


def who(u) -> str:
    uname = f"@{u.username}" if u.username else "без username"
    return f"{html.escape(u.full_name)} ({uname}), ID: <code>{u.id}</code>"


# ---------- Тексты: ru / en / uz ----------
LANGS = {"ru": "🇷🇺 Русский", "en": "🇬🇧 English", "uz": "🇺🇿 O‘zbekcha"}

T = {
    "start": {"ru": "@azatrdv", "en": "@azatrdv", "uz": "@azatrdv"},
    "btn_services": {"ru": "🛠 TG STORE", "en": "🛠 TG STORE", "uz": "🛠 TG STORE"},
    "btn_projects": {"ru": "🚀 Проекты", "en": "🚀 Projects", "uz": "🚀 Loyihalar"},
    "btn_contact": {"ru": "📬 Связаться", "en": "📬 Contact", "uz": "📬 Bog‘lanish"},
    "btn_cards": {"ru": "💳 Карты", "en": "💳 Cards", "uz": "💳 Kartalar"},
    "btn_channels": {"ru": "📢 ТГК", "en": "📢 TG channels", "uz": "📢 TG kanallar"},
    "btn_anon": {"ru": "🕵️ Анонимное сообщение", "en": "🕵️ Anonymous message", "uz": "🕵️ Anonim xabar"},
    "back_menu": {"ru": "⬅️ Назад в меню", "en": "⬅️ Back to menu", "uz": "⬅️ Menyuga qaytish"},
    "back_projects": {"ru": "⬅️ Назад в проекты", "en": "⬅️ Back to projects", "uz": "⬅️ Loyihalarga qaytish"},
    "back": {"ru": "⬅️ Назад", "en": "⬅️ Back", "uz": "⬅️ Orqaga"},
    "projects_title": {
        "ru": "🚀 <b>Проекты</b>\nВыберите проект:",
        "en": "🚀 <b>Projects</b>\nChoose a project:",
        "uz": "🚀 <b>Loyihalar</b>\nLoyihani tanlang:",
    },
    "about_btn": {"ru": "❓ Что такое {name}?", "en": "❓ What is {name}?", "uz": "❓ {name} nima?"},
    "btn_site": {"ru": "🌐 Сайт", "en": "🌐 Website", "uz": "🌐 Sayt"},
    "btn_download": {"ru": "📥 Скачать", "en": "📥 Download", "uz": "📥 Yuklab olish"},
    "btn_bot": {"ru": "✈️ Телеграм бот", "en": "✈️ Telegram bot", "uz": "✈️ Telegram bot"},
    "btn_channel": {"ru": "✈️ Телеграм канал", "en": "✈️ Telegram channel", "uz": "✈️ Telegram kanal"},
    "btn_tg": {"ru": "✈️ Телеграм", "en": "✈️ Telegram", "uz": "✈️ Telegram"},
    "btn_insta": {"ru": "📸 Инстаграм", "en": "📸 Instagram", "uz": "📸 Instagram"},
    "btn_play": {"ru": "🎮 Играть", "en": "🎮 Play", "uz": "🎮 O‘ynash"},
    "about_sosed": {
        "ru": "🏠 <b>sosed.uz</b> — шеринг вещей и взаимопомощь рядом с вами!\n\nУдобный сервис в Telegram и на сайте, который объединяет соседей по районам. Делитесь тем, чем не пользуетесь, берите во временное пользование нужные инструменты (бесплатно или за символическую плату) и выручайте друг друга в пару кликов.",
        "en": "🏠 <b>sosed.uz</b> — item sharing and mutual help right next to you!\n\nA convenient service in Telegram and on the web that connects neighbors by district. Share what you don't use, borrow the tools you need (free or for a small fee) and help each other out in a couple of taps.",
        "uz": "🏠 <b>sosed.uz</b> — buyumlarni ulashish va o‘zaro yordam, aynan yoningizda!\n\nTelegram va saytdagi qulay xizmat, u qo‘shnilarni mahallalar bo‘yicha birlashtiradi. Foydalanmaydigan narsalaringizni ulashing, kerakli asboblarni vaqtincha oling (bepul yoki ramziy haq evaziga) va bir necha bosishda bir-biringizga yordam bering.",
    },
    "about_auction": {
        "ru": "🎮 <b>auction game</b> — это Telegram-игра «Аукцион», азартная дуэль с друзьями, где вы торгуетесь за случайные лоты, соревнуясь в стратегической выдержке и распределении бюджета!\n\nСобирайте сильнейшие команды по супергероям Marvel/DC или известным футболистам вслепую и узнайте, чей состав окажется мощнее.",
        "en": "🎮 <b>auction game</b> is the Telegram game “Auction” — a thrilling duel with friends where you bid on random lots, testing your strategic nerve and budget management!\n\nBlindly build the strongest teams of Marvel/DC superheroes or famous footballers and find out whose lineup turns out more powerful.",
        "uz": "🎮 <b>auction game</b> — bu Telegram’dagi «Auksion» o‘yini, do‘stlar bilan shov-shuvli bellashuv: tasodifiy lotlar uchun savdolashasiz, strategik sabr va byudjetni taqsimlashda bellashasiz!\n\nMarvel/DC superqahramonlari yoki mashhur futbolchilardan eng kuchli jamoalarni ko‘r-ko‘rona yig‘ing va kimning tarkibi kuchliroq ekanini bilib oling.",
    },
    "about_kylix": {
        "ru": "👕 <b>kylix.uz</b> — это интернет-магазин с большим выбором товаров: одежда, обувь, кепки и многое другое с удобной доставкой.",
        "en": "👕 <b>kylix.uz</b> is an online store with a wide choice of goods: clothes, shoes, caps and much more with convenient delivery.",
        "uz": "👕 <b>kylix.uz</b> — keng tanlovli internet-do‘kon: kiyim, poyabzal, kepkalar va boshqa ko‘p narsalar qulay yetkazib berish bilan.",
    },
    "contact": {
        "ru": "📬 <b>Связаться</b>\n\n📞 Телефон: +998 877701799\n✈️ Telegram: @azatrdv\n💬 WhatsApp: +998 950990799\n📸 Instagram: azat.trd",
        "en": "📬 <b>Contact</b>\n\n📞 Phone: +998 877701799\n✈️ Telegram: @azatrdv\n💬 WhatsApp: +998 950990799\n📸 Instagram: azat.trd",
        "uz": "📬 <b>Bog‘lanish</b>\n\n📞 Telefon: +998 877701799\n✈️ Telegram: @azatrdv\n💬 WhatsApp: +998 950990799\n📸 Instagram: azat.trd",
    },
    "cards": {
        "ru": "💳 <b>Карты</b>\nНажмите на номер, чтобы скопировать.",
        "en": "💳 <b>Cards</b>\nTap a number to copy it.",
        "uz": "💳 <b>Kartalar</b>\nNusxalash uchun raqamni bosing.",
    },
    "channels_title": {"ru": "📢 <b>ТГК</b>", "en": "📢 <b>TG channels</b>", "uz": "📢 <b>TG kanallar</b>"},
    "channel_n": {"ru": "📢 Канал {n}", "en": "📢 Channel {n}", "uz": "📢 {n}-kanal"},
    "anon_text": {
        "ru": "🕵️ <b>Анонимное сообщение</b>\nНажмите кнопку — откроется официальный анонимный бот, и вы сможете написать мне, не раскрывая себя.",
        "en": "🕵️ <b>Anonymous message</b>\nTap the button — the official anonymous bot will open and you can write to me without revealing yourself.",
        "uz": "🕵️ <b>Anonim xabar</b>\nTugmani bosing — rasmiy anonim bot ochiladi va siz o‘zingizni oshkor qilmasdan menga yoza olasiz.",
    },
    "anon_soon": {
        "ru": "🕵️ <b>Анонимное сообщение</b>\nРаздел скоро будет доступен.",
        "en": "🕵️ <b>Anonymous message</b>\nThis section will be available soon.",
        "uz": "🕵️ <b>Anonim xabar</b>\nBo‘lim tez orada ishga tushadi.",
    },
    "anon_go": {"ru": "🕵️ Написать анонимно", "en": "🕵️ Write anonymously", "uz": "🕵️ Anonim yozish"},
    "dl_sending": {"ru": "Отправляю файл…", "en": "Sending the file…", "uz": "Fayl yuborilmoqda…"},
    "dl_caption": {"ru": "📥 sosed.uz для Android", "en": "📥 sosed.uz for Android", "uz": "📥 Android uchun sosed.uz"},
    "dl_unavailable": {
        "ru": "Файл пока недоступен. Напишите мне: @azatrdv",
        "en": "The file is not available yet. Message me: @azatrdv",
        "uz": "Fayl hozircha mavjud emas. Menga yozing: @azatrdv",
    },
    "dl_link": {
        "ru": "Скачайте приложение по кнопке ниже:",
        "en": "Download the app using the button below:",
        "uz": "Ilovani quyidagi tugma orqali yuklab oling:",
    },
    "dl_apk": {"ru": "📥 Скачать APK", "en": "📥 Download APK", "uz": "📥 APK yuklab olish"},
    "lang_title": {
        "ru": "🌐 Выберите язык",
        "en": "🌐 Choose your language",
        "uz": "🌐 Tilni tanlang",
    },
}


def t(key: str, lang: str, **kw) -> str:
    s = T[key].get(lang) or T[key]["ru"]
    return s.format(**kw) if kw else s


COMMANDS = {
    "ru": [("start", "Главное меню"), ("language", "Сменить язык")],
    "en": [("start", "Main menu"), ("language", "Change language")],
    "uz": [("start", "Asosiy menyu"), ("language", "Tilni o‘zgartirish")],
}

CARDS_BODY = (
    "\n\n<b>HUMO</b>\n"
    "<code>9860 1901 0257 9728</code>\n"
    "AZATBEK TURDIYEV\n\n"
    "<code>4067 0700 0641 3905</code>\n"
    "AZATBEK TURDIYEV\n\n"
    "<b>VISA</b>\n"
    "<code>4466 1369 5252 6631</code>\n"
    "AZATBEK TURDIYEV\n\n"
    "<code>4916 9903 1127 9423</code>"
)

PROJECTS = {"sosed": "sosed.uz", "auction": "auction game", "kylix": "kylix.uz"}
PROJECT_ICONS = {"sosed": "🏠", "auction": "🎮", "kylix": "👕"}


# ---------- Клавиатуры ----------
def kb(*rows: list[_Btn]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=list(rows))


def back_menu(lang) -> list[_Btn]:
    return [Btn(text=t("back_menu", lang), callback_data="menu")]


def main_menu(lang) -> InlineKeyboardMarkup:
    return kb(*[main_btn(k, lang) for k in
                ("services", "projects", "contact", "cards", "channels", "anon")])


def projects_menu(lang) -> InlineKeyboardMarkup:
    rows = [
        [Btn(text=f"{PROJECT_ICONS[k]} {name}", callback_data=f"p_{k}")]
        for k, name in PROJECTS.items()
    ]
    rows.append(back_menu(lang))
    return kb(*rows)


def project_menu(key: str, lang) -> InlineKeyboardMarkup:
    about = [Btn(text=t("about_btn", lang, name=PROJECTS[key]), callback_data=f"about_{key}")]
    if key == "sosed":
        rows = [
            about,
            [Btn(text=t("btn_site", lang), url="https://aztrdv.adaf.workers.dev/")],
            [Btn(text=t("btn_download", lang), callback_data="dl_sosed")],
            [Btn(text=t("btn_bot", lang), url="https://t.me/localtasks_bot")],
            [Btn(text=t("btn_channel", lang), url="https://t.me/sosed_uzz")],
            [Btn(text=t("btn_insta", lang), url="https://www.instagram.com/sosed_uz")],
        ]
    elif key == "auction":
        rows = [
            about,
            [Btn(text=t("btn_play", lang), url="https://t.me/auction_hub_bot")],
            [Btn(text=t("btn_tg", lang), url="https://t.me/auctiongame_az")],
            [Btn(text=t("btn_insta", lang), url="https://www.instagram.com/auction_game")],
        ]
    else:
        rows = [
            about,
            [Btn(text=t("btn_tg", lang), url="https://t.me/kylixuz")],
            [Btn(text=t("btn_insta", lang), url="https://www.instagram.com/kylix.uz")],
        ]
    rows.append([Btn(text=t("back_projects", lang), callback_data="projects")])
    return kb(*rows)


# ---------- Утилиты ----------
async def show(cb: CallbackQuery, text: str, markup: InlineKeyboardMarkup):
    """Редактирует текущее сообщение; если нельзя — шлёт новое."""
    text = deco(text)
    try:
        await cb.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            await cb.message.answer(text, reply_markup=markup)
    await cb.answer()


# ---------- Хендлеры ----------
@dp.message(CommandStart())
async def start(m: Message, bot: Bot):
    is_new = db_add_user(m.from_user)
    lang = get_lang(m.from_user)
    await m.answer(deco(t("start", lang)), reply_markup=main_menu(lang))
    if is_new and ADMIN_ID and m.from_user.id != ADMIN_ID:
        total = len(db_all_users())
        note = await bot.send_message(
            ADMIN_ID, f"🆕 Новый пользователь: {who(m.from_user)}\nВсего: {total}"
        )
        db_map(note.message_id, m.from_user.id)


@dp.message(Command("language"))
async def language_cmd(m: Message):
    db_add_user(m.from_user)
    markup = kb(*[[Btn(text=name, callback_data=f"lang_{code}")] for code, name in LANGS.items()])
    await m.answer(deco("🌐 Язык / Language / Til"), reply_markup=markup)


@dp.callback_query(F.data.startswith("lang_"))
async def language_set(cb: CallbackQuery):
    lang = cb.data.removeprefix("lang_")
    if lang not in LANGS:
        await cb.answer()
        return
    set_lang(cb.from_user, lang)
    await show(cb, t("start", lang), main_menu(lang))


@dp.message(Command("users"))
async def users_cmd(m: Message):
    if m.from_user.id != ADMIN_ID:
        return
    rows = db_all_users()
    if not rows:
        await m.answer("Пользователей пока нет.")
        return
    lines = [
        f"{i}. {html.escape(n)} {'@' + un if un else ''} — <code>{uid}</code> — {j}"
        for i, (uid, n, un, j) in enumerate(rows, 1)
    ]
    chunk = f"👥 <b>Пользователей: {len(rows)}</b>\n\n"
    for line in lines:
        if len(chunk) + len(line) > 3800:
            await m.answer(chunk)
            chunk = ""
        chunk += line + "\n"
    if chunk:
        await m.answer(chunk)


@dp.message(Command("icons"))
async def icons_cmd(m: Message, bot: Bot):
    """Админ: показывает, какие иконки подхватились, и список набора."""
    if m.from_user.id != ADMIN_ID:
        return
    ok = " ".join(sorted(ICON_MAP)) or "—"
    miss = " ".join(sorted(e for e in USED if e not in ICON_MAP)) or "—"
    head = f"Premium-режим: {'включён' if PREMIUM else 'выключен'}\nНабор: {ICON_PACK}\nПодключены: {ok}\nНе нашлись: {miss}\n\n"
    try:
        pack = await bot.get_sticker_set(ICON_PACK)
    except Exception as e:
        await m.answer(head + f"Не удалось открыть набор: {html.escape(str(e))}")
        return
    chunk = head + "Весь набор (эмодзи → id):\n"
    for st in pack.stickers:
        line = f"{st.emoji} <code>{st.custom_emoji_id}</code>\n"
        if len(chunk) + len(line) > 3800:
            await m.answer(chunk)
            chunk = ""
        chunk += line
    if chunk:
        await m.answer(chunk)


@dp.callback_query(F.data == "menu")
async def menu(cb: CallbackQuery):
    lang = get_lang(cb.from_user)
    await show(cb, t("start", lang), main_menu(lang))


@dp.callback_query(F.data == "contact")
async def contact(cb: CallbackQuery):
    lang = get_lang(cb.from_user)
    markup = kb(
        [Btn(text="✈️ @azatrdv", url="https://t.me/azatrdv")],
        [Btn(text="💬 WhatsApp", url="https://wa.me/998950990799")],
        [Btn(text="📸 Instagram", url="https://www.instagram.com/azat.trd")],
        back_menu(lang),
    )
    await show(cb, t("contact", lang), markup)


@dp.callback_query(F.data == "cards")
async def cards(cb: CallbackQuery):
    lang = get_lang(cb.from_user)
    await show(cb, t("cards", lang) + CARDS_BODY, kb(back_menu(lang)))


@dp.callback_query(F.data == "channels")
async def channels(cb: CallbackQuery):
    lang = get_lang(cb.from_user)
    markup = kb(
        [Btn(text=t("channel_n", lang, n=1), url="https://t.me/scznd")],
        [Btn(text=t("channel_n", lang, n=2), url="https://t.me/+KUqAVx6C9eYyMTFi")],
        back_menu(lang),
    )
    await show(cb, t("channels_title", lang), markup)


@dp.callback_query(F.data == "anon")
async def anon(cb: CallbackQuery):
    lang = get_lang(cb.from_user)
    if ANON_LINK:
        markup = kb([Btn(text=t("anon_go", lang), url=ANON_LINK)], back_menu(lang))
        await show(cb, t("anon_text", lang), markup)
    else:
        await show(cb, t("anon_soon", lang), kb(back_menu(lang)))


@dp.callback_query(F.data == "projects")
async def projects(cb: CallbackQuery):
    lang = get_lang(cb.from_user)
    await show(cb, t("projects_title", lang), projects_menu(lang))


@dp.callback_query(F.data.in_({"p_sosed", "p_auction", "p_kylix"}))
async def project(cb: CallbackQuery):
    lang = get_lang(cb.from_user)
    key = cb.data.removeprefix("p_")
    title = f"{PROJECT_ICONS[key]} <b>{PROJECTS[key]}</b>"
    await show(cb, title, project_menu(key, lang))


@dp.callback_query(F.data.in_({"about_sosed", "about_auction", "about_kylix"}))
async def about(cb: CallbackQuery):
    lang = get_lang(cb.from_user)
    key = cb.data.removeprefix("about_")
    markup = kb([Btn(text=t("back", lang), callback_data=f"p_{key}")])
    await show(cb, t(f"about_{key}", lang), markup)


@dp.callback_query(F.data == "dl_sosed")
async def download(cb: CallbackQuery):
    lang = get_lang(cb.from_user)
    await cb.answer(t("dl_sending", lang))
    if not APK_URL:
        await cb.message.answer(t("dl_unavailable", lang))
        return
    try:
        # Telegram сам скачивает файл по ссылке (до 20 МБ)
        await cb.message.answer_document(APK_URL, caption=deco(t("dl_caption", lang)))
    except Exception:
        logging.exception("Не удалось отправить APK по ссылке")
        markup = kb([Btn(text=t("dl_apk", lang), url=APK_URL)])
        await cb.message.answer(t("dl_link", lang), reply_markup=markup)


@dp.message(F.reply_to_message, F.from_user.id == ADMIN_ID)
async def admin_reply(m: Message):
    """Админ отвечает (Reply) на сообщение пользователя — ответ уходит этому пользователю."""
    uid = db_target(m.reply_to_message.message_id)
    if not uid:
        await m.answer("Не знаю, кому это отправить. Сделайте Reply на сообщение пользователя.")
        return
    try:
        await m.copy_to(uid)
        await m.answer("✅ Отправлено")
    except TelegramForbiddenError:
        await m.answer("❌ Пользователь заблокировал бота.")
    except Exception as e:
        logging.exception("Не удалось отправить ответ")
        await m.answer(f"❌ Не удалось отправить: {html.escape(str(e))}")


@dp.message()
async def to_admin(m: Message, bot: Bot):
    """Любое сообщение боту пересылается админу (пользователю ничего не отвечаем)."""
    db_add_user(m.from_user)
    if not ADMIN_ID or m.from_user.id == ADMIN_ID:
        return
    head = await bot.send_message(ADMIN_ID, f"✉️ Сообщение от {who(m.from_user)}:")
    fwd = await m.forward(ADMIN_ID)
    db_map(head.message_id, m.from_user.id)
    db_map(fwd.message_id, m.from_user.id)


async def main():
    if not BOT_TOKEN:
        raise SystemExit("Укажите BOT_TOKEN в файле .env")
    logging.basicConfig(level=logging.INFO)
    db_init()
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    global PREMIUM
    PREMIUM = await detect_premium(bot)
    logging.info("Premium-режим иконок: %s", PREMIUM)
    if PREMIUM:
        await load_icons(bot)
    # меню команд на трёх языках (ru — по умолчанию)
    await bot.set_my_commands(
        [BotCommand(command=c, description=d) for c, d in COMMANDS["ru"]],
        scope=BotCommandScopeDefault(),
    )
    for code, cmds in COMMANDS.items():
        await bot.set_my_commands(
            [BotCommand(command=c, description=d) for c, d in cmds],
            scope=BotCommandScopeDefault(),
            language_code=code,
        )
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
