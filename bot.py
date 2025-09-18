# bot.py
import json
import os
from pathlib import Path
from typing import Dict, List, Any
from contextlib import suppress
from datetime import datetime, time, timedelta

import pytz
from aiogram import Bot, Dispatcher, types, executor
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.utils.exceptions import MessageNotModified
from apscheduler.schedulers.asyncio import AsyncIOScheduler
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
STATE_FILE = DATA_DIR / "state.json"   # {"chat_id": int, "week": "practical"|"lecture", "notify": true}
DAYS = ["Понеділок", "Вівторок", "Середа", "Четвер", "Пʼятниця"]
DAY_TO_CRON = {
    "Понеділок": "mon",
    "Вівторок": "tue",
    "Середа": "wed",
    "Четвер": "thu",
    "Пʼятниця": "fri",
}
TZ = pytz.timezone("Europe/Kyiv")

# Старт пар (магістри 1 курс)
BELL_START: Dict[int, time] = {
    1: time(9, 0),
    2: time(10, 30),
    3: time(12, 20),
    4: time(13, 50),
    5: time(15, 20),
    6: time(16, 50),
}

# Кеш розкладів
SCHEDULES: Dict[str, Any] = {}

# APScheduler
scheduler = AsyncIOScheduler(timezone=TZ)

# ------- Утиліти збереження стану -------
def load_state() -> Dict[str, Any]:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"chat_id": None, "week": "practical", "notify": False}

def save_state(state: Dict[str, Any]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

# ------- Утиліти розкладу -------
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

def today_day_name(dt: datetime) -> str:
    # 0=Mon..6=Sun
    idx = dt.weekday()
    return DAYS[idx] if idx < 5 else "Вихідний"

def safe_get_first_pair(pairs: List[Dict[str, Any]]) -> Dict[str, Any] | None:
    if not pairs:
        return None
    return sorted(pairs, key=lambda x: x.get("pair", 99))[0]

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
    kb.add(InlineKeyboardButton("🔔 Нагадування", callback_data="notify:menu"))
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

def kb_week_select(prefix: str = "setweek") -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup()
    kb.add(
        InlineKeyboardButton("📘 Лекційний", callback_data=f"{prefix}:lecture"),
        InlineKeyboardButton("🛠️ Практичний", callback_data=f"{prefix}:practical"),
    )
    kb.add(InlineKeyboardButton("🏠 В головне меню", callback_data="home"))
    return kb

def kb_notify_menu(state: Dict[str, Any]) -> InlineKeyboardMarkup:
    enabled = state.get("notify", False)
    kb = InlineKeyboardMarkup()
    if enabled:
        kb.add(InlineKeyboardButton("🔕 Вимкнути нагадування", callback_data="notify:off"))
    else:
        kb.add(InlineKeyboardButton("🔔 Увімкнути нагадування", callback_data="notify:on"))
    kb.add(InlineKeyboardButton("🗓 Обрати тиждень", callback_data="setweek:menu"))
    kb.add(InlineKeyboardButton("🏠 В головне меню", callback_data="home"))
    return kb

# ------- Планування (APScheduler) -------
def remove_jobs_for_chat(chat_id: int):
    for job in scheduler.get_jobs():
        if job.id.startswith(f"{chat_id}:"):
            scheduler.remove_job(job.id)

def schedule_static_jobs_for_chat(chat_id: int):
    """
    Ставимо:
      - щоденно (пн-пт) тригери на 5 хв до кожної пари (1..6)
      - щоденно (пн-пт) тригер за 1 год до ПЕРШОЇ пари
      - щонеділі 18:00 — запитати тиждень
    Логіка “яка пара є сьогодні” визначається під час виконання — за актуальним state.week.
    """
    # 5 хв до кожної пари (пн-пт)
    for day_name, cron_day in DAY_TO_CRON.items():
        for pair_no, t in BELL_START.items():
            h, m = t.hour, t.minute
            # 5 хв до початку
            send_m = (datetime(2000,1,1,h,m) - timedelta(minutes=5)).minute
            send_h = (datetime(2000,1,1,h,m) - timedelta(minutes=5)).hour
            job_id = f"{chat_id}:warn5m:{cron_day}:{pair_no}"
            scheduler.add_job(
                notify_5min_before_pair,
                trigger="cron",
                id=job_id,
                day_of_week=cron_day,
                hour=send_h,
                minute=send_m,
                args=[chat_id, pair_no],
                replace_existing=True,
                misfire_grace_time=60,
            )
        # 1 година до першої пари — поставимо на фіксований час 08:00 (бо перша пара 09:00)
        job_id = f"{chat_id}:warn1h:{cron_day}"
        scheduler.add_job(
            notify_1h_before_first_pair,
            trigger="cron",
            id=job_id,
            day_of_week=cron_day,
            hour=8,
            minute=0,
            args=[chat_id],
            replace_existing=True,
            misfire_grace_time=300,
        )

    # Неділя 18:00 — запитати тиждень
    scheduler.add_job(
        ask_week_on_sunday,
        trigger="cron",
        id=f"{chat_id}:askweek:sun",
        day_of_week="sun",
        hour=18,
        minute=0,
        args=[chat_id],
        replace_existing=True,
        misfire_grace_time=300,
    )

async def notify_1h_before_first_pair(chat_id: int):
    state = load_state()
    if not state.get("notify") or state.get("chat_id") != chat_id:
        return
    # Сьогоднішній день
    now = datetime.now(TZ)
    day_name = today_day_name(now)
    if day_name not in DAYS:
        return
    pairs = get_day_pairs(state.get("week", "practical"), day_name)
    first_p = safe_get_first_pair(pairs)
    if not first_p:
        return
    subj = first_p.get("subject", "—")
    room = first_p.get("room", "—")
    pair_no = first_p.get("pair")
    txt = (
        f"⏰ <b>Через 1 годину</b> почнеться пара {pair_no} — <b>{subj}</b>\n"
        f"🏫 Аудиторія: {room}"
    )
    try:
        await bot.send_message(chat_id, txt)
    except Exception:
        pass

async def notify_5min_before_pair(chat_id: int, pair_no: int):
    state = load_state()
    if not state.get("notify") or state.get("chat_id") != chat_id:
        return
    now = datetime.now(TZ)
    day_name = today_day_name(now)
    if day_name not in DAYS:
        return
    pairs = get_day_pairs(state.get("week", "practical"), day_name)
    target = [p for p in pairs if p.get("pair") == pair_no]
    if not target:
        return
    p = target[0]
    subj = p.get("subject", "—")
    room = p.get("room", "—")
    txt = (
        f"⏳ <b>За 5 хв</b> почнеться пара {pair_no} — <b>{subj}</b>\n"
        f"🏫 Аудиторія: {room}"
    )
    try:
        await bot.send_message(chat_id, txt)
    except Exception:
        pass

async def ask_week_on_sunday(chat_id: int):
    state = load_state()
    if state.get("chat_id") != chat_id:
        return
    txt = (
        "🗓 <b>Вибір тижня на наступний цикл</b>\n"
        "Оберіть, будь ласка, який тиждень актуальний із понеділка:"
    )
    try:
        await bot.send_message(chat_id, txt, reply_markup=kb_week_select(prefix="setweek"))
    except Exception:
        pass

def reschedule_for_chat(chat_id: int, ensure_started=True):
    remove_jobs_for_chat(chat_id)
    schedule_static_jobs_for_chat(chat_id)
    if ensure_started and not scheduler.running:
        scheduler.start()

# ------- Команди -------
@dp.message_handler(commands=["start"])
async def start(m: types.Message):
    state = load_state()
    state["chat_id"] = m.chat.id
    save_state(state)
    # гарантуємо розклад задач
    reschedule_for_chat(m.chat.id)
    await m.answer("Привіт! 👋 Обери режим:", reply_markup=kb_main())

@dp.message_handler(commands=["bells"])
async def cmd_bells(m: types.Message):
    await m.answer(bells_text(), reply_markup=kb_home_only(), disable_web_page_preview=True)

@dp.message_handler(commands=["reload"])
async def cmd_reload(m: types.Message):
    load_schedules()
    await m.answer("🔄 Розклади перезавантажено з файлів.")

@dp.message_handler(commands=["notify_on"])
async def cmd_notify_on(m: types.Message):
    state = load_state()
    state["chat_id"] = m.chat.id
    state["notify"] = True
    save_state(state)
    reschedule_for_chat(m.chat.id)
    await m.answer("🔔 Нагадування увімкнено.\n"
                   "• За 1 годину до першої пари дня\n"
                   "• За 5 хв до кожної пари")

@dp.message_handler(commands=["notify_off"])
async def cmd_notify_off(m: types.Message):
    state = load_state()
    state["notify"] = False
    save_state(state)
    await m.answer("🔕 Нагадування вимкнено.")

@dp.message_handler(commands=["setweek"])
async def cmd_setweek(m: types.Message):
    await m.answer("Оберіть активний тиждень:", reply_markup=kb_week_select())

@dp.message_handler(commands=["weekstatus"])
async def cmd_weekstatus(m: types.Message):
    state = load_state()
    cur = state.get("week", "practical")
    flag = "увімкнені" if state.get("notify") else "вимкнені"
    await m.answer(f"ℹ️ Активний тиждень: <b>{'Практичний' if cur=='practical' else 'Лекційний'}</b>\n"
                   f"🔔 Нагадування: {flag}")

# ------- Callback-и (навігація) -------
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
        await safe_edit(c.message, f"ℹ️ {data['_message']}", reply_markup=kb_home_only())
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

# ------- Callback-и (setweek / notify) -------
@dp.callback_query_handler(lambda c: c.data == "setweek:menu")
async def cb_setweek_menu(c: CallbackQuery):
    await safe_edit(c.message, "Оберіть активний тиждень:", reply_markup=kb_week_select())
    await c.answer()

@dp.callback_query_handler(lambda c: c.data.startswith("setweek:"))
async def cb_setweek(c: CallbackQuery):
    _, week_key = c.data.split(":", 1)
    if week_key not in ("lecture", "practical"):
        await c.answer("Невідомий тип тижня", show_alert=True)
        return
    state = load_state()
    state["week"] = week_key
    # якщо ще не записаний chat_id — запишемо
    state["chat_id"] = state.get("chat_id") or c.message.chat.id
    save_state(state)
    # Перепланувати джоби (часи ті самі, логіка читає актуальний state)
    reschedule_for_chat(state["chat_id"])
    await safe_edit(
        c.message,
        f"✅ Тиждень встановлено: <b>{'Лекційний' if week_key=='lecture' else 'Практичний'}</b>",
        reply_markup=kb_main()
    )
    await c.answer("Збережено ✅")

@dp.callback_query_handler(lambda c: c.data == "notify:menu")
async def cb_notify_menu(c: CallbackQuery):
    state = load_state()
    await safe_edit(c.message, "Налаштування нагадувань:", reply_markup=kb_notify_menu(state))
    await c.answer()

@dp.callback_query_handler(lambda c: c.data == "notify:on")
async def cb_notify_on(c: CallbackQuery):
    state = load_state()
    state["chat_id"] = c.message.chat.id
    state["notify"] = True
    save_state(state)
    reschedule_for_chat(state["chat_id"])
    await safe_edit(c.message, "🔔 Нагадування увімкнено.", reply_markup=kb_notify_menu(state))
    await c.answer("Увімкнено 🔔")

@dp.callback_query_handler(lambda c: c.data == "notify:off")
async def cb_notify_off(c: CallbackQuery):
    state = load_state()
    state["notify"] = False
    save_state(state)
    await safe_edit(c.message, "🔕 Нагадування вимкнено.", reply_markup=kb_notify_menu(state))
    await c.answer("Вимкнено 🔕")

# ------- Старт -------
if __name__ == "__main__":
    load_schedules()
    # Піднімаємо scheduler одразу, і якщо в state вже є chat_id+notify — розкладемо задачі
    st = load_state()
    if st.get("chat_id"):
        reschedule_for_chat(st["chat_id"], ensure_started=False)
    if not scheduler.running:
        scheduler.start()
    print("Starting bot…")
    executor.start_polling(dp, skip_updates=True)
