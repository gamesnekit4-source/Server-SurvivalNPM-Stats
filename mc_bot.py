"""
Telegram-бот статистики Minecraft-сервера (с красивым оформлением).

pip install aiogram mcstatus
export BOT_TOKEN="токен от @BotFather"
python mc_bot.py
"""
import asyncio
import html
import logging
import os
import sqlite3
import time

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import BotCommand, CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from mcstatus import JavaServer
from aiogram.client.session.aiohttp import AiohttpSession

BOT_TOKEN = "9233315887:AAHFpEBFbA_AbXrb6ibJXNpIQR49jcnUGXY"
DEFAULT_SERVER = "213.171.18.152:32074"
POLL_INTERVAL = 60
KEEP_DAYS = 30

logging.basicConfig(level=logging.INFO)
session = AiohttpSession(proxy="socks5://127.0.0.1:10808")
bot = Bot(BOT_TOKEN, session=session, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

db = sqlite3.connect("mc_stats.db")
db.execute("CREATE TABLE IF NOT EXISTS samples (ts INTEGER, up INTEGER, online INTEGER, max INTEGER, ping REAL)")
db.execute("CREATE TABLE IF NOT EXISTS subs (chat_id INTEGER PRIMARY KEY)")
db.commit()

LINE = "━━━━━━━━━━━━━━━━"


# ---------- Утилиты оформления ----------
def bar(cur: int, mx: int, n: int = 12) -> str:
    filled = round(cur / mx * n) if mx else 0
    return "▰" * filled + "▱" * (n - filled)


def spark(values: list[int]) -> str:
    chars = "▁▂▃▄▅▆▇█"
    mx = max(values) if values and max(values) > 0 else 1
    return "".join(chars[min(7, int(v / mx * 7))] if v > 0 else "▁" for v in values)


def ping_icon(ms: float) -> str:
    return "🟢" if ms < 60 else "🟡" if ms < 150 else "🔴"


def esc(s: str) -> str:
    return html.escape(str(s))


def is_sub(chat_id: int) -> bool:
    return db.execute("SELECT 1 FROM subs WHERE chat_id=?", (chat_id,)).fetchone() is not None


# ---------- Клавиатуры ----------
def menu_kb(chat_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="📡 Статус", callback_data="status")
    b.button(text="👥 Игроки", callback_data="players")
    b.button(text="📊 Статистика", callback_data="stats")
    b.button(text="🔕 Выкл. уведомл." if is_sub(chat_id) else "🔔 Вкл. уведомл.", callback_data="sub")
    b.adjust(2, 2)
    return b.as_markup()


def back_kb(refresh: str | None) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if refresh:
        b.button(text="🔄 Обновить", callback_data=refresh)
    b.button(text="⬅️ Меню", callback_data="menu")
    b.adjust(2)
    return b.as_markup()


# ---------- Запрос к серверу ----------
async def query(addr: str):
    try:
        return await JavaServer.lookup(addr, timeout=5).async_status()
    except Exception as e:
        logging.info("query %s failed: %s", addr, e)
        return None


def motd_text(status) -> str:
    try:
        return status.motd.to_plain().strip()
    except Exception:
        return ""


def player_names(status) -> list[str]:
    return [p.name for p in (status.players.sample or [])]


# ---------- Экраны (возвращают текст) ----------
def menu_text() -> str:
    return (
        "⛏ <b>Minecraft Stats</b>\n"
        f"{LINE}\n"
        f"🌍 Сервер: <code>{DEFAULT_SERVER}</code>\n\n"
        "Выбери действие 👇"
    )


async def status_text(addr: str) -> str:
    s = await query(addr)
    if not s:
        return f"🔴 <b>Сервер офлайн</b>\n{LINE}\n<code>{esc(addr)}</code>\n\nНе отвечает на запросы 😵"
    p = s.players
    out = [
        "🟢 <b>Сервер онлайн</b>",
        LINE,
        f"<code>{esc(addr)}</code>",
    ]
    motd = motd_text(s)
    if motd:
        out.append(f"<i>{esc(motd)}</i>")
    out += [
        "",
        f"🎮 Версия: <b>{esc(s.version.name)}</b>",
        f"👥 Онлайн: <b>{p.online}/{p.max}</b>",
        f"<code>{bar(p.online, p.max)}</code>",
        f"📶 Пинг: {ping_icon(s.latency)} <b>{s.latency:.0f} мс</b>",
    ]
    names = player_names(s)
    if names:
        out += ["", "👤 " + ", ".join(f"<code>{esc(n)}</code>" for n in names)]
    return "\n".join(out)


async def players_text() -> str:
    s = await query(DEFAULT_SERVER)
    if not s:
        return f"🔴 <b>Сервер офлайн</b>\n{LINE}\nСписок игроков недоступен"
    names = player_names(s)
    head = f"👥 <b>Игроки онлайн: {s.players.online}/{s.players.max}</b>\n{LINE}\n"
    if s.players.online == 0:
        return head + "😴 Сейчас никого нет"
    if not names:
        return head + "Сервер скрывает список ников 🕶"
    return head + "\n".join(f"▫️ <code>{esc(n)}</code>" for n in names)


def stats_text() -> str:
    now = int(time.time())
    since = now - 24 * 3600
    rows = db.execute("SELECT ts, up, online, ping FROM samples WHERE ts >= ?", (since,)).fetchall()
    if not rows:
        return "📊 <b>Статистика</b>\n" + LINE + "\nПока нет данных, подожди пару минут ⏳"

    up_rows = [r for r in rows if r[1]]
    uptime = len(up_rows) / len(rows) * 100
    peak = max((r[2] for r in up_rows), default=0)
    avg = sum(r[2] for r in up_rows) / len(up_rows) if up_rows else 0
    ping = sum(r[3] for r in up_rows) / len(up_rows) if up_rows else 0

    buckets = [0] * 24
    for ts, up, online, _ in rows:
        i = min(23, (ts - since) // 3600)
        if up:
            buckets[i] = max(buckets[i], online)

    return (
        "📊 <b>Статистика за 24 часа</b>\n"
        f"{LINE}\n"
        f"⏱ Аптайм: <b>{uptime:.1f}%</b>\n"
        f"<code>{bar(round(uptime), 100)}</code>\n\n"
        f"📈 Пик онлайна: <b>{peak}</b>\n"
        f"👥 Средний онлайн: <b>{avg:.1f}</b>\n"
        f"📶 Средний пинг: {ping_icon(ping)} <b>{ping:.0f} мс</b>\n\n"
        "🕒 <b>Онлайн по часам</b>\n"
        f"<code>{spark(buckets)}</code>\n"
        "<code>−24ч            сейчас</code>"
    )


# ---------- Отправка / редактирование ----------
async def show(cb: CallbackQuery, text: str, kb: InlineKeyboardMarkup):
    try:
        await cb.message.edit_text(text, reply_markup=kb)
        await cb.answer()
    except TelegramBadRequest:
        await cb.answer("Без изменений ✅")


# ---------- Команды ----------
@dp.message(Command("start", "help", "menu"))
async def cmd_start(m: Message):
    await m.answer(menu_text(), reply_markup=menu_kb(m.chat.id))


@dp.message(Command("status"))
async def cmd_status(m: Message, command: CommandObject):
    addr = (command.args or "").strip()
    if addr:
        await m.answer(await status_text(addr), reply_markup=back_kb(None))
    else:
        await m.answer(await status_text(DEFAULT_SERVER), reply_markup=back_kb("status"))


@dp.message(Command("players"))
async def cmd_players(m: Message):
    await m.answer(await players_text(), reply_markup=back_kb("players"))


@dp.message(Command("stats"))
async def cmd_stats(m: Message):
    await m.answer(stats_text(), reply_markup=back_kb("stats"))


@dp.message(Command("subscribe"))
async def cmd_sub(m: Message):
    db.execute("INSERT OR IGNORE INTO subs VALUES (?)", (m.chat.id,))
    db.commit()
    await m.answer("🔔 <b>Уведомления включены</b>\nНапишу, когда сервер упадёт или поднимется")


@dp.message(Command("unsubscribe"))
async def cmd_unsub(m: Message):
    db.execute("DELETE FROM subs WHERE chat_id=?", (m.chat.id,))
    db.commit()
    await m.answer("🔕 <b>Уведомления выключены</b>")


# ---------- Кнопки ----------
@dp.callback_query(F.data == "menu")
async def cb_menu(cb: CallbackQuery):
    await show(cb, menu_text(), menu_kb(cb.message.chat.id))


@dp.callback_query(F.data == "status")
async def cb_status(cb: CallbackQuery):
    await show(cb, await status_text(DEFAULT_SERVER), back_kb("status"))


@dp.callback_query(F.data == "players")
async def cb_players(cb: CallbackQuery):
    await show(cb, await players_text(), back_kb("players"))


@dp.callback_query(F.data == "stats")
async def cb_stats(cb: CallbackQuery):
    await show(cb, stats_text(), back_kb("stats"))


@dp.callback_query(F.data == "sub")
async def cb_sub(cb: CallbackQuery):
    chat_id = cb.message.chat.id
    if is_sub(chat_id):
        db.execute("DELETE FROM subs WHERE chat_id=?", (chat_id,))
        note = "🔕 Уведомления выключены"
    else:
        db.execute("INSERT OR IGNORE INTO subs VALUES (?)", (chat_id,))
        note = "🔔 Уведомления включены"
    db.commit()
    await cb.message.edit_reply_markup(reply_markup=menu_kb(chat_id))
    await cb.answer(note)


# ---------- Фоновый сбор статистики ----------
async def poller():
    last_up = None
    while True:
        s = await query(DEFAULT_SERVER)
        now = int(time.time())
        up = s is not None
        db.execute(
            "INSERT INTO samples VALUES (?,?,?,?,?)",
            (now, int(up), s.players.online if up else 0, s.players.max if up else 0, s.latency if up else 0),
        )
        db.execute("DELETE FROM samples WHERE ts < ?", (now - KEEP_DAYS * 86400,))
        db.commit()

        if last_up is not None and up != last_up:
            msg = (
                f"🟢 <b>Сервер снова онлайн!</b>\n{LINE}\n<code>{DEFAULT_SERVER}</code>\nЗаходи играть ⛏"
                if up else
                f"🔴 <b>Сервер упал!</b>\n{LINE}\n<code>{DEFAULT_SERVER}</code>\nСкоро проверим снова…"
            )
            for (chat_id,) in db.execute("SELECT chat_id FROM subs").fetchall():
                try:
                    await bot.send_message(chat_id, msg)
                except Exception as e:
                    logging.warning("notify %s failed: %s", chat_id, e)
        last_up = up
        await asyncio.sleep(POLL_INTERVAL)


async def main():
    await bot.set_my_commands([
        BotCommand(command="menu", description="Главное меню"),
        BotCommand(command="status", description="Статус сервера"),
        BotCommand(command="players", description="Кто онлайн"),
        BotCommand(command="stats", description="Статистика за 24ч"),
        BotCommand(command="subscribe", description="Включить уведомления"),
        BotCommand(command="unsubscribe", description="Выключить уведомления"),
    ])
    asyncio.create_task(poller())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

