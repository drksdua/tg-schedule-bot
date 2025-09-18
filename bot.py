# bot.py
import os, json, asyncio, re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, InputFile
from aiogram.utils import executor
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
import pytz

# ── ENV ──────────────────────────────────────────────────────────────────────
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
TZ_NAME = os.getenv("TZ", "Europe/Kyiv")
TZ = pytz.timezone(TZ_NAME)

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")

bot = Bot(token=BOT_TOKEN, parse_mode="HTML")
dp = Dispatcher(bot)
scheduler = AsyncIOScheduler(timezone=TZ)

# ── PATHS ───────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

PRACTICAL_FILE = DATA_DIR / "practical.json"
LECTURE_FILE   = DATA_DIR / "lecture.json"
BELLS_FILE     = DATA_DIR / "bells.json"
STATE_FILE     = DATA_DIR / "state.json"

# ── CACHE ───────────────────────────────────────────────────────────────────
CACHE: Dict[str, Any] = {
    "practical": {},
    "lecture": {},
    "bells": {},
    "state": {}
}
UPLOAD_WAIT: Dict[int, str] = {}  # очікування файлу від адміна: {user_id: "practical"|...}

# ── STATE ───────────────────────────────────────────────────────────────────
def default_state() -> Dict[str, Any]:
    # за замовчуванням: практичний, автозміна щопонеділка, нагадування вимкнені
    return {
        "chat_id": None,
        "week": "practical",
        "auto_rotate": True,
        "notify_hour_before": False,
        "notify_5min_before": False,
    }

def load_state() -> Dict[str, Any]:
    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            data.setdefault("auto_rotate", True)
            data.setdefault("notify_hour_before", False)
            data.setdefault("notify_5min_before", False)
            return data
        except Exception:
            pass
    return default_state()

def save_state(state: Dict[str, Any]) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    CACHE["state"] = state

def toggle_week_value(week_key: str) -> str:
    return "practical" if week_key == "lecture" else "lecture"

def week_label(week_key: str) -> str:
    return "Лекційний" if week_key == "lecture" else "Практичний"

# ── DATA ────────────────────────────────────────────────────────────────────
def load_json_file(p: Path) -> Any:
    if not p.exists():
        return {}
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)

def reload_cache() -> None:
    CACHE["practical"] = load_json_file(PRACTICAL_FILE)
    CACHE["lecture"]   = load_json_file(LECTURE_FILE)
    CACHE["bells"]     = load_json_file(BELLS_FILE)
    CACHE["state"]     = load_state()

# ── HELPERS ─────────────────────────────────────────────────────────────────
PAIR_EMOJI = {1:"1️⃣",2:"2️⃣",3:"3️⃣",4:"4️⃣",5:"5️⃣",6:"6️⃣",7:"7️⃣",8:"8️⃣"}
UA_DAYS = ["Понеділок","Вівторок","Середа","Четвер","Пʼятниця","Субота","Неділя"]

def today_day_name(tz: pytz.timezone) -> str:
    now = datetime.now(tz)
    idx = (now.weekday() + 0) % 7  # 0=Mon
    return UA_DAYS[idx]

def parse_bell_start(bell_val: str) -> Tuple[int,int]:
    # "09:00-10:20" -> (9,0)
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*-\s*\d{1,2}:\d{2}\s*$", bell_val)
    if not m:
        raise ValueError(f"Bad bell time: {bell_val}")
    return int(m.group(1)), int(m.group(2))

def _bell_range(pair_num: int) -> Optional[str]:
    bells = CACHE.get("bells") or {}
    return bells.get(str(pair_num))

def format_day(week_key: str, day_name: str, detailed: bool) -> str:
    """Додає години з дзвінків у детальному режимі."""
    data = CACHE[week_key] or {}
    pairs: List[Dict[str, Any]] = data.get(day_name, [])
    head = f"📆 <b>{day_name}</b> • {week_label(week_key)} тиждень"
    if not pairs:
        return f"{head}\n— пар немає 🙂"
    lines = [head]
    for it in sorted(pairs, key=lambda x: int(x.get("pair", 0))):
        p = int(it.get("pair", 0))
        subj = it.get("subject", "")
        room = it.get("room", "")
        if detailed:
            teacher = it.get("teacher", "")
            hours = _bell_range(p)
            time_str = f"\n🕒 {hours}" if hours else ""
            lines.append(
                f"{PAIR_EMOJI.get(p, str(p))} <b>{subj}</b>{time_str}\n"
                f"🏫 {room}{('  •  👤 '+teacher) if teacher else ''}"
            )
        else:
            tail = f" — {room}" if room else ""
            lines.append(f"{PAIR_EMOJI.get(p, str(p))} {subj}{tail}")
    return "\n".join(lines)

def format_bells() -> str:
    bells = CACHE["bells"] or {}
    if not bells:
        return "🔔 Розклад дзвінків наразі порожній."
    lines = ["🔔 <b>Розклад дзвінків</b> (Магістр 1)"]
    for k in sorted(bells.keys(), key=lambda x: int(x)):
        lines.append(f"{PAIR_EMOJI.get(int(k), k)} {bells[k]}")
    lines.append("\n⬅️ Повернутися: натисніть «Назад» нижче.")
    return "\n".join(lines)

# ── KEYBOARDS ───────────────────────────────────────────────────────────────
def kb_main() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("📚 Розклад пар", callback_data="sched:open"))
    kb.add(InlineKeyboardButton("🔔 Розклад дзвінків", callback_data="bells:open"))
    kb.add(InlineKeyboardButton("⚙️ Налаштування", callback_data="settings:open"))
    return kb

def kb_sched_weeks() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("🧪 Практичний", callback_data="sched:week:practical"),
        InlineKeyboardButton("📖 Лекційний",  callback_data="sched:week:lecture"),
    )
    kb.add(InlineKeyboardButton("🏠 В головне меню", callback_data="home"))
    return kb

def kb_sched_days(week_key: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=3)
    kb.row(
        InlineKeyboardButton("Пн", callback_data=f"sched:day:{week_key}:Понеділок"),
        InlineKeyboardButton("Вт", callback_data=f"sched:day:{week_key}:Вівторок"),
        InlineKeyboardButton("Ср", callback_data=f"sched:day:{week_key}:Середа"),
    )
    kb.row(
        InlineKeyboardButton("Чт", callback_data=f"sched:day:{week_key}:Четвер"),
        InlineKeyboardButton("Пт", callback_data=f"sched:day:{week_key}:Пʼятниця"),
    )
    kb.add(
        InlineKeyboardButton("⬅️ Назад до тижнів", callback_data="sched:open"),
        InlineKeyboardButton("🏠 В головне меню", callback_data="home"),
    )
    return kb

def kb_day_view(week_key: str, day_name: str, detailed: bool) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=2)
    if detailed:
        kb.add(InlineKeyboardButton("🔙 Коротко", callback_data=f"sched:view:{week_key}:{day_name}:short"))
    else:
        kb.add(InlineKeyboardButton("🔎 Детально", callback_data=f"sched:view:{week_key}:{day_name}:detail"))
    kb.add(
        InlineKeyboardButton("⬅️ Назад до днів", callback_data=f"sched:week:{week_key}"),
        InlineKeyboardButton("🏠 В головне меню", callback_data="home"),
    )
    return kb

def kb_bells_back() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("⬅️ Назад", callback_data="home"))
    return kb

def kb_settings(state: Dict[str, Any]) -> InlineKeyboardMarkup:
    """Тільки нагадування — без авто-ротації для користувачів."""
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton(
            f"{'✅' if state.get('notify_hour_before') else '❌'} ⏰ За 1 год до першої",
            callback_data="settings:toggle:hour"),
        InlineKeyboardButton(
            f"{'✅' if state.get('notify_5min_before') else '❌'} ⌛ За 5 хв до кожної",
            callback_data="settings:toggle:5min"),
    )
    kb.add(InlineKeyboardButton("⬅️ Назад", callback_data="home"))
    return kb

# ── SAFE EDIT ───────────────────────────────────────────────────────────────
async def safe_edit(message: types.Message, text: str, reply_markup: Optional[InlineKeyboardMarkup] = None):
    try:
        await message.edit_text(text, reply_markup=reply_markup, disable_web_page_preview=True)
    except Exception as e:
        if "Message is not modified" in str(e):
            return
        await message.answer(text, reply_markup=reply_markup, disable_web_page_preview=True)

# ── NOTIFICATIONS SCHEDULING ────────────────────────────────────────────────
def _notif_job_id_prefix(chat_id: int) -> str:
    return f"{chat_id}:notif:"

def _clear_notif_jobs(chat_id: int):
    pref = _notif_job_id_prefix(chat_id)
    for job in list(scheduler.get_jobs()):
        if job.id.startswith(pref):
            scheduler.remove_job(job.id)

def _first_pair_today(week_key: str, day_name: str) -> Optional[int]:
    items = (CACHE.get(week_key) or {}).get(day_name, [])
    if not items:
        return None
    return min(int(x.get("pair", 99)) for x in items)

def _pairs_today(week_key: str, day_name: str) -> List[int]:
    items = (CACHE.get(week_key) or {}).get(day_name, [])
    return sorted(int(x.get("pair")) for x in items if "pair" in x)

def _pair_text(week_key: str, day_name: str, pair_num: int) -> str:
    items = (CACHE.get(week_key) or {}).get(day_name, [])
    for it in items:
        if int(it.get("pair", -1)) == int(pair_num):
            subj = it.get("subject","")
            room = it.get("room","")
            teacher = it.get("teacher","")
            hours = _bell_range(pair_num)
            time_line = f"\n🕒 {hours}" if hours else ""
            return f"{PAIR_EMOJI.get(pair_num,str(pair_num))} <b>{subj}</b>{time_line}\n🏫 {room}{'  •  👤 '+teacher if teacher else ''}"
    return f"Пара №{pair_num}"

async def _send_hour_before(chat_id: int, week_key: str, day_name: str, pair_num: int):
    txt = f"⏰ <b>За 1 годину</b> почнеться перша пара сьогодні:\n{_pair_text(week_key, day_name, pair_num)}"
    await bot.send_message(chat_id, txt)

async def _send_5min_before(chat_id: int, week_key: str, day_name: str, pair_num: int):
    txt = f"⌛ <b>Через 5 хв</b> стартує:\n{_pair_text(week_key, day_name, pair_num)}"
    await bot.send_message(chat_id, txt)

def schedule_today_notifications(chat_id: int):
    """Планує нагадування на поточний день згідно state/bells/schedule."""
    state = load_state()
    week_key = state.get("week", "practical")
    dh = today_day_name(TZ)
    bells = CACHE.get("bells") or {}

    _clear_notif_jobs(chat_id)

    first_pair = _first_pair_today(week_key, dh)
    if first_pair is None:
        return

    # 1) За годину до першої
    if state.get("notify_hour_before"):
        start_str = bells.get(str(first_pair))
        if start_str:
            h, m = parse_bell_start(start_str)
            dt = TZ.localize(datetime.now().replace(hour=h, minute=m, second=0, microsecond=0)) - timedelta(hours=1)
            if dt > datetime.now(TZ):
                scheduler.add_job(
                    _send_hour_before, "date",
                    id=f"{_notif_job_id_prefix(chat_id)}hour",
                    run_date=dt, args=[chat_id, week_key, dh, first_pair],
                    misfire_grace_time=300, replace_existing=True
                )

    # 2) За 5 хв до кожної
    if state.get("notify_5min_before"):
        for p in _pairs_today(week_key, dh):
            start_str = bells.get(str(p))
            if not start_str:
                continue
            h, m = parse_bell_start(start_str)
            dt = TZ.localize(datetime.now().replace(hour=h, minute=m, second=0, microsecond=0)) - timedelta(minutes=5)
            if dt > datetime.now(TZ):
                scheduler.add_job(
                    _send_5min_before, "date",
                    id=f"{_notif_job_id_prefix(chat_id)}p{p}",
                    run_date=dt, args=[chat_id, week_key, dh, p],
                    misfire_grace_time=300, replace_existing=True
                )

# ── AUTO-WEEK ROTATION ─────────────────────────────────────────────────────
async def auto_rotate_job(chat_id: int):
    state = load_state()
    if state.get("chat_id") != chat_id or not state.get("auto_rotate", True):
        return
    state["week"] = toggle_week_value(state.get("week","practical"))
    save_state(state)
    reload_cache()
    try:
        await bot.send_message(chat_id, f"🔄 Автоматично встановлено тиждень: <b>{week_label(state['week'])}</b>")
    except Exception:
        pass
    schedule_today_notifications(chat_id)

def schedule_fixed_jobs(chat_id: int):
    # авто-ротація щопонеділка 00:05
    scheduler.add_job(
        auto_rotate_job,
        trigger="cron",
        id=f"{chat_id}:autorotate",
        day_of_week="mon",
        hour=0, minute=5,
        args=[chat_id],
        replace_existing=True,
        misfire_grace_time=300,
    )
    # Щодня о 00:10 — оновити нагадування на день
    scheduler.add_job(
        schedule_today_notifications,
        trigger="cron",
        id=f"{chat_id}:replan_daily",
        hour=0, minute=10,
        args=[chat_id],
        replace_existing=True,
        misfire_grace_time=300,
    )

# ── START / HOME ────────────────────────────────────────────────────────────
@dp.message_handler(commands=["start"])
async def start(m: types.Message):
    st = load_state()
    if not st.get("chat_id"):
        st["chat_id"] = m.chat.id
        save_state(st)
        reload_cache()
        schedule_fixed_jobs(m.chat.id)
        schedule_today_notifications(m.chat.id)
    hello = (
        "👋 Привіт! Я бот розкладу.\n"
        "Оберіть дію:"
    )
    await m.answer(hello, reply_markup=kb_main())

@dp.callback_query_handler(lambda c: c.data == "home")
async def cb_home(c: CallbackQuery):
    await safe_edit(c.message, "🏠 <b>Головне меню</b>:", reply_markup=kb_main())
    await c.answer()

# ── SCHEDULE FLOW (week -> day -> view) ─────────────────────────────────────
@dp.callback_query_handler(lambda c: c.data == "sched:open")
async def sched_open(c: CallbackQuery):
    await safe_edit(c.message, "📚 <b>Розклад пар</b>\nОберіть тип тижня:", reply_markup=kb_sched_weeks())
    await c.answer()

@dp.callback_query_handler(lambda c: c.data.startswith("sched:week:"))
async def sched_week(c: CallbackQuery):
    _, _, week_key = c.data.split(":")
    await safe_edit(c.message, f"📅 Оберіть день • <b>{week_label(week_key)}</b> тиждень", reply_markup=kb_sched_days(week_key))
    await c.answer()

@dp.callback_query_handler(lambda c: c.data.startswith("sched:day:"))
async def sched_day(c: CallbackQuery):
    _, _, week_key, day_name = c.data.split(":", 3)
    text = format_day(week_key, day_name, detailed=False)
    await safe_edit(c.message, text, reply_markup=kb_day_view(week_key, day_name, detailed=False))
    await c.answer()

@dp.callback_query_handler(lambda c: c.data.startswith("sched:view:"))
async def sched_view_toggle(c: CallbackQuery):
    _, _, week_key, day_name, mode = c.data.split(":", 4)
    detailed = (mode == "detail")
    text = format_day(week_key, day_name, detailed=detailed)
    await safe_edit(c.message, text, reply_markup=kb_day_view(week_key, day_name, detailed=detailed))
    await c.answer()

# ── BELLS ───────────────────────────────────────────────────────────────────
@dp.callback_query_handler(lambda c: c.data == "bells:open")
async def cb_bells(c: CallbackQuery):
    txt = format_bells()
    await safe_edit(c.message, txt, reply_markup=kb_bells_back())
    await c.answer()

# ── SETTINGS (reminders only) ───────────────────────────────────────────────
@dp.callback_query_handler(lambda c: c.data == "settings:open")
async def settings_open(c: CallbackQuery):
    st = load_state()
    text = (
        "⚙️ <b>Налаштування</b>\n\n"
        f"• Поточний тиждень: <b>{week_label(st.get('week','practical'))}</b>\n"
        "• Увімкніть потрібні нагадування:"
    )
    await safe_edit(c.message, text, reply_markup=kb_settings(st))
    await c.answer()

@dp.callback_query_handler(lambda c: c.data.startswith("settings:toggle:"))
async def settings_toggle(c: CallbackQuery):
    st = load_state()
    kind = c.data.split(":", 2)[2]
    if kind == "hour":
        st["notify_hour_before"] = not st.get("notify_hour_before", False)
    elif kind == "5min":
        st["notify_5min_before"] = not st.get("notify_5min_before", False)
    save_state(st); reload_cache()

    if st.get("chat_id"):
        schedule_today_notifications(st["chat_id"])

    text = (
        "⚙️ <b>Налаштування</b>\n\n"
        f"• Поточний тиждень: <b>{week_label(st.get('week','practical'))}</b>\n"
        "• Оновлено✅. За потреби перемкніть інші опції:"
    )
    await safe_edit(c.message, text, reply_markup=kb_settings(st))
    await c.answer("Збережено")

# ── ADMIN ───────────────────────────────────────────────────────────────────
@dp.message_handler(lambda m: m.text and m.text.strip().lower() in ("/admin", "//admin"))
async def admin_entry(m: types.Message):
    if m.from_user.id != ADMIN_ID:
        await m.reply("⛔ Ви не адміністратор цього бота.")
        return
    st = load_state()
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton("📤 Завантажити поточні JSON", callback_data="admin:download"),
        InlineKeyboardButton("📥 Оновити practical.json", callback_data="admin:upload:practical"),
        InlineKeyboardButton("📥 Оновити lecture.json",   callback_data="admin:upload:lecture"),
        InlineKeyboardButton("📥 Оновити bells.json",     callback_data="admin:upload:bells"),
    )
    kb.add(
        InlineKeyboardButton(f"♻️ Перемкнути тиждень (зараз: {week_label(st.get('week','practical'))})", callback_data="admin:toggle_week"),
        InlineKeyboardButton("🔁 Авто-ротація: " + ("УВІМК" if st.get("auto_rotate", True) else "ВИМК"),
                             callback_data="admin:toggle_auto"),
    )
    kb.add(InlineKeyboardButton("❌ Закрити", callback_data="admin:close"))
    await m.answer("🔐 Адмін-панель:", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("admin:"))
async def admin_actions(c: CallbackQuery):
    if c.from_user.id != ADMIN_ID:
        await c.answer("⛔ Ви не адміністратор цього бота.", show_alert=True)
        return
    action = c.data.split(":",1)[1]

    if action == "download":
        sent = False
        for name, path in (("practical.json", PRACTICAL_FILE), ("lecture.json", LECTURE_FILE), ("bells.json", BELLS_FILE)):
            if path.exists():
                await bot.send_document(c.message.chat.id, InputFile(str(path), filename=name))
                sent = True
        if not sent:
            await c.answer("Немає файлів", show_alert=True)
        else:
            await c.answer("Надіслано файли")
        return

    if action.startswith("upload:"):
        kind = action.split(":",1)[1]  # practical|lecture|bells
        UPLOAD_WAIT[c.from_user.id] = kind
        await safe_edit(
            c.message,
            f"📥 Надішли файл <b>{kind}.json</b> у відповідь на це повідомлення.\n"
            "Я перевірю JSON і, якщо все ок, заміню файл і перезавантажу дані.",
            reply_markup=None
        )
        await c.answer("Чекаю файл")
        return

    if action == "toggle_week":
        st = load_state()
        st["week"] = toggle_week_value(st.get("week", "practical"))
        save_state(st); reload_cache()
        await safe_edit(c.message, f"✅ Перемкнуто на: <b>{week_label(st['week'])}</b>", reply_markup=None)
        if st.get("chat_id"):
            schedule_today_notifications(st["chat_id"])
        await c.answer("Готово")
        return

    if action == "toggle_auto":
        st = load_state()
        st["auto_rotate"] = not st.get("auto_rotate", True)
        save_state(st); reload_cache()
        await safe_edit(c.message, "Збережено ✅", reply_markup=None)
        await c.answer()
        return

    if action == "close":
        await safe_edit(c.message, "Адмін-панель закрито.", reply_markup=None)
        await c.answer()
        return

# прийом JSON від адміна
def validate_schedule_payload(kind: str, data: Any):
    try:
        if kind in ("practical","lecture"):
            if not isinstance(data, dict): return False, "Очікується обʼєкт { 'Понеділок': [ ... ] }"
            for day, items in data.items():
                if not isinstance(items, list): return False, f"{day}: очікується список"
                for it in items:
                    if not isinstance(it, dict): return False, f"{day}: елементи мають бути обʼєктами"
                    if "pair" not in it or "subject" not in it: return False, f"{day}: потрібні поля 'pair' і 'subject'"
        elif kind == "bells":
            if not isinstance(data, dict): return False, "Очікується обʼєкт { '1': '09:00-10:20', ... }"
            for k,v in data.items():
                int(k)
                if not isinstance(v, str): return False, "Час має бути рядком"
        else:
            return False, "Невідомий тип"
    except Exception as e:
        return False, f"Помилка валідації: {e}"
    return True, "OK"

@dp.message_handler(content_types=types.ContentType.DOCUMENT)
async def on_doc(m: types.Message):
    if m.from_user.id != ADMIN_ID:
        await m.reply("⛔ Ви не адміністратор цього бота.")
        return
    kind = UPLOAD_WAIT.get(m.from_user.id)
    if not kind:
        return
    doc = m.document
    if not doc.file_name.lower().endswith(".json"):
        await m.reply("⚠️ Надішли саме JSON-файл.")
        return
    tmp = DATA_DIR / f"__upload_{m.from_user.id}_{doc.file_name}"
    await doc.download(destination_file=tmp)
    try:
        data = json.loads(tmp.read_text(encoding="utf-8"))
    except Exception as e:
        tmp.unlink(missing_ok=True)
        await m.reply(f"❌ Не вдалося прочитати JSON: {e}")
        return
    ok, msg = validate_schedule_payload(kind, data)
    if not ok:
        tmp.unlink(missing_ok=True)
        await m.reply(f"❌ Невалідний JSON: {msg}")
        return
    target = {"practical": PRACTICAL_FILE, "lecture": LECTURE_FILE, "bells": BELLS_FILE}[kind]
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.unlink(missing_ok=True)
    reload_cache()
    UPLOAD_WAIT.pop(m.from_user.id, None)
    await m.reply(f"✅ Оновлено <b>{target.name}</b>. Дані перезавантажено.")
    st = load_state()
    if st.get("chat_id"):
        schedule_today_notifications(st["chat_id"])

# ── STARTUP ─────────────────────────────────────────────────────────────────
async def on_startup(dp: Dispatcher):
    reload_cache()
    st = CACHE["state"]
    if st.get("chat_id"):
        schedule_fixed_jobs(st["chat_id"])
        schedule_today_notifications(st["chat_id"])
    scheduler.start()

if __name__ == "__main__":
    executor.start_polling(dp, on_startup=on_startup, skip_updates=True)
