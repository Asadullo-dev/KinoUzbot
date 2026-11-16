"""Reels: admin qisqa video (treyler) yuklaydi, bot uni kanalga «🎬 Kinoni ko'rish» tugmasi bilan joylaydi.

Tugma https://t.me/<bot>?start=<kod> havolasi: Telegram userni botga olib kiradi va START bosilganda
"/start <kod>" yuboriladi — mavjud /start oqimi kinoni (obuna va VIP tekshiruvi bilan) chiqaradi.

Jadval: reels(id, code, title, kind, video_file_id, video_kind, channel, message_id, created_by, created_at)
Sozlamalar (settings jadvali): reels_channel, reels_instagram
"""
import html
import re
import time

from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup

import config
import series
from config import bot, bot_username, cursor, get_admin, get_film, parse_film
from vip import get_setting, set_setting

cursor.execute(
    "CREATE TABLE IF NOT EXISTS reels("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, code INTEGER NOT NULL, title TEXT NOT NULL, kind TEXT NOT NULL, "
    "video_file_id TEXT NOT NULL, video_kind TEXT NOT NULL DEFAULT 'video', channel TEXT NOT NULL, "
    "message_id INTEGER, created_by INTEGER, created_at INTEGER NOT NULL)"
)

DEFAULT_CHANNEL = config.CHANNEL  # .env dagi CHANNEL; admin paneldan o'zgartirish mumkin
LIST_LIMIT = 10
DRAFTS = {}  # {admin_chat_id: {...}}


# ---------------- Sozlamalar ----------------

def channel():
    return get_setting("reels_channel") or DEFAULT_CHANNEL


def instagram():
    return get_setting("reels_instagram") or ""


def channel_link(ch=None):
    ch = (ch or channel()).strip()
    return f"https://t.me/{ch.lstrip('@')}" if ch.startswith("@") else None


def post_link(ch, message_id):
    if ch.startswith("@"):
        return f"https://t.me/{ch.lstrip('@')}/{message_id}"
    if ch.startswith("-100"):  # yopiq kanal
        return f"https://t.me/c/{ch[4:]}/{message_id}"
    return None


# ---------------- Kontent ----------------

def find_content(code):
    """Kod bo'yicha kino yoki serialni topadi: (kind, title) yoki None."""
    row = get_film(code)
    if row is not None:
        return "film", parse_film(row)["title"]
    s = series.get_series(code)
    if s is not None:
        return "series", s[1]
    return None


def watch_url(code):
    return f"https://t.me/{bot_username()}?start={code}"


def reels_caption(d):
    is_series = d["kind"] == "series"
    bot_name = bot_username()
    ch = channel()
    links = []
    if instagram():
        links.append(f'📸 <a href="{html.escape(instagram())}">Instagram</a>')
    if channel_link(ch):
        links.append(f'✈️ <a href="{channel_link(ch)}">Telegram</a>')
    links.append(f'🤖 <a href="{watch_url(d["code"])}">BOT</a>')
    return (
        f"🎬 {'Serial' if is_series else 'Kino'} nomi: <b>{html.escape(d['title'])}</b>\n\n"
        f"🎬 {'Serial' if is_series else 'Kino'} kodi: <b>{d['code']}</b> ✅\n\n"
        f"🤖 Bot: @{bot_name} ✅\n\n"
        "🔥 Eng sara filmlar faqat bizda !\n\n"
        f"💞 Bizning kanal: {html.escape(ch)}\n\n"
        + " | ".join(links)
    )


def reels_key(d):
    text = "📺 Serialni ko'rish" if d["kind"] == "series" else "🎬 Kinoni ko'rish"
    return InlineKeyboardMarkup().add(InlineKeyboardButton(text=text, url=watch_url(d["code"])))


def _send_reel(chat_id, d, reply_markup):
    send = bot.send_document if d["video_kind"] == "document" else bot.send_video
    return send(chat_id, d["video_file_id"], caption=reels_caption(d), reply_markup=reply_markup)


# ---------------- Admin ekranlari ----------------

def admin_text():
    total = cursor.execute("SELECT COUNT(*) FROM reels WHERE message_id IS NOT NULL").fetchone()[0]
    insta = instagram() or "kiritilmagan"
    return (
        "🎞 <b>Reels</b>\n\n"
        "Qisqa video (treyler) yuklang — bot uni kanalga «🎬 Kinoni ko'rish» tugmasi bilan joylaydi. "
        "Tugma bosilganda user botga o'tadi va kino darhol chiqadi.\n\n"
        f"📢 Kanal: <b>{html.escape(channel())}</b>\n"
        f"📸 Instagram: {html.escape(insta)}\n"
        f"📊 Joylangan reelslar: <b>{total}</b>\n\n"
        "⚠️ Bot kanalda <b>admin</b> bo'lishi va «Xabar joylash» huquqiga ega bo'lishi kerak."
    )


def admin_key():
    key = InlineKeyboardMarkup()
    key.add(InlineKeyboardButton(text="➕ Yangi reels", callback_data="rl_new"))
    key.add(InlineKeyboardButton(text="📋 Joylangan reelslar", callback_data="rl_list"))
    key.row(
        InlineKeyboardButton(text="📢 Kanal", callback_data="rl_ch"),
        InlineKeyboardButton(text="📸 Instagram", callback_data="rl_ig"),
    )
    key.add(InlineKeyboardButton(text="🔙 Admin panel", callback_data="back"))
    return key


def back_key(cb="rl_menu", text="🔙 Ortga"):
    return InlineKeyboardMarkup().add(InlineKeyboardButton(text=text, callback_data=cb))


def preview_key():
    return (
        InlineKeyboardMarkup()
        .add(InlineKeyboardButton(text="✅ Kanalga joylash", callback_data="rl_pub"))
        .row(
            InlineKeyboardButton(text="✏️ Nomini o'zgartirish", callback_data="rl_title"),
            InlineKeyboardButton(text="❌ Bekor qilish", callback_data="rl_cancel"),
        )
    )


def list_text():
    rows = cursor.execute(
        "SELECT id, code, title, channel, message_id, created_at FROM reels WHERE message_id IS NOT NULL "
        "ORDER BY id DESC LIMIT ?", (LIST_LIMIT,)
    ).fetchall()
    if not rows:
        return "📋 <b>Joylangan reelslar</b>\n\nHali reels joylanmagan."
    lines = ["📋 <b>Oxirgi joylangan reelslar</b>\n"]
    for rid, code, title, ch, mid, created in rows:
        link = post_link(ch, mid)
        name = f'<a href="{link}">{html.escape(title)}</a>' if link else html.escape(title)
        lines.append(f"#{rid} • {name} — kod <code>{code}</code> • {time.strftime('%d.%m.%Y', time.localtime(created))}")
    lines.append("\n🗑 — kanaldan o'chirish")
    return "\n".join(lines)


def list_key():
    key = InlineKeyboardMarkup()
    for rid, title in cursor.execute(
        "SELECT id, title FROM reels WHERE message_id IS NOT NULL ORDER BY id DESC LIMIT ?", (LIST_LIMIT,)
    ).fetchall():
        key.add(InlineKeyboardButton(text=f"🗑 #{rid} • {title}"[:60], callback_data=f"rl_del:{rid}"))
    key.add(InlineKeyboardButton(text="🔙 Ortga", callback_data="rl_menu"))
    return key


# ---------------- Admin qadamlari ----------------

def _video_from(msg):
    if msg.video:
        return msg.video.file_id, "video"
    if msg.document and (msg.document.mime_type or "").startswith("video"):
        return msg.document.file_id, "document"
    if msg.animation:
        return msg.animation.file_id, "document"
    return None, None


def video_step(msg):
    cid = msg.chat.id
    file_id, kind = _video_from(msg)
    if not file_id:
        bot.send_message(cid, "❌ Video kutilgan edi. Bekor qilindi.", reply_markup=admin_key())
        return
    DRAFTS[cid] = {"video_file_id": file_id, "video_kind": kind}
    a = bot.send_message(cid, "2/2. 🆔 Bu reels qaysi <b>kino yoki serial</b> uchun? Kodini yuboring (masalan <code>960</code>):",
                         reply_markup=back_key("rl_cancel", "❌ Bekor qilish"))
    bot.register_next_step_handler(a, code_step)


def code_step(msg):
    cid = msg.chat.id
    draft = DRAFTS.get(cid)
    text = (msg.text or "").strip()
    if draft is None:
        bot.send_message(cid, "❌ Bekor qilindi.", reply_markup=admin_key())
        return
    if not text.isdigit():
        a = bot.send_message(cid, "❌ Kod faqat raqam bo'ladi. Qaytadan yuboring:", reply_markup=back_key("rl_cancel", "❌ Bekor qilish"))
        bot.register_next_step_handler(a, code_step)
        return
    found = find_content(int(text))
    if found is None:
        a = bot.send_message(cid, f"❌ <code>{text}</code> kodli kino yoki serial topilmadi. Boshqa kod yuboring:",
                             reply_markup=back_key("rl_cancel", "❌ Bekor qilish"))
        bot.register_next_step_handler(a, code_step)
        return
    draft.update(code=int(text), kind=found[0], title=found[1])
    send_preview(cid)


def send_preview(cid):
    draft = DRAFTS[cid]
    bot.send_message(cid, "👀 <b>Ko'rinishi</b> — kanalda shunday chiqadi:")
    _send_reel(cid, draft, reels_key(draft))
    bot.send_message(cid, f"📢 Kanal: <b>{html.escape(channel())}</b>\nJoylaymizmi?", reply_markup=preview_key())


def title_step(msg):
    cid = msg.chat.id
    title = (msg.text or "").strip()
    if cid not in DRAFTS or not title:
        bot.send_message(cid, "❌ Bekor qilindi.", reply_markup=admin_key())
        DRAFTS.pop(cid, None)
        return
    DRAFTS[cid]["title"] = title[:120]
    send_preview(cid)


def channel_step(msg):
    text = (msg.text or "").strip()
    m = re.fullmatch(r"(?:https?://)?(?:t\.me/)?@?([A-Za-z][A-Za-z0-9_]{4,31})", text)
    if re.fullmatch(r"-100\d{5,}", text):
        value = text
    elif m:
        value = "@" + m.group(1)
    else:
        bot.reply_to(msg, "❌ Kanal noto'g'ri. Masalan: <code>@kanal_username</code> yoki <code>-1001234567890</code>",
                     reply_markup=back_key())
        return
    try:
        member = bot.get_chat_member(value, bot.get_me().id)
        can_post = member.status == "creator" or (member.status == "administrator" and getattr(member, "can_post_messages", True))
        warn = "" if can_post else "\n\n⚠️ Bot bu kanalda admin emas yoki xabar joylash huquqi yo'q — avval admin qiling."
    except Exception as e:
        warn = f"\n\n⚠️ Kanalni tekshirib bo'lmadi: {html.escape(str(e)[:120])}\nBotni kanalga admin qiling."
    set_setting("reels_channel", value)
    bot.reply_to(msg, f"✅ Kanal saqlandi: <b>{html.escape(value)}</b>{warn}", reply_markup=back_key())


def instagram_step(msg):
    text = (msg.text or "").strip()
    if text == "/skip":
        set_setting("reels_instagram", "")
        bot.reply_to(msg, "✅ Instagram havolasi o'chirildi.", reply_markup=back_key())
        return
    if not re.fullmatch(r"https?://(www\.)?instagram\.com/\S+", text):
        bot.reply_to(msg, "❌ Havola noto'g'ri. Masalan: <code>https://instagram.com/sahifangiz</code>", reply_markup=back_key())
        return
    set_setting("reels_instagram", text)
    bot.reply_to(msg, "✅ Instagram havolasi saqlandi.", reply_markup=back_key())


def publish(cid):
    """Qoralamani kanalga joylaydi. (True, havola) yoki (False, xato matni)."""
    draft = DRAFTS.get(cid)
    if draft is None:
        return False, "Qoralama topilmadi — qaytadan boshlang."
    if find_content(draft["code"]) is None:
        return False, "Kino/serial bazadan o'chirilgan."
    ch = channel()
    if not ch:
        return False, "Kanal kiritilmagan: .env da CHANNEL ni yozing yoki «📢 Kanal» tugmasi orqali kiriting."
    try:
        msg = _send_reel(ch, draft, reels_key(draft))
    except Exception as e:
        return False, (f"Kanalga joylab bo'lmadi: {html.escape(str(e)[:200])}\n\n"
                       "Botni kanalga admin qilib, «Xabar joylash» huquqini bering.")
    cursor.execute(
        "INSERT INTO reels(code, title, kind, video_file_id, video_kind, channel, message_id, created_by, created_at) "
        "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (draft["code"], draft["title"], draft["kind"], draft["video_file_id"], draft["video_kind"], ch,
         msg.message_id, cid, int(time.time())),
    )
    DRAFTS.pop(cid, None)
    return True, post_link(ch, msg.message_id)


# ---------------- Callbacklar ----------------

@bot.callback_query_handler(func=lambda call: (call.data or "").startswith("rl_"))
def reels_callback(call):
    try:
        _handle(call)
    except Exception as e:
        print("reels callback xato:", call.data, e)
    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass


def _edit(call, text, key):
    try:
        bot.edit_message_text(text, call.message.chat.id, call.message.id, reply_markup=key, disable_web_page_preview=True)
    except Exception:
        bot.send_message(call.message.chat.id, text, reply_markup=key, disable_web_page_preview=True)


def _handle(call):
    d = call.data
    cid = call.message.chat.id if call.message else call.from_user.id
    if not get_admin(cid):
        return
    bot.clear_step_handler_by_chat_id(cid)

    if d == "rl_menu":
        _edit(call, admin_text(), admin_key())
    elif d == "rl_new":
        DRAFTS.pop(cid, None)
        a = bot.send_message(cid, "🎞 <b>Yangi reels</b>\n\n1/2. 📹 Reels videoni yuboring (qisqa treyler):",
                             reply_markup=back_key("rl_cancel", "❌ Bekor qilish"))
        bot.register_next_step_handler(a, video_step)
    elif d == "rl_title":
        if cid in DRAFTS:
            a = bot.send_message(cid, "✏️ Yangi nomni yuboring:", reply_markup=back_key("rl_cancel", "❌ Bekor qilish"))
            bot.register_next_step_handler(a, title_step)
    elif d == "rl_pub":
        ok, info = publish(cid)
        if ok:
            text = "✅ <b>Reels kanalga joylandi!</b>" + (f'\n\n🔗 <a href="{info}">Postni ochish</a>' if info else "")
            bot.answer_callback_query(call.id, "✅ Kanalga joylandi")
            _edit(call, text, back_key("rl_menu", "🎞 Reels bo'limi"))
        else:
            bot.answer_callback_query(call.id, "❌ Joylanmadi", show_alert=True)
            bot.send_message(cid, f"❌ {info}", reply_markup=preview_key() if cid in DRAFTS else admin_key())
    elif d == "rl_cancel":
        DRAFTS.pop(cid, None)
        _edit(call, "❌ Bekor qilindi.\n\n" + admin_text(), admin_key())
    elif d == "rl_list":
        _edit(call, list_text(), list_key())
    elif d.startswith("rl_del:"):
        rid = int(d.split(":")[1])
        row = cursor.execute("SELECT channel, message_id FROM reels WHERE id=?", (rid,)).fetchone()
        if row:
            note = "🗑 Kanaldan o'chirildi"
            try:
                bot.delete_message(row[0], row[1])
            except Exception as e:
                # 48 soatdan eski post yoki allaqachon o'chirilgan — ro'yxatdan baribir olib tashlaymiz
                note = "Ro'yxatdan olib tashlandi (kanaldan o'chirib bo'lmadi — qo'lda o'chiring)"
                print("reels o'chirish xato:", e)
            cursor.execute("DELETE FROM reels WHERE id=?", (rid,))
            bot.answer_callback_query(call.id, note, show_alert=True)
        _edit(call, list_text(), list_key())
    elif d == "rl_ch":
        a = bot.send_message(cid, f"📢 Reels joylanadigan kanalni yuboring (hozir: <b>{html.escape(channel())}</b>)\n"
                                  "Masalan: <code>@kanal_username</code>", reply_markup=back_key())
        bot.register_next_step_handler(a, channel_step)
    elif d == "rl_ig":
        a = bot.send_message(cid, "📸 Instagram sahifangiz havolasini yuboring\n"
                                  "Masalan: <code>https://instagram.com/sahifangiz</code>\nO'chirish: /skip",
                             reply_markup=back_key())
        bot.register_next_step_handler(a, instagram_step)
