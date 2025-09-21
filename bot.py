# bot.py — персональні нагадування + глобальний тиждень + автознищення повідомлень
import os, json, re
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
AUTODELETE_MINUTES = int(os.getenv("AUTODELETE_MINUTES", "10"))

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")
TZ = pytz.timezone(TZ_NAME)

bot = Bot(token=BOT_TOKEN, parse_mode="HTML")
dp = Dispatcher(bot)
scheduler = AsyncIOScheduler(timezone=TZ)

# ── AUTO-DELETE HELPERS ──────────────────────────────────────────────────────
async def delete_message_safe(chat_id: int, message_id: int):
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass

def schedule_autodelete(chat_id: int, message_id: int, minutes: Optional[int] = None):
    if minutes is None:
        minutes = AUTODELETE_MINUTES
    when = datetime.now(TZ) + timedelta(minutes=minutes)
    try:
        scheduler.add_job(
            delete_message_safe, "date",
            id=f"autodel:{chat_id}:{message_id}",
            run_date=when, args=[chat_id, message_id],
            misfire_grace_time=300, replace_existing=True
        )
    except Exception:
        pass

# ── PATHS ───────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

PRACTICAL_FILE = DATA_DIR / "practical.json"
LECTURE_FILE   = DATA_DIR / "lecture.json"
BELLS_FILE     = DATA_DIR / "bells.json"

LEGACY_STATE_FILE = DATA_DIR / "state.json"  # старий спільний файл
GLOBAL_FILE = DATA_DIR / "global.json"
USERS_FILE  = DATA_DIR / "users.json"

# ── CACHE ───────────────────────────────────────────────────────────────────
CACHE: Dict[str, Any] = {"practical": {}, "lecture": {}, "bells": {}}
UPLOAD_WAIT: Dict[int, str] = {}  # {admin_id: "practical"|"lecture"|"bells"}

# ── GLOBAL/USERS STATE ──────────────────────────────────────────────────────
def default_global() -> Dict[str, Any]:
    return {"week": "practical", "auto_rotate": True}

def load_users() -> Dict[str, Any]:
    if USERS_FILE.exists():
        try:
            d = json.loads(USERS_FILE.read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else {}
        except Exception:
            pass
    return {}

def save_users(all_users: Dict[str, Any]) -> None:
    USERS_FILE.write_text(json.dumps(all_users, ensure_ascii=False, indent=2), encoding="utf-8")

def default_user_state() -> Dict[str, Any]:
    return {"notify_hour_before": False, "notify_5min_before": False}

def load_user(chat_id: int) -> Dict[str, Any]:
    return load_users().get(str(chat_id), default_user_state())

def save_user(chat_id: int, ustate: Dict[str, Any]) -> None:
    users = load_users()
    users[str(chat_id)] = {
        "notify_hour_before": bool(ustate.get("notify_hour_before", False)),
        "notify_5min_before": bool(ustate.get("notify_5min_before", False)),
    }
    save_users(users)

def save_global(state: Dict[str, Any]) -> None:
    GLOBAL_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

def load_global() -> Dict[str, Any]:
    # Міграція зі старого state.json (якщо присутній)
    if LEGACY_STATE_FILE.exists():
        try:
            legacy = json.loads(LEGACY_STATE_FILE.read_text(encoding="utf-8"))
            g = {
                "week": legacy.get("week", "practical"),
                "auto_rotate": legacy.get("auto_rotate", True),
            }
            save_global(g)
            if legacy.get("chat_id") is not None:  # перенесемо старі прапорці одного юзера
                u = load_users()
                u[str(legacy["chat_id"])] = {
                    "notify_hour_before": legacy.get("notify_hour_before", False),
                    "notify_5min_before": legacy.get("notify_5min_before", False),
                }
                save_users(u)
            try:
                LEGACY_STATE_FILE.unlink()
            except Exception:
                pass
        except Exception:
            pass
    if GLOBAL_FILE.exists():
        try:
            d = json.loads(GLOBAL_FILE.read_text(encoding="utf-8"))
            d.setdefault("week", "practical")
            d.setdefault("auto_rotate", True)
            return d
        except Exception:
            pass
    return default_global()

# ── DATA LOADERS ────────────────────────────────────────────────────────────
def load_json_file(p: Path) -> Any:
    if not p.exists():
        return {}
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)

def reload_cache() -> None:
    CACHE["practical"] = load_json_file(PRACTICAL_FILE)
    CACHE["lecture"]   = load_json_file(LECTURE_FILE)
    CACHE["bells"]     = load_json_file(BELLS_FILE)

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

def toggle_week_value(week_key: str) -> str:
    return "practical" if week_key == "lecture" else "lecture"

def week_label(week_key: str) -> str:
    return "Лекційний" if week_key == "lecture" else "Практичний"

def _bell_range(pair_num: int) -> Optional[str]:
    bells = CACHE.get("bells") or {}
    return bells.get(str(pair_num))

def _pair_text(week_key: str, day_name: str, pair_num: int) -> str:
    items = (CACHE.get(week_key) or {}).get(day_name, [])
    for it in items:
        if int(it.get("pair", -1)) == int(pair_num):
            subj = it.get("subject", "")
            room = it.get("room", "")
            teacher = it.get("teacher", "")
            hours = _bell_range(pair_num) or ""
            tstr = f"\n🕒 {hours}" if hours else ""
            extra = f"\n👨‍🏫 {teacher}" if teacher else ""
            rstr = f"\n🚪 {room}" if room else ""
            return f"{PAIR_EMOJI.get(pair_num, str(pair_num))} <b>{subj}</b>{tstr}{rstr}{extra}"
    return f"{PAIR_EMOJI.get(pair_num, str(pair_num))} Пара №{pair_num}"

# ── RENDERERS ───────────────────────────────────────────────────────────────
def format_day(week_key: str, day_name: str, detailed: bool) -> str:
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
                f"🚪 {room}" + (f"\n👨‍🏫 {teacher}" if teacher else "")
            )
        else:
            lines.append(f"{PAIR_EMOJI.get(p, str(p))} <b>{subj}</b> — {room}")
    return "\n\n".join(lines)

def format_bells() -> str:
    bells = CACHE.get("bells") or {}
    if not bells:
        return "🔔 Розклад дзвінків ще не завантажено."
    lines = ["🔔 <b>Розклад дзвінків</b>"]
    for k in sorted(bells.keys(), key=lambda x: int(x)):
        lines.append(f"{PAIR_EMOJI.get(int(k), k)} {bells[k]}")
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
        kb.add(InlineKeyboardButton("🔎 Стисло", callback_data=f"sched:view:{week_key}:{day_name}:brief"))
    else:
        kb.add(InlineKeyboardButton("🔎 Детально", callback_data=f"sched:view:{week_key}:{day_name}:detail"))
    kb.add(
        InlineKeyboardButton("⬅️ Дні", callback_data=f"sched:week:{week_key}"),
        InlineKeyboardButton("🏠 Меню", callback_data="home"),
    )
    return kb

def kb_bells_back() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("⬅️ Назад", callback_data="home"))
    return kb

def kb_settings(user_state: Dict[str, Any], g: Dict[str, Any]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton(
            f"{'✅' if user_state.get('notify_hour_before') else '❌'} ⏰ За 1 год до першої",
            callback_data="settings:toggle:hour"),
        InlineKeyboardButton(
            f"{'✅' if user_state.get('notify_5min_before') else '❌'} ⌛ За 5 хв до кожної",
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
    return f"notif:{chat_id}:"

def _clear_notif_jobs(chat_id: int):
    pref = _notif_job_id_prefix(chat_id)
    for job in list(scheduler.get_jobs()):
        if job.id.startswith(pref):
            try:
                scheduler.remove_job(job.id)
            except Exception:
                pass

def _first_pair_today(week_key: str, day_name: str) -> Optional[int]:
    items = (CACHE.get(week_key) or {}).get(day_name, [])
    if not items:
        return None
    return min(int(x.get("pair", 99)) for x in items)

def _pairs_today(week_key: str, day_name: str) -> List[int]:
    items = (CACHE.get(week_key) or {}).get(day_name, [])
    return sorted(int(x.get("pair")) for x in items if "pair" in x)

async def _send_hour_before(chat_id: int, week_key: str, day_name: str, first_pair: int):
    try:
        text = f"""⏰ Нагадування: за 1 год до першої пари

{_pair_text(week_key, day_name, first_pair)}"""
        msg = await bot.send_message(chat_id, text)
        schedule_autodelete(chat_id, msg.message_id)
    except Exception:
        pass

async def _send_5min_before(chat_id: int, week_key: str, day_name: str, pair_num: int):
    try:
        text = f"""⌛ Нагадування: за 5 хв до пари

{_pair_text(week_key, day_name, pair_num)}"""
        msg = await bot.send_message(chat_id, text)
        schedule_autodelete(chat_id, msg.message_id)
    except Exception:
        pass

def schedule_today_notifications(chat_id: int):
    g = load_global()                 # глобальний тиждень
    u = load_user(chat_id)            # персональні прапорці
    week_key = g.get("week", "practical")
    dh = today_day_name(TZ)
    bells = CACHE.get("bells") or {}

    _clear_notif_jobs(chat_id)

    first_pair = _first_pair_today(week_key, dh)
    if first_pair is None:
        return

    now_tz = datetime.now(TZ)

    # За 1 годину до першої
    if u.get("notify_hour_before"):
        start_str = bells.get(str(first_pair))
        if start_str:
            h, m = parse_bell_start(start_str)
            dt = now_tz.replace(hour=h, minute=m, second=0, microsecond=0) - timedelta(hours=1)
            if dt > now_tz:
                scheduler.add_job(
                    _send_hour_before, "date",
                    id=f"{_notif_job_id_prefix(chat_id)}hour",
                    run_date=dt, args=[chat_id, week_key, dh, first_pair],
                    misfire_grace_time=300, replace_existing=True
                )

    # За 5 хв до кожної пари
    if u.get("notify_5min_before"):
        for p in _pairs_today(week_key, dh):
            start_str = bells.get(str(p))
            if not start_str:
                continue
            h, m = parse_bell_start(start_str)
            dt = now_tz.replace(hour=h, minute=m, second=0, microsecond=0) - timedelta(minutes=5)
            if dt > now_tz:
                scheduler.add_job(
                    _send_5min_before, "date",
                    id=f"{_notif_job_id_prefix(chat_id)}p{p}",
                    run_date=dt, args=[chat_id, week_key, dh, p],
                    misfire_grace_time=300, replace_existing=True
                )

# ── AUTO-WEEK ROTATION (ГЛОБАЛЬНО) ─────────────────────────────────────────
async def auto_rotate_job():
    g = load_global()
    if not g.get("auto_rotate", True):
        return
    g["week"] = toggle_week_value(g.get("week", "practical"))
    save_global(g)
    reload_cache()
    # Сповістити адміна
    try:
        await bot.send_message(ADMIN_ID, f"🔄 Автоматично встановлено тиждень: <b>{week_label(g['week'])}</b>")
    except Exception:
        pass
    # Перепланувати нагадування всім користувачам
    for uid in load_users().keys():
        try:
            schedule_today_notifications(int(uid))
        except Exception:
            pass

def schedule_fixed_jobs(chat_id: int):
    # Щодня о 00:10 — оновити нагадування на день для КОНКРЕТНОГО юзера
    scheduler.add_job(
        schedule_today_notifications,
        trigger="cron",
        id=f"{chat_id}:replan_daily",
        hour=0, minute=10,
        args=[chat_id],
        replace_existing=True,
        misfire_grace_time=300,
    )

def schedule_global_jobs():
    # авто-ротація щопонеділка 00:05 — одна джоба на весь бот
    scheduler.add_job(
        auto_rotate_job,
        trigger="cron",
        id="global:autorotate",
        day_of_week="mon",
        hour=0, minute=5,
        replace_existing=True,
        misfire_grace_time=300,
    )

# ── HANDLERS: HOME / START ─────────────────────────────────────────────────
@dp.message_handler(commands=["start"])
async def start(m: types.Message):
    u = load_user(m.chat.id)
    save_user(m.chat.id, u)  # no-op якщо вже є
    reload_cache()
    schedule_fixed_jobs(m.chat.id)
    schedule_today_notifications(m.chat.id)

    hello = "👋 Привіт! Я бот розкладу.\nОберіть дію:"
    await m.answer(hello, reply_markup=kb_main())

@dp.callback_query_handler(lambda c: c.data == "home")
async def go_home(c: CallbackQuery):
    await safe_edit(c.message, "🏠 Головне меню:", reply_markup=kb_main())
    await c.answer()

# ── HANDLERS: SCHEDULE ─────────────────────────────────────────────────────
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

# ── HANDLERS: BELLS ────────────────────────────────────────────────────────
@dp.callback_query_handler(lambda c: c.data == "bells:open")
async def cb_bells(c: CallbackQuery):
    txt = format_bells()
    await safe_edit(c.message, txt, reply_markup=kb_bells_back())
    await c.answer()

# ── HANDLERS: SETTINGS (REMINDERS ONLY) ─────────────────────────────────────
@dp.callback_query_handler(lambda c: c.data == "settings:open")
async def settings_open(c: CallbackQuery):
    g = load_global()
    u = load_user(c.message.chat.id)
    text = (
        "⚙️ <b>Налаштування</b>\n\n"
        f"• Поточний тиждень: <b>{week_label(g.get('week','practical'))}</b>\n"
        "• Увімкніть потрібні нагадування:"
    )
    await safe_edit(c.message, text, reply_markup=kb_settings(u, g))
    await c.answer()

@dp.callback_query_handler(lambda c: c.data.startswith("settings:toggle:"))
async def settings_toggle(c: CallbackQuery):
    kind = c.data.split(":", 2)[2]
    u = load_user(c.message.chat.id)
    if kind == "hour":
        u["notify_hour_before"] = not u.get("notify_hour_before", False)
    elif kind == "5min":
        u["notify_5min_before"] = not u.get("notify_5min_before", False)
    save_user(c.message.chat.id, u)
    reload_cache()
    schedule_today_notifications(c.message.chat.id)
    await c.answer("Збережено ✅")
    g = load_global()
    text = (
        "⚙️ <b>Налаштування</b>\n\n"
        f"• Поточний тиждень: <b>{week_label(g.get('week','practical'))}</b>\n"
        "• Увімкніть потрібні нагадування:"
    )
    await safe_edit(c.message, text, reply_markup=kb_settings(u, g))

# ── HANDLERS: ADMIN ────────────────────────────────────────────────────────
@dp.message_handler(commands=["admin"])
async def admin_panel(m: types.Message):
    if m.from_user.id != ADMIN_ID:
        await m.reply("⛔ Ви не адміністратор цього бота.")
        return
    g = load_global()
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton("📤 Завантажити поточні JSON", callback_data="admin:download"),
        InlineKeyboardButton("📥 Оновити practical.json", callback_data="admin:upload:practical"),
        InlineKeyboardButton("📥 Оновити lecture.json",   callback_data="admin:upload:lecture"),
        InlineKeyboardButton("📥 Оновити bells.json",     callback_data="admin:upload:bells"),
    )
    kb.add(
        InlineKeyboardButton(f"♻️ Перемкнути тиждень (зараз: {week_label(g.get('week','practical'))})", callback_data="admin:toggle_week"),
        InlineKeyboardButton("🔁 Авто-ротація: " + ("УВІМК" if g.get("auto_rotate", True) else "ВИМК"), callback_data="admin:toggle_auto"),
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
                try:
                    await bot.send_document(c.from_user.id, InputFile(str(path), filename=name))
                    sent = True
                except Exception:
                    pass
        if not sent:
            await c.answer("Немає файлів для відправки", show_alert=True)
        else:
            await c.answer("Відправлено")
        return

    if action.startswith("upload:"):
        _, kind = action.split(":", 1)
        UPLOAD_WAIT[c.from_user.id] = kind
        await safe_edit(c.message, f"Надішліть файл <b>{kind}.json</b> одним документом у відповідь на це повідомлення.")
        await c.answer()
        return

    if action == "toggle_week":
        g = load_global()
        g["week"] = toggle_week_value(g.get("week", "practical"))
        save_global(g); reload_cache()
        await safe_edit(c.message, f"✅ Перемкнуто на: <b>{week_label(g['week'])}</b>", reply_markup=None)
        for uid in load_users().keys():
            try:
                schedule_today_notifications(int(uid))
            except Exception:
                pass
        await c.answer("Готово")
        return

    if action == "toggle_auto":
        g = load_global()
        g["auto_rotate"] = not g.get("auto_rotate", True)
        save_global(g); reload_cache()
        await safe_edit(c.message, "Збережено ✅", reply_markup=None)
        await c.answer()
        return

    if action == "close":
        await safe_edit(c.message, "Адмін-панель закрито.", reply_markup=None)
        await c.answer()
        return

def _validate_payload(kind: str, data: Any):
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
        await m.reply("Немає активного запиту на завантаження. Відкрий /admin → 'Оновити ...'")
        return

    tmp = DATA_DIR / f"__upload_{kind}.json"
    await m.document.download(destination=str(tmp))

    try:
        data = json.loads(tmp.read_text(encoding="utf-8"))
        ok, msg = _validate_payload(kind, data)
        if not ok:
            await m.reply("❌ " + msg)
            tmp.unlink(missing_ok=True)
            return
        if kind == "practical":
            PRACTICAL_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        elif kind == "lecture":
            LECTURE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        elif kind == "bells":
            BELLS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        reload_cache()
        await m.reply("✅ Оновлено")
    except Exception as e:
        await m.reply(f"❌ Помилка: {e}")
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
    UPLOAD_WAIT.pop(m.from_user.id, None)

# ── STARTUP ─────────────────────────────────────────────────────────────────
async def on_startup(dp: Dispatcher):
    reload_cache()
    schedule_global_jobs()

    # Стартуємо щоденний replan для всіх відомих юзерів
    for uid in load_users().keys():
        try:
            cid = int(uid)
            schedule_fixed_jobs(cid)
            schedule_today_notifications(cid)
        except Exception:
            pass

    scheduler.start()

if __name__ == "__main__":
    executor.start_polling(dp, on_startup=on_startup, skip_updates=True)
