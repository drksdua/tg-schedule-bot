# bot.py
import json
import os
from pathlib import Path
from typing import Dict, List, Any
from contextlib import suppress

from aiogram import Bot, Dispatcher, types, executor
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.utils.exceptions import MessageNotModified
from dotenv import load_dotenv

# ------- ENV -------
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не знайдено у .env")

bot = Bot(BOT_TOKEN, parse_mode=types.ParseMode.HTML)
dp = Dispatcher(bot)

# ------- Константи -------
DATA_DIR = Path("data")
FILES = {
    "practical": DATA_DIR / "practical.json",
    "lecture": DATA_DIR / "lecture.json",
}
DAYS = ["Понеділок", "Вівторок", "Середа", "Четвер", "Пʼятниця"]

# Кеш розкладів
SCHEDULES: Dict[str, Any] = {}

# ------- Утиліти -------
def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)

def load_schedules() -> None:
    """Читає обидва файли розкладу в кеш."""
    global SCHEDULES
    loaded = {}
    for key, path in FILES.items():
        try:
            loaded[key] = load_json(path)
        except Exception as e:
            loaded[key] = {"_message": f"Помилка читання {path.name}: {e}"}
    SCHEDULES = loaded

def get_day_pairs(week_key: str, day_name: str) -> List[Dict[str, Any]]:
    data = SCHEDULES.get(week_key, {})
    if "_message" in data:
        return []
    return data.get(day_name, [])

def format_pairs_short(pairs: List[Dict[str, Any]]) -> str:
    if not pairs:
        return "❌ Пар немає."
    lines = []
    for p in pairs:
        pair_no = p.get("pair")
        subj = p.get("subject", "—")
        lines.append(f"• <b>{pair_no} пара</b>: {subj}")
    return "\n".join(lines)

def format_pairs_detailed(pairs: List[Dict[str, Any]]) -> str:
    if not pairs:
        return "❌ Пар немає."
    lines = []
    for p in pairs:
        pair_no = p.get("pair")
        subj = p.get("subject", "—")
        teacher = p.get("teacher", "—")
        room = p.get("room", "—")
        lines.append(
            f"📚 <b>{pair_no} пара</b>\n"
            f"   • Предмет: <b>{subj}</b>\n"
            f"   • Викладач: {teacher}\n"
            f"   • Аудиторія: {room}"
        )
    return "\n\n".join(lines)

def bells_text() -> str:
    # Магістри 1 курс (права колонка твого фото)
    return (
        "⏰ <b>Розклад дзвінків (магістри 1 курс)</b>\n\n"
        "1️⃣ 09:00–10:20\n"
        "— перерва 10 хв —\n"
        "2️⃣ 10:30–11:50\n"
        "— перерва 30 хв —\n"
        "3️⃣ 12:20–13:40\n"
        "— перерва 10 хв —\n"
        "4️⃣ 13:50–15:10\n"
        "— перерва 10 хв —\n"
        "5️⃣ 15:20–16:40\n"
        "— перерва 10 хв —\n"
        "6️⃣ 16:50–18:10"
    )

# Безпечне редагування (глушить MessageNotModified)
async def safe_edit(message: types.Message, text: str, **kwargs):
    with suppress(MessageNotModified):
        return await message.edit_text(text, **kwargs)

# ------- Клавіатури -------
def kb_main() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup()
    kb.add(
        InlineKeyboardButton("📘 Лекційний тиждень", callback_data="week:lecture"),
        InlineKeyboardButton("🛠️ Практичний тиждень", callback_data="week:practical"),
    )
    kb.add(InlineKeyboardButton("⏰ Розклад дзвінків", callback_data="bells"))
    return kb

def kb_home_only() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("🏠 В головне меню", callback_data="home"))
    return kb

def kb_days(week_key: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=3)
    buttons = [InlineKeyboardButton(d, callback_data=f"day:{week_key}:{d}") for d in DAYS]
    kb.add(*buttons)
    kb.add(InlineKeyboardButton("🏠 В головне меню", callback_data="home"))
    return kb

def kb_day_actions(week_key: str, day_name: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("ℹ️ Детальніше", callback_data=f"detail:{week_key}:{day_name}"))
    kb.add(
        InlineKeyboardButton("⬅️ Назад до днів", callback_data=f"back_days:{week_key}"),
        InlineKeyboardButton("🏠 Меню", callback_data="home"),
    )
    return kb

# ------- Команди -------
@dp.message_handler(commands=["start"])
async def start(m: types.Message):
    await m.answer("Привіт! 👋 Обери режим:", reply_markup=kb_main())

@dp.message_handler(commands=["bells"])
async def cmd_bells(m: types.Message):
    await m.answer(bells_text(), reply_markup=kb_home_only(), disable_web_page_preview=True)

@dp.message_handler(commands=["reload"])
async def cmd_reload(m: types.Message):
    load_schedules()
    await m.answer("🔄 Розклади перезавантажено з файлів.")

# ------- Callback-и -------
@dp.callback_query_handler(lambda c: c.data == "home")
async def cb_home(c: CallbackQuery):
    await safe_edit(c.message, "Головне меню:", reply_markup=kb_main())
    await c.answer("Вже тут ✅")

@dp.callback_query_handler(lambda c: c.data == "bells")
async def cb_bells(c: CallbackQuery):
    await safe_edit(c.message, bells_text(), reply_markup=kb_home_only(), disable_web_page_preview=True)
    await c.answer("Розклад дзвінків відкритий 🔔")

@dp.callback_query_handler(lambda c: c.data.startswith("week:"))
async def cb_week(c: CallbackQuery):
    _, week_key = c.data.split(":", 1)
    data = SCHEDULES.get(week_key, {})
    if "_message" in data:
        await safe_edit(
            c.message,
            f"ℹ️ {data['_message']}",
            reply_markup=kb_home_only()
        )
        await c.answer("Наразі лекційний розклад відсутній ℹ️")
    else:
        title = "🛠️ Практичний тиждень" if week_key == "practical" else "📘 Лекційний тиждень"
        await safe_edit(c.message, f"{title}\n\nОберіть день:", reply_markup=kb_days(week_key))
        await c.answer()

@dp.callback_query_handler(lambda c: c.data.startswith("back_days:"))
async def cb_back_days(c: CallbackQuery):
    _, week_key = c.data.split(":", 1)
    title = "🛠️ Практичний тиждень" if week_key == "practical" else "📘 Лекційний тиждень"
    await safe_edit(c.message, f"{title}\n\nОберіть день:", reply_markup=kb_days(week_key))
    await c.answer("Повернув до списку днів ↩️")

@dp.callback_query_handler(lambda c: c.data.startswith("day:"))
async def cb_day(c: CallbackQuery):
    _, week_key, day_name = c.data.split(":", 2)
    pairs = get_day_pairs(week_key, day_name)
    text = f"📅 <b>{day_name}</b>\n\n{format_pairs_short(pairs)}"
    await safe_edit(c.message, text, reply_markup=kb_day_actions(week_key, day_name))
    await c.answer()

@dp.callback_query_handler(lambda c: c.data.startswith("detail:"))
async def cb_detail(c: CallbackQuery):
    _, week_key, day_name = c.data.split(":", 2)
    pairs = get_day_pairs(week_key, day_name)
    text = f"📅 <b>{day_name}</b>\n\n{format_pairs_detailed(pairs)}"
    kb = InlineKeyboardMarkup()
    kb.add(
        InlineKeyboardButton("⬅️ Назад до днів", callback_data=f"back_days:{week_key}"),
        InlineKeyboardButton("🏠 Меню", callback_data="home"),
    )
    await safe_edit(c.message, text, reply_markup=kb)
    await c.answer()

# ------- Старт -------
if __name__ == "__main__":
    load_schedules()
    print("Starting bot…")
    executor.start_polling(dp, skip_updates=True)
