"""Seriallar: foydalanuvchi uchun qismlar ro'yxati va admin panel orqali boshqarish.

Jadvallar (mavjud `video` jadvaliga tegmaydi):
  series(id = serial kodi, title, description, poster_file_id, views, created_at)
  series_episodes(series_id, number, file_id, kind)  — kind: 'video' yoki 'document'

Serial kodi kino kodlari bilan bir xil "nomlar maydoni"da: foydalanuvchi kod yuborsa,
avval kino, topilmasa serial qidiriladi. Shuning uchun kino kodi bilan to'qnashuv tekshiriladi.
"""
import html
import re
import threading
import time

from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup

import config
from config import bot, conn, cursor, get_admin, get_film, join

cursor.execute(
    "CREATE TABLE IF NOT EXISTS series("
    "id INTEGER PRIMARY KEY, title TEXT NOT NULL, description TEXT, poster_file_id TEXT, "
    "views INTEGER DEFAULT 0, created_at INTEGER)"
)
cursor.execute(
    "CREATE TABLE IF NOT EXISTS series_episodes("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, series_id INTEGER NOT NULL, number INTEGER NOT NULL, "
    "file_id TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'video', UNIQUE(series_id, number))"
)
cursor.execute("CREATE INDEX IF NOT EXISTS idx_series_episodes ON series_episodes(series_id, number)")

EPISODES_PER_PAGE = 30   # 6 qator x 5 tugma
EPISODES_PER_ROW = 5
ADMIN_LIST_PAGE = 10

_db_lock = threading.Lock()
# Admin qism yuklash rejimida: {chat_id: series_id}
UPLOAD_MODE = {}
# Yangi serial yaratish qoralamasi: {chat_id: {...}}
DRAFTS = {}


# ---------------- Ma'lumotlar ----------------

def get_series(code):
    try:
        code = int(code)
    except (TypeError, ValueError):
        return None
    return cursor.execute(
        "SELECT id, title, description, poster_file_id, views FROM series WHERE id=?", (code,)
    ).fetchone()


def episode_numbers(sid):
    return [r[0] for r in cursor.execute(
        "SELECT number FROM series_episodes WHERE series_id=? ORDER BY number", (sid,)
    ).fetchall()]


def get_episode(sid, number):
    return cursor.execute(
        "SELECT number, file_id, kind FROM series_episodes WHERE series_id=? AND number=?", (sid, number)
    ).fetchone()


def all_series(offset=0, limit=None):
    sql = (
        "SELECT s.id, s.title, s.description, s.poster_file_id, s.views, COUNT(e.id) "
        "FROM series s LEFT JOIN series_episodes e ON e.series_id = s.id "
        "GROUP BY s.id ORDER BY s.created_at DESC, s.id DESC"
    )
    if limit is not None:
        return cursor.execute(sql + " LIMIT ? OFFSET ?", (limit, offset)).fetchall()
    return cursor.execute(sql).fetchall()


def series_count():
    return cursor.execute("SELECT COUNT(*) FROM series").fetchone()[0]


def code_taken(code):
    return get_film(code) is not None or get_series(code) is not None


def add_episode(sid, file_id, kind, number=None):
    """Qism qo'shadi. number berilmasa — navbatdagi raqam. (raqam, yangi_mi) qaytaradi."""
    with _db_lock:
        if number is None:
            number = (cursor.execute(
                "SELECT COALESCE(MAX(number), 0) FROM series_episodes WHERE series_id=?", (sid,)
            ).fetchone()[0]) + 1
        existed = get_episode(sid, number) is not None
        cursor.execute(
            "INSERT INTO series_episodes(series_id, number, file_id, kind) VALUES(?, ?, ?, ?) "
            "ON CONFLICT(series_id, number) DO UPDATE SET file_id=excluded.file_id, kind=excluded.kind",
            (sid, number, file_id, kind),
        )
        conn.commit()
    return number, not existed


def delete_episode(sid, number):
    with _db_lock:
        deleted = cursor.execute("DELETE FROM series_episodes WHERE series_id=? AND number=?", (sid, number)).rowcount
        conn.commit()
        return deleted > 0


def delete_series(sid):
    with _db_lock:
        cursor.execute("DELETE FROM series_episodes WHERE series_id=?", (sid,))
        cursor.execute("DELETE FROM series WHERE id=?", (sid,))
        conn.commit()


# ---------------- Foydalanuvchi ko'rinishi ----------------

def series_caption(s, count):
    sid, title, description, _poster, views = s
    parts = [f"📺 <b>{html.escape(title)}</b>"]
    if description:
        parts.append(f"\n{html.escape(description)}")
    parts.append(f"\n🎞 Qismlar: <b>{count} ta</b>")
    parts.append(f"👁 Ko'rishlar: {int(views or 0)}")
    parts.append(f"🆔 Serial kodi: <code>{sid}</code>")
    parts.append("\n👇 Kerakli qismni tanlang:" if count else "\n⏳ Qismlar tez orada qo'shiladi.")
    return "\n".join(parts)


def episodes_key(sid, page=0, current=None):
    numbers = episode_numbers(sid)
    pages = max((len(numbers) - 1) // EPISODES_PER_PAGE + 1, 1)
    page = min(max(page, 0), pages - 1)
    chunk = numbers[page * EPISODES_PER_PAGE:(page + 1) * EPISODES_PER_PAGE]
    key = InlineKeyboardMarkup()
    for i in range(0, len(chunk), EPISODES_PER_ROW):
        key.row(*[
            InlineKeyboardButton(text=f"• {n} •" if n == current else str(n), callback_data=f"se:{sid}:{n}")
            for n in chunk[i:i + EPISODES_PER_ROW]
        ])
    if pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="◀️", callback_data=f"sp:{sid}:{page - 1}"))
        nav.append(InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="noop"))
        if page < pages - 1:
            nav.append(InlineKeyboardButton(text="▶️", callback_data=f"sp:{sid}:{page + 1}"))
        key.row(*nav)
    key.add(InlineKeyboardButton(text="🏠 Bosh menyu", callback_data="home"))
    return key


def send_series(cid, code, page=0, count_view=True):
    """Serial kartochkasini yuboradi. Serial bo'lmasa False."""
    s = get_series(code)
    if s is None:
        return False
    if count_view:
        with _db_lock:
            cursor.execute("UPDATE series SET views = COALESCE(views, 0) + 1 WHERE id=?", (s[0],))
            conn.commit()
        s = get_series(code)
    count = len(episode_numbers(s[0]))
    caption, key = series_caption(s, count), episodes_key(s[0], page)
    if s[3]:
        try:
            bot.send_photo(cid, s[3], caption=caption, reply_markup=key)
            return True
        except Exception as e:
            print("serial poster xato:", e)
    bot.send_message(cid, caption, reply_markup=key)
    return True


def episode_nav_key(sid, number):
    numbers = episode_numbers(sid)
    key = InlineKeyboardMarkup()
    nav = []
    idx = numbers.index(number) if number in numbers else -1
    if idx > 0:
        nav.append(InlineKeyboardButton(text=f"⬅️ {numbers[idx - 1]}-qism", callback_data=f"se:{sid}:{numbers[idx - 1]}"))
    nav.append(InlineKeyboardButton(text="📋 Qismlar", callback_data=f"sl:{sid}:{idx // EPISODES_PER_PAGE if idx >= 0 else 0}"))
    if 0 <= idx < len(numbers) - 1:
        nav.append(InlineKeyboardButton(text=f"{numbers[idx + 1]}-qism ➡️", callback_data=f"se:{sid}:{numbers[idx + 1]}"))
    key.row(*nav)
    key.add(InlineKeyboardButton(text="🏠 Bosh menyu", callback_data="home"))
    return key


def send_episode(cid, sid, number):
    s = get_series(sid)
    ep = get_episode(sid, number) if s else None
    if ep is None:
        return False
    if not config.can_watch(cid):
        return True  # qism bor, lekin VIP talab qilinadi — taklif yuborildi
    total = len(episode_numbers(sid))
    caption = (
        f"📺 <b>{html.escape(s[1])}</b>\n"
        f"🎞 <b>{number}-qism</b> / {total} ta\n\n"
        f"🆔 Serial kodi: <code>{sid}</code>"
    )
    send = bot.send_document if ep[2] == "document" else bot.send_video
    send(cid, ep[1], caption=caption, reply_markup=episode_nav_key(sid, number), protect_content=True)
    return True


def series_list_key(page=0, back="home"):
    total = series_count()
    rows = all_series(page * ADMIN_LIST_PAGE, ADMIN_LIST_PAGE)
    key = InlineKeyboardMarkup()
    for sid, title, _d, _p, _v, count in rows:
        key.add(InlineKeyboardButton(text=f"📺 {title} • {count} qism"[:60], callback_data=f"sv:{sid}"))
    pages = max((total - 1) // ADMIN_LIST_PAGE + 1, 1)
    if pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="◀️", callback_data=f"sls:{page - 1}"))
        nav.append(InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="noop"))
        if page < pages - 1:
            nav.append(InlineKeyboardButton(text="▶️", callback_data=f"sls:{page + 1}"))
        key.row(*nav)
    key.add(InlineKeyboardButton(text="🔙 Ortga", callback_data=back))
    return key


# ---------------- Admin panel ----------------

def admin_series_text():
    total = series_count()
    if not total:
        return "📺 <b>Seriallar</b>\n\nHozircha serial yo'q. «➕ Yangi serial» orqali qo'shing."
    return f"📺 <b>Seriallar</b> — {total} ta\n\nBoshqarish uchun serialni tanlang."


def admin_series_key(page=0):
    key = InlineKeyboardMarkup()
    key.add(InlineKeyboardButton(text="➕ Yangi serial", callback_data="sr_new"))
    for sid, title, _d, _p, _v, count in all_series(page * ADMIN_LIST_PAGE, ADMIN_LIST_PAGE):
        key.add(InlineKeyboardButton(text=f"{sid} • {title} • {count} qism"[:60], callback_data=f"sr_m:{sid}"))
    pages = max((series_count() - 1) // ADMIN_LIST_PAGE + 1, 1)
    if pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="◀️", callback_data=f"sr_pg:{page - 1}"))
        nav.append(InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="noop"))
        if page < pages - 1:
            nav.append(InlineKeyboardButton(text="▶️", callback_data=f"sr_pg:{page + 1}"))
        key.row(*nav)
    key.add(InlineKeyboardButton(text="🔙 Admin panel", callback_data="back"))
    return key


def manage_text(sid):
    s = get_series(sid)
    numbers = episode_numbers(sid)
    if not numbers:
        eps = "yo'q"
    elif numbers == list(range(1, len(numbers) + 1)):
        eps = f"{len(numbers)} ta (1–{numbers[-1]})"
    else:
        eps = f"{len(numbers)} ta: " + ", ".join(map(str, numbers[:60])) + ("…" if len(numbers) > 60 else "")
    return (
        f"📺 <b>{html.escape(s[1])}</b>\n\n"
        f"🆔 Kod: <code>{sid}</code>\n"
        f"🎞 Qismlar: {eps}\n"
        f"🖼 Poster: {'bor' if s[3] else 'yo‘q'}\n"
        f"👁 Ko'rishlar: {int(s[4] or 0)}"
    )


def manage_key(sid):
    key = InlineKeyboardMarkup()
    key.row(
        InlineKeyboardButton(text="➕ Qism qo'shish", callback_data=f"sr_up:{sid}"),
        InlineKeyboardButton(text="🗑 Qism o'chirish", callback_data=f"sr_de:{sid}"),
    )
    key.row(
        InlineKeyboardButton(text="👁 Ko'rish", callback_data=f"sv:{sid}"),
        InlineKeyboardButton(text="❌ Serialni o'chirish", callback_data=f"sr_rm:{sid}"),
    )
    key.add(InlineKeyboardButton(text="🔙 Seriallar", callback_data="sr_admin"))
    return key


def upload_key(sid):
    return InlineKeyboardMarkup().add(InlineKeyboardButton(text="✅ Tugatish", callback_data=f"sr_done:{sid}"))


def cancel_key():
    return InlineKeyboardMarkup().add(InlineKeyboardButton(text="🔙 Bekor qilish", callback_data="sr_admin"))


def _upload_prompt(sid):
    numbers = episode_numbers(sid)
    nxt = (numbers[-1] + 1) if numbers else 1
    return (
        f"📤 <b>Qism yuklash rejimi</b> — {html.escape(get_series(sid)[1])}\n\n"
        f"Videolarni ketma-ket yuboring (bir nechtasini birdan ham bo'ladi).\n"
        f"Har biri navbatdagi qism bo'ladi — keyingisi: <b>{nxt}-qism</b>.\n\n"
        "Aniq raqam kerak bo'lsa, video izohiga yozing: <code>5</code> yoki <code>5-qism</code>.\n"
        "Tugatgach «✅ Tugatish»ni bosing."
    )


def new_series_code_step(msg):
    cid = msg.chat.id
    text = (msg.text or "").strip()
    if not text.isdigit():
        bot.send_message(cid, "❌ Kod faqat raqamlardan iborat bo'lishi kerak. Bekor qilindi.", reply_markup=admin_series_key())
        return
    if code_taken(int(text)):
        bot.send_message(cid, f"❌ <code>{text}</code> kodi band (kino yoki serial). Boshqa kod bilan qayta urinib ko'ring.",
                         reply_markup=admin_series_key())
        return
    DRAFTS[cid] = {"id": int(text)}
    a = bot.send_message(cid, "2/4. 📝 Serial <b>nomini</b> yuboring:", reply_markup=cancel_key())
    bot.register_next_step_handler(a, new_series_title_step)


def new_series_title_step(msg):
    cid = msg.chat.id
    title = (msg.text or "").strip()
    if cid not in DRAFTS or not title:
        DRAFTS.pop(cid, None)
        bot.send_message(cid, "❌ Bekor qilindi.", reply_markup=admin_series_key())
        return
    DRAFTS[cid]["title"] = title[:120]
    a = bot.send_message(
        cid,
        "3/4. 📄 Serial <b>tavsifini</b> yuboring (janr, yil, davlat, tili...).\nO'tkazib yuborish: /skip",
        reply_markup=cancel_key(),
    )
    bot.register_next_step_handler(a, new_series_desc_step)


def new_series_desc_step(msg):
    cid = msg.chat.id
    if cid not in DRAFTS or not msg.text:
        DRAFTS.pop(cid, None)
        bot.send_message(cid, "❌ Bekor qilindi.", reply_markup=admin_series_key())
        return
    DRAFTS[cid]["description"] = None if msg.text.strip() == "/skip" else msg.text.strip()[:900]
    a = bot.send_message(cid, "4/4. 🖼 Serial <b>posterini</b> (rasm) yuboring.\nO'tkazib yuborish: /skip",
                         reply_markup=cancel_key())
    bot.register_next_step_handler(a, new_series_poster_step)


def new_series_poster_step(msg):
    cid = msg.chat.id
    draft = DRAFTS.pop(cid, None)
    if draft is None:
        bot.send_message(cid, "❌ Bekor qilindi.", reply_markup=admin_series_key())
        return
    if msg.photo:
        draft["poster"] = msg.photo[-1].file_id
    elif (msg.text or "").strip() == "/skip":
        draft["poster"] = None
    else:
        bot.send_message(cid, "❌ Rasm yoki /skip kutilgan edi. Bekor qilindi.", reply_markup=admin_series_key())
        return
    if code_taken(draft["id"]):
        bot.send_message(cid, "❌ Bu kod hozirgina band qilindi. Bekor qilindi.", reply_markup=admin_series_key())
        return
    with _db_lock:
        cursor.execute(
            "INSERT INTO series(id, title, description, poster_file_id, views, created_at) VALUES(?, ?, ?, ?, 0, ?)",
            (draft["id"], draft["title"], draft["description"], draft["poster"], int(time.time())),
        )
        conn.commit()
    UPLOAD_MODE[cid] = draft["id"]
    bot.send_message(cid, f"✅ <b>Serial yaratildi!</b> Kod: <code>{draft['id']}</code>\n\n" + _upload_prompt(draft["id"]),
                     reply_markup=upload_key(draft["id"]))


def delete_episode_step(msg, sid):
    cid = msg.chat.id
    numbers = [int(n) for n in re.findall(r"\d+", msg.text or "")]
    if not numbers or get_series(sid) is None:
        bot.send_message(cid, "❌ Bekor qilindi.", reply_markup=manage_key(sid) if get_series(sid) else admin_series_key())
        return
    removed = [n for n in dict.fromkeys(numbers) if delete_episode(sid, n)]
    missing = [n for n in dict.fromkeys(numbers) if n not in removed]
    text = ""
    if removed:
        text += f"🗑 O'chirildi: {', '.join(map(str, removed))}-qism\n"
    if missing:
        text += f"❌ Topilmadi: {', '.join(map(str, missing))}\n"
    bot.send_message(cid, f"<b>{text}</b>\n" + manage_text(sid), reply_markup=manage_key(sid))


_EP_NUMBER = re.compile(r"^\s*(\d{1,4})\s*(?:-?\s*qism)?\b", re.IGNORECASE)


@bot.message_handler(
    content_types=["video", "document"],
    func=lambda m: m.chat.id in UPLOAD_MODE and get_admin(m.chat.id),
)
def receive_episode(msg):
    cid = msg.chat.id
    sid = UPLOAD_MODE.get(cid)
    if sid is None or get_series(sid) is None:
        UPLOAD_MODE.pop(cid, None)
        return
    if msg.video:
        file_id, kind = msg.video.file_id, "video"
    elif msg.document and (msg.document.mime_type or "").startswith("video"):
        file_id, kind = msg.document.file_id, "document"
    else:
        bot.reply_to(msg, "❌ Faqat video fayl yuboring.")
        return
    m = _EP_NUMBER.match(msg.caption or "")
    number, is_new = add_episode(sid, file_id, kind, int(m.group(1)) if m else None)
    note = "qo'shildi" if is_new else "yangilandi (eski fayl almashtirildi)"
    bot.reply_to(msg, f"✅ <b>{number}-qism</b> {note}.", reply_markup=upload_key(sid))


# ---------------- Callbacklar ----------------

ADMIN_PREFIXES = ("sr_",)
USER_PREFIXES = ("se:", "sp:", "sl:", "sv:", "sls:")


def _is_series_callback(call):
    data = call.data or ""
    return data.startswith(ADMIN_PREFIXES + USER_PREFIXES) or data in ("menu_series", "noop")


@bot.callback_query_handler(func=_is_series_callback)
def series_callback(call):
    try:
        _handle(call)
    except Exception as e:
        print("serial callback xato:", call.data, e)
    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass


def _edit(call, text, key):
    """Matnli yoki rasmli xabarni tahrirlaydi."""
    msg = call.message
    if msg.content_type in ("photo", "video", "document"):
        bot.edit_message_caption(text, msg.chat.id, msg.id, reply_markup=key)
    else:
        bot.edit_message_text(text, msg.chat.id, msg.id, reply_markup=key)


def _handle(call):
    data = call.data
    cid = call.message.chat.id if call.message else call.from_user.id

    if data == "noop":
        return

    # --- Admin ---
    if data.startswith(ADMIN_PREFIXES):
        if not get_admin(cid):
            return
        bot.clear_step_handler_by_chat_id(cid)
        if data == "sr_admin" or data.startswith("sr_pg:"):
            UPLOAD_MODE.pop(cid, None)
            DRAFTS.pop(cid, None)
            page = int(data.split(":")[1]) if ":" in data else 0
            _edit(call, admin_series_text(), admin_series_key(page))
        elif data == "sr_new":
            a = bot.send_message(cid, "📺 <b>Yangi serial</b>\n\n1/4. 🆔 Serial <b>kodini</b> yuboring (raqam, masalan <code>5001</code>):",
                                 reply_markup=cancel_key())
            bot.register_next_step_handler(a, new_series_code_step)
        elif data.startswith("sr_m:"):
            sid = int(data.split(":")[1])
            if get_series(sid):
                _edit(call, manage_text(sid), manage_key(sid))
        elif data.startswith("sr_up:"):
            sid = int(data.split(":")[1])
            if get_series(sid):
                UPLOAD_MODE[cid] = sid
                bot.send_message(cid, _upload_prompt(sid), reply_markup=upload_key(sid))
        elif data.startswith("sr_done:"):
            sid = int(data.split(":")[1])
            UPLOAD_MODE.pop(cid, None)
            if get_series(sid):
                bot.send_message(cid, "✅ <b>Yuklash tugadi.</b>\n\n" + manage_text(sid), reply_markup=manage_key(sid))
        elif data.startswith("sr_de:"):
            sid = int(data.split(":")[1])
            if get_series(sid):
                a = bot.send_message(cid, "🗑 O'chiriladigan qism raqamini yuboring (bir nechtasi: <code>3 4 5</code>):",
                                     reply_markup=InlineKeyboardMarkup().add(
                                         InlineKeyboardButton(text="🔙 Bekor qilish", callback_data=f"sr_m:{sid}")))
                bot.register_next_step_handler(a, delete_episode_step, sid)
        elif data.startswith("sr_rm:"):
            sid = int(data.split(":")[1])
            s = get_series(sid)
            if s:
                key = InlineKeyboardMarkup().row(
                    InlineKeyboardButton(text="✅ Ha, o'chirish", callback_data=f"sr_rmok:{sid}"),
                    InlineKeyboardButton(text="🔙 Yo'q", callback_data=f"sr_m:{sid}"),
                )
                _edit(call, f"⚠️ <b>{html.escape(s[1])}</b> serialini barcha qismlari bilan o'chirasizmi?", key)
        elif data.startswith("sr_rmok:"):
            sid = int(data.split(":")[1])
            delete_series(sid)
            UPLOAD_MODE.pop(cid, None)
            bot.answer_callback_query(call.id, "❌ Serial o'chirildi")
            _edit(call, admin_series_text(), admin_series_key())
        return

    # --- Foydalanuvchi ---
    if data == "menu_series" or data.startswith("sls:"):
        page = int(data.split(":")[1]) if data.startswith("sls:") else 0
        if not series_count():
            bot.answer_callback_query(call.id, "📺 Seriallar hali qo'shilmagan.", show_alert=True)
            return
        _edit(call, "📺 <b>Seriallar</b>\n\nKerakli serialni tanlang:", series_list_key(page, back="sq_back"))
        return

    if not join(cid, "check"):
        return
    if data.startswith("sv:"):
        if not send_series(cid, int(data.split(":")[1])):
            bot.answer_callback_query(call.id, "Serial topilmadi", show_alert=True)
    elif data.startswith(("sp:", "sl:")):
        _, sid, page = data.split(":")
        sid, page = int(sid), int(page)
        if get_series(sid) is None:
            bot.answer_callback_query(call.id, "Serial topilmadi", show_alert=True)
        elif data.startswith("sp:") and call.message:
            bot.edit_message_reply_markup(cid, call.message.id, reply_markup=episodes_key(sid, page))
        else:
            send_series(cid, sid, page=page, count_view=False)
    elif data.startswith("se:"):
        _, sid, number = data.split(":")
        if not send_episode(cid, int(sid), int(number)):
            bot.answer_callback_query(call.id, "Bu qism topilmadi", show_alert=True)
