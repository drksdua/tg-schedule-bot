import os, json
from aiogram import Bot, Dispatcher, types, executor
from dotenv import load_dotenv

# ========= базові =========
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("У .env нема BOT_TOKEN")

DEFAULT_GROUP = "CS-101"   # команда /group є, але не світимо її у текстах

bot = Bot(BOT_TOKEN, parse_mode="HTML")
dp = Dispatcher(bot)

# ========= дані =========
with open("schedule.json", "r", encoding="utf-8") as f:
    DATA = json.load(f)
SCHED = DATA["groups"]

# Вибір тижня та останній обраний день на чат
WEEK_KIND = {}   # {chat_id: "lecture" | "practical"}
LAST_DAY  = {}   # {chat_id: 1..7}

DAY_NAMES = {
    1: "Понеділок", 2: "Вівторок", 3: "Середа",
    4: "Четвер", 5: "П’ятниця", 6: "Субота", 7: "Неділя"
}

# --- розклад дзвінків (1 курс магістратури) ---
BELLS_M1 = [
    ("1️⃣ пара", "09:00–10:20"),
    ("2️⃣ пара", "10:30–11:50"),
    ("3️⃣ пара", "12:20–13:40"),
    ("4️⃣ пара", "13:50–15:10"),
    ("5️⃣ пара", "15:20–16:40"),
    ("6️⃣ пара", "16:50–18:10"),
]

def bells_text(bells):
    parts = ["🔔 <b>Розклад дзвінків</b> <i>(магістратура — 1 курс)</i>", ""]
    for i, (name, time) in enumerate(bells, 1):
        parts.append(f"{name}: ⏰ <b>{time}</b>")
        if i != len(bells):
            parts.append("· · ·")
    return "\n".join(parts)

# ===== helpers (гарне форматування) =====
def lessons_for(group: str, day: int, kind: str):
    return SCHED.get(group, {}).get(kind, {}).get(str(day), [])

def header(day_title: str, kind: str) -> str:
    nice = "Лекційний" if kind == "lecture" else "Практичний"
    return f"🗓️ <b>{day_title}</b>\n🏷️ <i>{nice} тиждень</i>"

def format_summary(lessons, day_title: str, kind: str) -> str:
    head = header(day_title, kind)
    if not lessons:
        return f"{head}\n\nПар немає 🎉"
    lines = [head, ""]
    for l in lessons:
        lines.append(f"⏰ <b>{l['start']}-{l['end']}</b> — 📘 {l['title']}")
    return "\n".join(lines)

def format_details(lessons, day_title: str, kind: str) -> str:
    head = header(day_title, kind)
    if not lessons:
        return f"{head}\n\nПар немає 🎉"
    parts = [head, ""]
    for idx, l in enumerate(lessons, start=1):
        teacher = l.get("teacher", "—")
        room = l.get("room", "—")
        parts += [
            f"📚 <b>Пара {idx}</b>",
            f"   ⏰ <b>{l['start']}-{l['end']}</b>",
            f"   📘 <b>{l['title']}</b>",
            f"   👤 <i>{teacher}</i>",
            f"   🏫 {room}",
        ]
        if idx != len(lessons):
            parts.append("—" * 20)
    return "\n".join(parts)

def lecture_placeholder_text() -> str:
    return ("😅 <b>Упс…</b>\n"
            "Розкладу <b>лекційного</b> тижня поки немає — очікуйте оновлення 🙏")

# ========= клавіатури =========
def kb_start():
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("▶️ Почати", callback_data="begin"))
    return kb

def kb_main_menu():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("📚 Лекційний", callback_data="set_kind:lecture"),
        types.InlineKeyboardButton("🧪 Практичний", callback_data="set_kind:practical"),
    )
    kb.add(types.InlineKeyboardButton("🔔 Розклад дзвінків", callback_data="bells_m1"))
    return kb

def kb_days():
    kb = types.InlineKeyboardMarkup(row_width=3)
    day_emoji = {1:"🌤️", 2:"🌤️", 3:"🌤️", 4:"🌤️", 5:"🎉"}
    buttons = [types.InlineKeyboardButton(f"{day_emoji.get(i,'')} {DAY_NAMES[i]}", callback_data=f"day:{i}") for i in range(1, 6)]
    kb.add(*buttons)
    kb.add(types.InlineKeyboardButton("🏠 У головне меню", callback_data="to_menu"))
    return kb

def kb_day_summary():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("⬅️ Назад до днів", callback_data="back_days"),
        types.InlineKeyboardButton("ℹ️ Детальніше", callback_data="details"),
    )
    return kb

def kb_day_details():
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("⬅️ Назад до днів", callback_data="back_days"))
    return kb

def kb_lecture_placeholder():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("🧪 Перейти до практичного", callback_data="set_kind:practical"),
        types.InlineKeyboardButton("🏠 У меню", callback_data="to_menu")
    )
    return kb

# ========= хендлери =========
@dp.message_handler(commands=["start"])
async def start_cmd(m: types.Message):
    await m.answer("Привіт! 👋 Натисни, щоб почати:", reply_markup=kb_start())

@dp.message_handler(commands=["help"])
async def help_cmd(m: types.Message):
    await m.answer("Натисни «Почати», обери тип тижня і день.", reply_markup=kb_start())

@dp.message_handler(commands=["group"])
async def cmd_group(m: types.Message):
    global DEFAULT_GROUP
    parts = m.text.split(maxsplit=1)
    if len(parts) != 2:
        await m.answer("Приклад: <code>/group CS-101</code>")
        return
    new_group = parts[1].strip()
    if new_group in SCHED:
        DEFAULT_GROUP = new_group
        await m.answer(f"Групу змінено на <b>{DEFAULT_GROUP}</b>.", reply_markup=kb_main_menu())
    else:
        await m.answer("Такої групи нема. Доступні: " + ", ".join(SCHED.keys()))

# --- навігація
@dp.callback_query_handler(lambda c: c.data == "begin")
async def begin(c: types.CallbackQuery):
    await c.message.edit_text("Оберіть тип тижня:", reply_markup=kb_main_menu())
    await c.answer()

@dp.callback_query_handler(lambda c: c.data == "to_menu")
async def to_menu(c: types.CallbackQuery):
    await c.message.edit_text("Оберіть тип тижня:", reply_markup=kb_main_menu())
    await c.answer()

@dp.callback_query_handler(lambda c: c.data == "bells_m1")
async def show_bells(c: types.CallbackQuery):
    await c.message.edit_text(
        bells_text(BELLS_M1),
        reply_markup=types.InlineKeyboardMarkup().add(
            types.InlineKeyboardButton("⬅️ Назад у меню", callback_data="to_menu")
        )
    )
    await c.answer("Дзвінки відкрито")

@dp.callback_query_handler(lambda c: c.data.startswith("set_kind:"))
async def set_kind(c: types.CallbackQuery):
    kind = c.data.split(":")[1]  # lecture | practical
    WEEK_KIND[c.message.chat.id] = kind

    if kind == "lecture":
        # КОСТИЛЬ: показати плейсхолдер замість переходу до вибору дня
        await c.message.edit_text(lecture_placeholder_text(), reply_markup=kb_lecture_placeholder())
        await c.answer("Розклад лекційного тижня ще не додано")
        return

    # practical — як завжди
    await c.message.edit_text("Обрано: <b>Практичний</b> тиждень.\nОберіть день:", reply_markup=kb_days())
    await c.answer()

@dp.callback_query_handler(lambda c: c.data == "back_days")
async def back_days(c: types.CallbackQuery):
    await c.message.edit_text("Оберіть день (Пн–Пт):", reply_markup=kb_days())
    await c.answer()

@dp.callback_query_handler(lambda c: c.data.startswith("day:"))
async def day_pick(c: types.CallbackQuery):
    day = int(c.data.split(":")[1])
    LAST_DAY[c.message.chat.id] = day
    kind = WEEK_KIND.get(c.message.chat.id)

    # Якщо чомусь обраний lecture — дублюємо плейсхолдер
    if kind == "lecture":
        await c.message.edit_text(lecture_placeholder_text(), reply_markup=kb_lecture_placeholder())
        await c.answer()
        return

    if not kind:
        await c.message.edit_text("Спершу оберіть тип тижня:", reply_markup=kb_main_menu())
        await c.answer()
        return

    lessons = lessons_for(DEFAULT_GROUP, day, kind)
    text = format_summary(lessons, f"{DAY_NAMES[day]}", kind)
    await c.message.edit_text(text, reply_markup=kb_day_summary())
    await c.answer()

@dp.callback_query_handler(lambda c: c.data == "details")
async def show_details(c: types.CallbackQuery):
    kind = WEEK_KIND.get(c.message.chat.id)
    day  = LAST_DAY.get(c.message.chat.id)

    # на всяк випадок — якщо lecture
    if kind == "lecture":
        await c.message.edit_text(lecture_placeholder_text(), reply_markup=kb_lecture_placeholder())
        await c.answer()
        return

    if not kind or not day:
        await c.message.edit_text("Спершу оберіть тип тижня та день:", reply_markup=kb_main_menu())
        await c.answer()
        return

    lessons = lessons_for(DEFAULT_GROUP, day, kind)
    text = format_details(lessons, f"{DAY_NAMES[day]}", kind)
    await c.message.edit_text(text, reply_markup=kb_day_details())
    await c.answer("Показую деталі")

# ========= запуск =========
if __name__ == "__main__":
    print("Starting bot…")
    executor.start_polling(dp, skip_updates=True)