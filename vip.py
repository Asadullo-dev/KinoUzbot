"""VIP obuna: karta orqali to'lov, chekni admin tasdiqlashi, muddat va eslatmalar.

Oqim:
  User: 💎 VIP → tarif tanlaydi → karta ma'lumotlari → 🧾 chek yuklaydi
  Admin: chek ovozli bildirishnoma bilan keladi → ✅ VIP qilish / ❌ Rad etish
  Tizim: obuna tugashiga 5 kun qolganda va tugaganda userga eslatma yuboradi.

VIP rejimi (admin panelda yoqiladi) yoqilganda kino va serial qismlarini faqat VIP userlar ko'radi.
Jadvallar mavjud `video` jadvaliga tegmaydi.
"""
import calendar
import html
import json
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone

from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup

import config
from config import ADMIN_ID, DB_PATH, bot, get_admin

TZ = timezone(timedelta(hours=5))  # Toshkent vaqti
REMIND_BEFORE = 5 * 24 * 3600      # tugashidan 5 kun oldin eslatish
REMINDER_INTERVAL = 15 * 60        # eslatmalar tekshiruvi oralig'i (soniya)
RECEIPT_TIMEOUT = 30 * 60          # chek kutish holati shuncha vaqt saqlanadi

DEFAULT_PLANS = [(1, 23000), (3, 39000), (6, 45000), (12, 49000)]

# Chek kutayotgan userlar: {chat_id: (plan_id, vaqt)}
RECEIPT_WAIT = {}


# ---------------- Baza ----------------

def _db():
    conn = sqlite3.connect(DB_PATH, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn


def q(sql, params=()):
    conn = _db()
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def q1(sql, params=()):
    rows = q(sql, params)
    return rows[0] if rows else None


def ex(sql, params=()):
    conn = _db()
    try:
        cur = conn.execute(sql, params)
        return cur.lastrowid, cur.rowcount
    finally:
        conn.close()


def init_db():
    conn = _db()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS vip_plans(
                id INTEGER PRIMARY KEY, months INTEGER NOT NULL UNIQUE, price INTEGER NOT NULL,
                active INTEGER NOT NULL DEFAULT 1);
            CREATE TABLE IF NOT EXISTS vip_cards(
                id INTEGER PRIMARY KEY, number TEXT NOT NULL, holder TEXT NOT NULL, bank TEXT,
                active INTEGER NOT NULL DEFAULT 1);
            CREATE TABLE IF NOT EXISTS vip_subscriptions(
                chat_id INTEGER PRIMARY KEY, months INTEGER, starts_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL, granted_by INTEGER,
                notified_soon INTEGER NOT NULL DEFAULT 0, notified_end INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS vip_payments(
                id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, user_name TEXT,
                months INTEGER NOT NULL, price INTEGER NOT NULL, receipt_file_id TEXT NOT NULL,
                receipt_kind TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
                reject_reason TEXT, created_at INTEGER NOT NULL, reviewed_by INTEGER,
                reviewed_at INTEGER, admin_msgs TEXT);
            CREATE INDEX IF NOT EXISTS idx_vip_payments_status ON vip_payments(status);
        """)
        if conn.execute("SELECT COUNT(*) FROM vip_plans").fetchone()[0] == 0:
            conn.executemany("INSERT INTO vip_plans(months, price) VALUES(?, ?)", DEFAULT_PLANS)
    finally:
        conn.close()


init_db()


def _default_contact():
    # Murojaat username'i kiritilmagan bo'lsa — bosh adminning username'i
    if get_setting("admin_contact") is None:
        try:
            username = bot.get_chat(ADMIN_ID).username
        except Exception:
            username = None
        set_setting("admin_contact", f"@{username}" if username else "")


def get_setting(key, default=None):
    row = q1("SELECT value FROM settings WHERE key=?", (key,))
    return row["value"] if row else default


def set_setting(key, value):
    ex("INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
       (key, str(value)))


# ---------------- Yordamchilar ----------------

def money(n):
    return f"{int(n):,}".replace(",", " ") + " so'm"


def plan_name(months):
    return "1 yil" if months == 12 else f"{months} oy"


def fmt_date(ts):
    return datetime.fromtimestamp(ts, TZ).strftime("%d.%m.%Y")


def fmt_dt(ts):
    return datetime.fromtimestamp(ts, TZ).strftime("%d.%m.%Y %H:%M")


def fmt_left(seconds):
    seconds = max(int(seconds), 0)
    days, rest = divmod(seconds, 86400)
    hours = rest // 3600
    if days:
        return f"{days} kun {hours} soat"
    return f"{hours} soat {rest % 3600 // 60} daqiqa"


def add_months(ts, months):
    d = datetime.fromtimestamp(ts, TZ)
    month = d.month - 1 + months
    year, month = d.year + month // 12, month % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return int(d.replace(year=year, month=month, day=day).timestamp())


def admin_ids():
    ids = {ADMIN_ID}
    ids.update(r["chat_id"] for r in q("SELECT chat_id FROM admins"))
    return sorted(ids)


def vip_mode_on():
    return get_setting("vip_mode", "0") == "1"


def subscription(cid):
    return q1("SELECT * FROM vip_subscriptions WHERE chat_id=?", (cid,))


def is_vip(cid, now=None):
    sub = subscription(cid)
    return bool(sub and sub["expires_at"] > (now or time.time()))


def contact_url():
    contact = (get_setting("admin_contact") or "").lstrip("@").strip()
    return f"https://t.me/{contact}" if contact else None


def contact_button():
    url = contact_url()
    return InlineKeyboardButton(text="👨‍💻 Adminga murojaat", url=url) if url else None


def grant(cid, months, by=None, now=None):
    """VIP beradi yoki uzaytiradi. Faol obuna bo'lsa — uning oxiridan boshlab qo'shiladi."""
    now = int(now or time.time())
    sub = subscription(cid)
    if sub and sub["expires_at"] > now:
        starts, base = sub["starts_at"], sub["expires_at"]
    else:
        starts, base = now, now
    expires = add_months(base, months)
    ex(
        "INSERT INTO vip_subscriptions(chat_id, months, starts_at, expires_at, granted_by, notified_soon, notified_end) "
        "VALUES(?, ?, ?, ?, ?, 0, 0) ON CONFLICT(chat_id) DO UPDATE SET months=excluded.months, "
        "starts_at=excluded.starts_at, expires_at=excluded.expires_at, granted_by=excluded.granted_by, "
        "notified_soon=0, notified_end=0",
        (cid, months, starts, expires, by),
    )
    return base, expires


def revoke(cid):
    return ex("DELETE FROM vip_subscriptions WHERE chat_id=?", (cid,))[1] > 0


# ---------------- Kirish nazorati ----------------

def gate_text():
    return "💎 <b>«VIP» sotib olish orqali filmlarni ko'rish uchun ruxsat olasiz!</b>"


def gate_key():
    return InlineKeyboardMarkup().add(InlineKeyboardButton(text="🛍 VIP SOTIB OLISH", callback_data="vip_buy"))


def can_watch(cid):
    """Kino/serial qismini ko'rish mumkinmi. Mumkin bo'lmasa userga VIP taklifini yuboradi."""
    if not vip_mode_on() or get_admin(cid) or is_vip(cid):
        return True
    bot.send_message(cid, gate_text(), reply_markup=gate_key())
    return False


config.can_watch = can_watch  # send_film va seriallar shu hook orqali tekshiradi
config.is_vip_user = is_vip  # VIP obunachilar kanallarga obuna bo'lishi shart emas


# ---------------- User ekranlari ----------------

def status_text(cid):
    sub = subscription(cid)
    now = time.time()
    if sub and sub["expires_at"] > now:
        return (
            "💎 <b>VIP obunangiz faol!</b>\n\n"
            f"📦 Tarif: <b>{plan_name(sub['months'] or 1)}</b>\n"
            f"📅 Boshlangan: {fmt_date(sub['starts_at'])}\n"
            f"⏳ Amal qiladi: <b>{fmt_date(sub['expires_at'])}</b> gacha\n"
            f"🕒 Qoldi: <b>{fmt_left(sub['expires_at'] - now)}</b>"
        )
    expired = f"\n\n⌛️ Oldingi obunangiz {fmt_date(sub['expires_at'])} da tugagan." if sub else ""
    return gate_text() + expired


def status_key(cid):
    key = InlineKeyboardMarkup()
    if is_vip(cid):
        key.add(InlineKeyboardButton(text="🔄 Obunani uzaytirish", callback_data="vip_buy"))
    else:
        key.add(InlineKeyboardButton(text="🛍 VIP SOTIB OLISH", callback_data="vip_buy"))
    if contact_button():
        key.add(contact_button())
    key.add(InlineKeyboardButton(text="🏠 Bosh menyu", callback_data="home"))
    return key


def plans_key():
    key = InlineKeyboardMarkup()
    for p in q("SELECT * FROM vip_plans WHERE active=1 ORDER BY months"):
        key.add(InlineKeyboardButton(text=f"💎 {plan_name(p['months'])} - {money(p['price'])}", callback_data=f"vip_plan:{p['id']}"))
    key.add(InlineKeyboardButton(text="⬅️ Orqaga", callback_data="vip"))
    return key


def payment_text(plan):
    cards = q("SELECT * FROM vip_cards WHERE active=1 ORDER BY id")
    lines = [
        "💳 <b>To'lov ma'lumotlari</b>\n",
        f"💎 Tarif: <b>{plan_name(plan['months'])}</b>",
        f"💰 Summa: <b>{money(plan['price'])}</b>\n",
    ]
    if cards:
        lines.append("<b>Quyidagi kartalardan biriga o'tkazing:</b>")
        for c in cards:
            bank = f" ({html.escape(c['bank'])})" if c["bank"] else ""
            lines.append(f"\n💳 <code>{html.escape(c['number'])}</code>{bank}\n👤 {html.escape(c['holder'])}")
        lines.append(
            "\n📌 <b>Qanday to'lanadi:</b>\n"
            "1️⃣ Ko'rsatilgan summani kartaga o'tkazing\n"
            "2️⃣ «🧾 To'lov chekini yuklash» tugmasini bosing\n"
            "3️⃣ Chek rasmini (skrinshot) yoki PDF faylini yuboring\n\n"
            "✅ Admin chekni tekshirib, VIP ni faollashtiradi va sizga xabar keladi."
        )
    else:
        lines.append("⚠️ To'lov kartalari hali kiritilmagan. Iltimos, admin bilan bog'laning.")
    return "\n".join(lines)


def payment_key(plan_id, has_cards):
    key = InlineKeyboardMarkup()
    if has_cards:
        key.add(InlineKeyboardButton(text="🧾 To'lov chekini yuklash", callback_data=f"vip_receipt:{plan_id}"))
    if contact_button():
        key.add(contact_button())
    key.add(InlineKeyboardButton(text="⬅️ Orqaga", callback_data="vip_buy"))
    return key


def show_vip(cid):
    bot.send_message(cid, status_text(cid), reply_markup=status_key(cid))


# ---------------- Chek qabul qilish ----------------

def _waiting_plan(cid):
    item = RECEIPT_WAIT.get(cid)
    if not item:
        return None
    plan_id, since = item
    if time.time() - since > RECEIPT_TIMEOUT:
        RECEIPT_WAIT.pop(cid, None)
        return None
    return plan_id


def _receipt_from(msg):
    if msg.photo:
        return msg.photo[-1].file_id, "photo"
    if msg.document:
        mime = msg.document.mime_type or ""
        if mime.startswith("image/") or mime == "application/pdf":
            return msg.document.file_id, "document"
    return None, None


@bot.message_handler(
    content_types=["photo", "document", "text"],
    func=lambda m: _waiting_plan(m.chat.id) is not None and not (m.text or "").startswith("/"),
)
def receive_receipt(msg):
    cid = msg.chat.id
    plan = q1("SELECT * FROM vip_plans WHERE id=?", (_waiting_plan(cid),))
    file_id, kind = _receipt_from(msg)
    if plan is None:
        RECEIPT_WAIT.pop(cid, None)
        return
    if not file_id:
        bot.reply_to(
            msg, "🧾 Iltimos, to'lov chekini <b>rasm</b> yoki <b>PDF fayl</b> ko'rinishida yuboring.",
            reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton(text="❌ Bekor qilish", callback_data="vip_cancel")),
        )
        return
    RECEIPT_WAIT.pop(cid, None)
    user = msg.from_user
    name = " ".join(filter(None, [user.first_name, user.last_name])) or str(cid)
    pid, _ = ex(
        "INSERT INTO vip_payments(chat_id, user_name, months, price, receipt_file_id, receipt_kind, created_at) "
        "VALUES(?, ?, ?, ?, ?, ?, ?)",
        (cid, (name + (f" (@{user.username})" if user.username else ""))[:120], plan["months"], plan["price"],
         file_id, kind, int(time.time())),
    )
    bot.send_message(
        cid,
        "✅ <b>Chekingiz qabul qilindi!</b>\n\n"
        f"🧾 To'lov raqami: #{pid}\n"
        f"💎 Tarif: {plan_name(plan['months'])} — {money(plan['price'])}\n\n"
        "⏳ Admin tekshirgach, VIP obunangiz faollashadi va sizga xabar keladi.",
        reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton(text="🏠 Bosh menyu", callback_data="home")),
    )
    notify_admins(pid)


def payment_caption(p, status_line=""):
    sub = subscription(p["chat_id"])
    current = ""
    if sub and sub["expires_at"] > time.time():
        current = f"\n⚠️ Hozir VIP faol: {fmt_date(sub['expires_at'])} gacha (uzaytiriladi)"
    return (
        f"🧾 <b>Yangi to'lov #{p['id']}</b>\n\n"
        f"👤 <a href=\"tg://user?id={p['chat_id']}\">{html.escape(p['user_name'] or str(p['chat_id']))}</a>\n"
        f"🆔 ID: <code>{p['chat_id']}</code>\n"
        f"💎 Tarif: <b>{plan_name(p['months'])}</b>\n"
        f"💰 Summa: <b>{money(p['price'])}</b>\n"
        f"🕒 Vaqt: {fmt_dt(p['created_at'])}"
        f"{current}"
        f"{status_line}"
    )


def review_key(p):
    return InlineKeyboardMarkup().row(
        InlineKeyboardButton(text=f"✅ VIP qilish ({plan_name(p['months'])})", callback_data=f"vpa:{p['id']}"),
        InlineKeyboardButton(text="❌ Rad etish", callback_data=f"vpr:{p['id']}"),
    )


def send_payment_to(admin_id, p):
    send = bot.send_photo if p["receipt_kind"] == "photo" else bot.send_document
    # disable_notification=False — admin ovozli bildirishnoma oladi
    m = send(admin_id, p["receipt_file_id"], caption=payment_caption(p), reply_markup=review_key(p),
             disable_notification=False)
    return m.message_id


def notify_admins(pid):
    p = q1("SELECT * FROM vip_payments WHERE id=?", (pid,))
    sent = []
    for aid in admin_ids():
        try:
            sent.append([aid, send_payment_to(aid, p)])
        except Exception as e:
            print(f"admin {aid} ga chek yuborilmadi:", e)
    ex("UPDATE vip_payments SET admin_msgs=? WHERE id=?", (json.dumps(sent), pid))


def _close_admin_messages(p, status_line):
    for aid, mid in json.loads(p["admin_msgs"] or "[]"):
        try:
            bot.edit_message_caption(payment_caption(p, status_line), aid, mid, reply_markup=None)
        except Exception as e:
            if "not modified" not in str(e):
                print("admin xabarini yangilash xato:", e)


def _claim(pid, admin_id, status, reason=None):
    """To'lovni bir marta ko'rib chiqish (ikki admin bir vaqtda bossa ham)."""
    _, changed = ex(
        "UPDATE vip_payments SET status=?, reviewed_by=?, reviewed_at=?, reject_reason=? WHERE id=? AND status='pending'",
        (status, admin_id, int(time.time()), reason, pid),
    )
    return changed == 1


def approve(pid, admin_id):
    if not _claim(pid, admin_id, "approved"):
        return None
    p = q1("SELECT * FROM vip_payments WHERE id=?", (pid,))
    starts, expires = grant(p["chat_id"], p["months"], by=admin_id)
    _close_admin_messages(p, f"\n\n✅ <b>Tasdiqlandi</b> — admin <code>{admin_id}</code>\n⏳ {fmt_date(expires)} gacha")
    try:
        bot.send_message(
            p["chat_id"],
            "🎉 <b>Tabriklaymiz! VIP obunangiz faollashtirildi!</b>\n\n"
            f"💎 Tarif: <b>{plan_name(p['months'])}</b>\n"
            f"📅 Boshlanish: {fmt_date(starts)}\n"
            f"⏳ Amal qilish muddati: <b>{fmt_date(expires)}</b> gacha\n\n"
            "🔔 Obuna tugashiga 5 kun qolganda eslatib qo'yamiz.\n🍿 Yoqimli tomosha!",
            reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton(text="🏠 Bosh menyu", callback_data="home")),
        )
    except Exception as e:
        print("userga VIP xabari yuborilmadi:", e)
    return expires


def reject(pid, admin_id, reason=None):
    if not _claim(pid, admin_id, "rejected", reason):
        return False
    p = q1("SELECT * FROM vip_payments WHERE id=?", (pid,))
    why = f"\n📝 Sabab: {html.escape(reason, quote=False)}" if reason else ""
    _close_admin_messages(p, f"\n\n❌ <b>Rad etildi</b> — admin <code>{admin_id}</code>{why}")
    key = InlineKeyboardMarkup().add(InlineKeyboardButton(text="🔄 Qayta yuborish", callback_data="vip_buy"))
    if contact_button():
        key.add(contact_button())
    try:
        bot.send_message(
            p["chat_id"],
            f"❌ <b>To'lovingiz (#{pid}) tasdiqlanmadi.</b>{why}\n\n"
            "Chekni tekshirib, qaytadan yuboring yoki admin bilan bog'laning.",
            reply_markup=key,
        )
    except Exception as e:
        print("userga rad xabari yuborilmadi:", e)
    return True


def reject_reason_step(msg, pid):
    reason = None if (msg.text or "").strip() in ("", "/skip") else msg.text.strip()[:300]
    if reject(pid, msg.chat.id, reason):
        bot.reply_to(msg, f"❌ To'lov #{pid} rad etildi, userga xabar yuborildi.")
    else:
        bot.reply_to(msg, f"ℹ️ To'lov #{pid} allaqachon ko'rib chiqilgan.")


# ---------------- Eslatmalar ----------------

def renew_key():
    key = InlineKeyboardMarkup().add(InlineKeyboardButton(text="🔄 Obunani yangilash", callback_data="vip_buy"))
    if contact_button():
        key.add(contact_button())
    return key


def check_reminders(now=None):
    now = int(now or time.time())
    sent = 0
    for s in q("SELECT * FROM vip_subscriptions WHERE notified_soon=0 AND expires_at > ? AND expires_at - ? <= ?",
               (now, now, REMIND_BEFORE)):
        try:
            bot.send_message(
                s["chat_id"],
                "⚠️ <b>VIP tarifingiz obunasi tugamoqda!</b>\n\n"
                f"💎 Tarif: {plan_name(s['months'] or 1)}\n"
                f"⏳ Tugash sanasi: <b>{fmt_dt(s['expires_at'])}</b>\n"
                f"🕒 Qoldi: <b>{fmt_left(s['expires_at'] - now)}</b>\n\n"
                "Tomoshani to'xtatmaslik uchun obunani oldindan yangilang 👇",
                reply_markup=renew_key(),
            )
            sent += 1
        except Exception as e:
            print("eslatma yuborilmadi:", s["chat_id"], e)
        ex("UPDATE vip_subscriptions SET notified_soon=1 WHERE chat_id=?", (s["chat_id"],))
    for s in q("SELECT * FROM vip_subscriptions WHERE notified_end=0 AND expires_at <= ?", (now,)):
        try:
            bot.send_message(
                s["chat_id"],
                "⌛️ <b>VIP obunangiz muddati tugadi.</b>\n\nQayta yoqish uchun obunani yangilang 👇",
                reply_markup=renew_key(),
            )
            sent += 1
        except Exception as e:
            print("tugash xabari yuborilmadi:", s["chat_id"], e)
        ex("UPDATE vip_subscriptions SET notified_soon=1, notified_end=1 WHERE chat_id=?", (s["chat_id"],))
    return sent


def _reminder_loop():
    while True:
        try:
            check_reminders()
        except Exception as e:
            print("eslatmalar xato:", e)
        time.sleep(REMINDER_INTERVAL)


_reminders_started = False


def start_reminders():
    global _reminders_started
    if not _reminders_started:
        _reminders_started = True
        _default_contact()
        threading.Thread(target=_reminder_loop, name="vip-reminders", daemon=True).start()


# ---------------- Admin panel ----------------

def admin_text():
    now = int(time.time())
    active = q1("SELECT COUNT(*) n FROM vip_subscriptions WHERE expires_at > ?", (now,))["n"]
    soon = q1("SELECT COUNT(*) n FROM vip_subscriptions WHERE expires_at > ? AND expires_at - ? <= ?",
              (now, now, REMIND_BEFORE))["n"]
    pending = q1("SELECT COUNT(*) n FROM vip_payments WHERE status='pending'")["n"]
    month_start = int(datetime.now(TZ).replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp())
    income = q1("SELECT COALESCE(SUM(price), 0) s, COUNT(*) n FROM vip_payments WHERE status='approved' AND reviewed_at >= ?",
                (month_start,))
    cards = q1("SELECT COUNT(*) n FROM vip_cards WHERE active=1")["n"]
    contact = get_setting("admin_contact") or "kiritilmagan"
    return (
        "💎 <b>VIP boshqaruvi</b>\n\n"
        f"🔒 VIP rejimi: <b>{'✅ YOQILGAN' if vip_mode_on() else '⛔️ O‘CHIRILGAN'}</b>\n"
        f"<i>{'Kino va seriallarni faqat VIP userlar ko‘radi.' if vip_mode_on() else 'Hozir hamma bepul ko‘ra oladi.'}</i>\n\n"
        f"👥 Faol VIP: <b>{active}</b> (5 kun ichida tugaydi: {soon})\n"
        f"🧾 Kutilayotgan cheklar: <b>{pending}</b>\n"
        f"💰 Shu oy: {money(income['s'])} ({income['n']} ta to'lov)\n"
        f"💳 Faol kartalar: {cards}\n"
        f"👨‍💻 Murojaat: {html.escape(contact)}"
    )


def admin_key():
    pending = q1("SELECT COUNT(*) n FROM vip_payments WHERE status='pending'")["n"]
    key = InlineKeyboardMarkup()
    key.add(InlineKeyboardButton(text="⛔️ VIP rejimini o'chirish" if vip_mode_on() else "✅ VIP rejimini yoqish",
                                 callback_data="va_mode"))
    key.add(InlineKeyboardButton(text=f"🧾 Kutilayotgan cheklar ({pending})", callback_data="va_pending"))
    key.row(InlineKeyboardButton(text="💳 Kartalar", callback_data="va_cards"),
            InlineKeyboardButton(text="💰 Tariflar", callback_data="va_plans"))
    key.row(InlineKeyboardButton(text="👥 VIP userlar", callback_data="va_subs"),
            InlineKeyboardButton(text="👨‍💻 Murojaat", callback_data="va_contact"))
    key.row(InlineKeyboardButton(text="➕ VIP berish", callback_data="va_grant"),
            InlineKeyboardButton(text="➖ VIP olish", callback_data="va_revoke"))
    key.add(InlineKeyboardButton(text="🔙 Admin panel", callback_data="back"))
    return key


def back_key(cb="va_menu", text="🔙 Ortga"):
    return InlineKeyboardMarkup().add(InlineKeyboardButton(text=text, callback_data=cb))


def cards_text():
    cards = q("SELECT * FROM vip_cards ORDER BY id")
    if not cards:
        return "💳 <b>Kartalar</b>\n\nHali karta yo'q. «➕ Karta qo'shish» ni bosing."
    lines = ["💳 <b>Kartalar</b>\n"]
    for c in cards:
        bank = f" ({html.escape(c['bank'])})" if c["bank"] else ""
        lines.append(f"{'✅' if c['active'] else '⏸'} <code>{html.escape(c['number'])}</code>{bank} — {html.escape(c['holder'])}")
    lines.append("\nKartani bosib yoqing/o'chiring, ❌ — o'chirib tashlash.")
    return "\n".join(lines)


def cards_key():
    key = InlineKeyboardMarkup()
    for c in q("SELECT * FROM vip_cards ORDER BY id"):
        key.row(
            InlineKeyboardButton(text=f"{'✅' if c['active'] else '⏸'} {c['number'][-4:]} • {c['holder']}"[:40],
                                 callback_data=f"va_card_t:{c['id']}"),
            InlineKeyboardButton(text="❌", callback_data=f"va_card_d:{c['id']}"),
        )
    key.add(InlineKeyboardButton(text="➕ Karta qo'shish", callback_data="va_card_add"))
    key.add(InlineKeyboardButton(text="🔙 Ortga", callback_data="va_menu"))
    return key


def add_card_step(msg):
    parts = [p.strip() for p in (msg.text or "").split("|")]
    number = re.sub(r"[^\d ]", "", parts[0]) if parts else ""
    digits = re.sub(r"\D", "", number)
    if len(parts) < 2 or not 16 <= len(digits) <= 19 or not parts[1]:
        bot.reply_to(msg, "❌ Format noto'g'ri. Namuna:\n<code>9860 1234 5678 9012 | Ism Familiya | Humo</code>",
                     reply_markup=cards_key())
        return
    pretty = " ".join(digits[i:i + 4] for i in range(0, len(digits), 4))
    ex("INSERT INTO vip_cards(number, holder, bank) VALUES(?, ?, ?)",
       (pretty, parts[1][:60], (parts[2][:30] if len(parts) > 2 and parts[2] else None)))
    bot.reply_to(msg, "✅ Karta qo'shildi.\n\n" + cards_text(), reply_markup=cards_key())


def plans_admin_text():
    lines = ["💰 <b>Tariflar</b>\n"]
    for p in q("SELECT * FROM vip_plans ORDER BY months"):
        lines.append(f"{'✅' if p['active'] else '⏸'} {plan_name(p['months'])} — {money(p['price'])}")
    lines.append("\nNarxni o'zgartirish uchun tarifni bosing.")
    return "\n".join(lines)


def plans_admin_key():
    key = InlineKeyboardMarkup()
    for p in q("SELECT * FROM vip_plans ORDER BY months"):
        key.row(
            InlineKeyboardButton(text=f"✏️ {plan_name(p['months'])} — {money(p['price'])}", callback_data=f"va_plan_p:{p['id']}"),
            InlineKeyboardButton(text="⏸ O'chirish" if p["active"] else "▶️ Yoqish", callback_data=f"va_plan_t:{p['id']}"),
        )
    key.add(InlineKeyboardButton(text="🔙 Ortga", callback_data="va_menu"))
    return key


def plan_price_step(msg, plan_id):
    digits = re.sub(r"\D", "", msg.text or "")
    if not digits or not 1000 <= int(digits) <= 100_000_000:
        bot.reply_to(msg, "❌ Narx noto'g'ri. Masalan: <code>39000</code>", reply_markup=plans_admin_key())
        return
    ex("UPDATE vip_plans SET price=? WHERE id=?", (int(digits), plan_id))
    bot.reply_to(msg, "✅ Narx yangilandi.\n\n" + plans_admin_text(), reply_markup=plans_admin_key())


def subs_text():
    now = int(time.time())
    rows = q("SELECT * FROM vip_subscriptions WHERE expires_at > ? ORDER BY expires_at LIMIT 40", (now,))
    if not rows:
        return "👥 <b>VIP userlar</b>\n\nHozircha faol VIP yo'q."
    total = q1("SELECT COUNT(*) n FROM vip_subscriptions WHERE expires_at > ?", (now,))["n"]
    lines = [f"👥 <b>Faol VIP userlar</b> — {total} ta (tugashi yaqinlari birinchi)\n"]
    for s in rows:
        lines.append(f"• <a href=\"tg://user?id={s['chat_id']}\">{s['chat_id']}</a> — {plan_name(s['months'] or 1)}, "
                     f"{fmt_date(s['expires_at'])} gacha ({fmt_left(s['expires_at'] - now)})")
    return "\n".join(lines)


def grant_step(msg):
    nums = [int(n) for n in re.findall(r"\d+", msg.text or "")]
    if len(nums) < 2 or nums[1] not in (1, 3, 6, 12):
        bot.reply_to(msg, "❌ Format: <code>USER_ID OY</code> — masalan <code>123456789 3</code> (oy: 1, 3, 6 yoki 12)",
                     reply_markup=back_key())
        return
    uid, months = nums[0], nums[1]
    starts, expires = grant(uid, months, by=msg.chat.id)
    bot.reply_to(msg, f"✅ <code>{uid}</code> ga VIP berildi: {plan_name(months)}, {fmt_date(expires)} gacha.",
                 reply_markup=back_key())
    try:
        bot.send_message(
            uid,
            "🎉 <b>Sizga VIP obuna berildi!</b>\n\n"
            f"💎 Tarif: <b>{plan_name(months)}</b>\n⏳ Amal qilish muddati: <b>{fmt_date(expires)}</b> gacha",
            reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton(text="🏠 Bosh menyu", callback_data="home")),
        )
    except Exception as e:
        print("userga xabar yuborilmadi:", e)


def revoke_step(msg):
    nums = re.findall(r"\d+", msg.text or "")
    if not nums:
        bot.reply_to(msg, "❌ User ID kiriting.", reply_markup=back_key())
        return
    uid = int(nums[0])
    text = f"✅ <code>{uid}</code> dan VIP olib tashlandi." if revoke(uid) else f"ℹ️ <code>{uid}</code> da VIP yo'q edi."
    bot.reply_to(msg, text, reply_markup=back_key())


def contact_step(msg):
    text = (msg.text or "").strip()
    if text == "/skip":
        set_setting("admin_contact", "")
    elif re.fullmatch(r"@?[A-Za-z][A-Za-z0-9_]{4,31}", text):
        set_setting("admin_contact", "@" + text.lstrip("@"))
    else:
        bot.reply_to(msg, "❌ Username noto'g'ri. Masalan: <code>@admin_username</code>", reply_markup=back_key())
        return
    bot.reply_to(msg, "✅ Saqlandi.", reply_markup=back_key())


# ---------------- Callbacklar ----------------

USER_CALLBACKS = ("vip", "vip_buy", "vip_cancel")
USER_PREFIXES = ("vip_plan:", "vip_receipt:")
ADMIN_PREFIXES = ("va_", "vpa:", "vpr:")


def _is_vip_callback(call):
    d = call.data or ""
    return d in USER_CALLBACKS or d.startswith(USER_PREFIXES + ADMIN_PREFIXES)


@bot.callback_query_handler(func=_is_vip_callback)
def vip_callback(call):
    try:
        _handle(call)
    except Exception as e:
        print("vip callback xato:", call.data, e)
    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass


def _edit(call, text, key):
    msg = call.message
    try:
        if msg.content_type in ("photo", "video", "document"):
            raise ValueError("media")
        bot.edit_message_text(text, msg.chat.id, msg.id, reply_markup=key)
    except Exception:
        bot.send_message(msg.chat.id, text, reply_markup=key)


def _handle(call):
    d = call.data
    cid = call.message.chat.id if call.message else call.from_user.id

    if d.startswith(ADMIN_PREFIXES):
        if not get_admin(cid):
            return
        _handle_admin(call, d, cid)
        return

    if d == "vip":
        if call.message:
            _edit(call, status_text(cid), status_key(cid))
        else:
            show_vip(cid)
    elif d == "vip_buy":
        if not q1("SELECT 1 FROM vip_plans WHERE active=1"):
            bot.answer_callback_query(call.id, "Tariflar vaqtincha mavjud emas", show_alert=True)
            return
        text = "💎 <b>«VIP» obuna muddatini tanlang:</b>"
        if call.message:
            _edit(call, text, plans_key())
        else:
            bot.send_message(cid, text, reply_markup=plans_key())
    elif d.startswith("vip_plan:"):
        plan = q1("SELECT * FROM vip_plans WHERE id=? AND active=1", (int(d.split(":")[1]),))
        if plan is None:
            bot.answer_callback_query(call.id, "Bu tarif mavjud emas", show_alert=True)
            return
        has_cards = q1("SELECT 1 FROM vip_cards WHERE active=1") is not None
        _edit(call, payment_text(plan), payment_key(plan["id"], has_cards))
    elif d.startswith("vip_receipt:"):
        plan_id = int(d.split(":")[1])
        if q1("SELECT 1 FROM vip_plans WHERE id=? AND active=1", (plan_id,)) is None:
            return
        RECEIPT_WAIT[cid] = (plan_id, time.time())
        bot.send_message(
            cid,
            "🧾 <b>To'lov chekini yuboring</b>\n\nChek rasmini (skrinshot) yoki PDF faylini shu yerga yuboring 👇",
            reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton(text="❌ Bekor qilish", callback_data="vip_cancel")),
        )
    elif d == "vip_cancel":
        RECEIPT_WAIT.pop(cid, None)
        _edit(call, "❌ Bekor qilindi.", status_key(cid))


def _handle_admin(call, d, cid):
    bot.clear_step_handler_by_chat_id(cid)
    if d.startswith("vpa:"):
        pid = int(d.split(":")[1])
        expires = approve(pid, cid)
        bot.answer_callback_query(call.id, f"✅ VIP faollashtirildi: {fmt_date(expires)} gacha" if expires
                                  else "ℹ️ Bu to'lov allaqachon ko'rib chiqilgan", show_alert=True)
    elif d.startswith("vpr:"):
        pid = int(d.split(":")[1])
        p = q1("SELECT status FROM vip_payments WHERE id=?", (pid,))
        if not p or p["status"] != "pending":
            bot.answer_callback_query(call.id, "ℹ️ Bu to'lov allaqachon ko'rib chiqilgan", show_alert=True)
            return
        a = bot.send_message(cid, f"❌ To'lov #{pid} ni rad etish sababini yozing (userga yuboriladi).\nSababsiz: /skip")
        bot.register_next_step_handler(a, reject_reason_step, pid)
    elif d == "va_menu":
        _edit(call, admin_text(), admin_key())
    elif d == "va_mode":
        if not vip_mode_on() and not q1("SELECT 1 FROM vip_cards WHERE active=1"):
            bot.answer_callback_query(call.id, "⚠️ Avval kamida bitta karta qo'shing — aks holda userlar to'lov qila olmaydi.",
                                      show_alert=True)
            return
        set_setting("vip_mode", "0" if vip_mode_on() else "1")
        _edit(call, admin_text(), admin_key())
    elif d == "va_pending":
        rows = q("SELECT * FROM vip_payments WHERE status='pending' ORDER BY id LIMIT 10")
        if not rows:
            bot.answer_callback_query(call.id, "Kutilayotgan chek yo'q ✅", show_alert=True)
            return
        for p in rows:
            msgs = json.loads(p["admin_msgs"] or "[]")
            msgs.append([cid, send_payment_to(cid, p)])
            ex("UPDATE vip_payments SET admin_msgs=? WHERE id=?", (json.dumps(msgs), p["id"]))
    elif d == "va_cards":
        _edit(call, cards_text(), cards_key())
    elif d.startswith("va_card_t:"):
        ex("UPDATE vip_cards SET active = 1 - active WHERE id=?", (int(d.split(":")[1]),))
        _edit(call, cards_text(), cards_key())
    elif d.startswith("va_card_d:"):
        ex("DELETE FROM vip_cards WHERE id=?", (int(d.split(":")[1]),))
        _edit(call, cards_text(), cards_key())
    elif d == "va_card_add":
        a = bot.send_message(cid, "💳 Karta ma'lumotlarini quyidagi formatda yuboring:\n\n"
                                  "<code>9860 1234 5678 9012 | Ism Familiya | Humo</code>\n\n(bank nomi ixtiyoriy)",
                             reply_markup=back_key("va_cards"))
        bot.register_next_step_handler(a, add_card_step)
    elif d == "va_plans":
        _edit(call, plans_admin_text(), plans_admin_key())
    elif d.startswith("va_plan_t:"):
        ex("UPDATE vip_plans SET active = 1 - active WHERE id=?", (int(d.split(":")[1]),))
        _edit(call, plans_admin_text(), plans_admin_key())
    elif d.startswith("va_plan_p:"):
        plan = q1("SELECT * FROM vip_plans WHERE id=?", (int(d.split(":")[1]),))
        if plan:
            a = bot.send_message(cid, f"✏️ {plan_name(plan['months'])} tarifining yangi narxini yuboring (so'mda):",
                                 reply_markup=back_key("va_plans"))
            bot.register_next_step_handler(a, plan_price_step, plan["id"])
    elif d == "va_subs":
        _edit(call, subs_text(), back_key())
    elif d == "va_grant":
        a = bot.send_message(cid, "➕ VIP berish: <code>USER_ID OY</code> yuboring\nMasalan: <code>123456789 3</code>",
                             reply_markup=back_key())
        bot.register_next_step_handler(a, grant_step)
    elif d == "va_revoke":
        a = bot.send_message(cid, "➖ VIP olib tashlash uchun user ID yuboring:", reply_markup=back_key())
        bot.register_next_step_handler(a, revoke_step)
    elif d == "va_contact":
        a = bot.send_message(cid, "👨‍💻 Userlar murojaat qiladigan admin username'ini yuboring (masalan <code>@admin_username</code>).\n"
                                  "O'chirish: /skip", reply_markup=back_key())
        bot.register_next_step_handler(a, contact_step)
