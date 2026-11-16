import html
import re
import time
import telebot
from telebot.types import *
import sqlite3
import speedtest
import os
import threading

from ratings import (
    _meta_line,
    cards_from_search_rows,
    inline_rating_results,
    is_rating_query,
    lookup_rating,
    rating_films_keyboard,
    rating_list_text,
    split_genres,
)

BASE = {}

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "database.db")


def _load_env(path):
    # .env faylidan KEY=VALUE qatorlarini o'qiydi (python-dotenv shart emas)
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env(os.path.join(BASE_DIR, ".env"))

# Sozlamalar .env faylidan olinadi (namuna: .env.example, yo'riqnoma: README.md)
BOT_NAME = os.environ.get("BOT_NAME", "").strip() or "Kino Bot"
CHANNEL = os.environ.get("CHANNEL", "").strip()
if CHANNEL and not CHANNEL.startswith(("@", "-100")):
    CHANNEL = "@" + CHANNEL
# database.db birinchi ishga tushirishda shu papkada avtomatik yaratiladi

class _Result:
    """So'rov natijasi: qatorlar lock ichida o'qib olingan, keyin xavfsiz ishlatiladi."""

    def __init__(self, rows, rowcount, lastrowid):
        self._rows, self._pos = rows, 0
        self.rowcount, self.lastrowid = rowcount, lastrowid

    def fetchone(self):
        if self._pos >= len(self._rows):
            return None
        self._pos += 1
        return self._rows[self._pos - 1]

    def fetchall(self):
        rows, self._pos = self._rows[self._pos:], len(self._rows)
        return rows

    def __iter__(self):
        return iter(self.fetchall())


class _ThreadSafeCursor:
    """Bitta umumiy sqlite cursor'ni bir nechta oqim ishlatganda jarayon yiqilishi mumkin (segfault).

    telebot handlerlari parallel oqimlarda ishlaydi, shuning uchun har bir so'rov lock ostida
    alohida cursor'da bajariladi va natija darhol o'qib olinadi. Interfeys oddiy cursor bilan bir xil:
    cursor.execute(...).fetchone() / .fetchall() / .rowcount
    """

    def __init__(self, connection):
        self._conn = connection
        self._lock = threading.RLock()
        self._local = threading.local()

    def execute(self, sql, params=()):
        with self._lock:
            cur = self._conn.execute(sql, params)
            try:
                rows = cur.fetchall() if cur.description else []
                result = _Result(rows, cur.rowcount, cur.lastrowid)
            finally:
                cur.close()
        self._local.last = result
        return result

    @property
    def rowcount(self):
        return getattr(self._local, "last", _Result([], -1, None)).rowcount

    def fetchone(self):
        return self._local.last.fetchone()

    def fetchall(self):
        return self._local.last.fetchall()


conn = sqlite3.connect(DB_PATH,
                       check_same_thread=False,
                       isolation_level=None,
                       timeout=10)
cursor = _ThreadSafeCursor(conn)

cursor.execute("CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,chat_id INTIGER UNIQUE)")
cursor.execute("CREATE TABLE IF NOT EXISTS admins(id INTEGER PRIMARY KEY,chat_id INTIGER UNIQUE)")
cursor.execute("CREATE TABLE IF NOT EXISTS chats(id INTEGER PRIMARY KEY,username TEXT UNIQUE)")
cursor.execute("CREATE TABLE IF NOT EXISTS video(id INTEGER UNIQUE,file_id TEXT,caption TEXT,down INTIGER)")
cursor.execute("CREATE TABLE IF NOT EXISTS saved(id INTEGER PRIMARY KEY, chat_id INTEGER, video_id INTEGER, UNIQUE(chat_id, video_id))")
cursor.execute(
    "CREATE TABLE IF NOT EXISTS comments(id INTEGER PRIMARY KEY, video_id INTEGER, chat_id INTEGER, name TEXT, text TEXT, created INTEGER)"
)
# Admin tanlagan "Top filmlar" ro'yxati (video jadvaliga tegmaydi)
cursor.execute(
    "CREATE TABLE IF NOT EXISTS top_films(video_id INTEGER PRIMARY KEY, added_by INTEGER, added_at INTEGER)"
)

conn.commit()

BTN_SEARCH = "🔍 Film qidiruvi"
BTN_TOP = "🏆 TOP filmlar"
BTN_AI = "✨ Sun'iy intellekt"
BTN_SAVED = "🔖 Saqlangan"
BTN_VIP = "👑 VIP"
BTN_HELP = "ℹ️ Yordam"
MENU_BUTTONS = {BTN_SEARCH, BTN_TOP, BTN_AI, BTN_SAVED, BTN_VIP, BTN_HELP}


def user_display_name(user):
    if user.username:
        return html.escape(user.username)
    return html.escape(user.first_name or "foydalanuvchi")


def welcome_text(user):
    name = user_display_name(user)
    return (
        f"👋 <b>Assalomu alaykum {name}, {html.escape(BOT_NAME)} botimizga xush kelibsiz!</b>\n\n"
        "🎥 <i>Bot orqali siz sevimli filmlar, seriallar va multfilmlarni sifatli formatda ko'rishingiz mumkin</i>\n\n"
        "🚀 <u>Shunchaki:</u>\n"
        "— Kino yoki serialning kodini yuboring\n"
        "— Pastdagi bo'limlardan birini tanlang va zavqlaning! 😉\n\n"
        f"⭐️ <b>{html.escape(BOT_NAME)} - @{bot_username()}</b>"
    )


# Ochiq kanal (@username) bo'lsa «Barcha Filmlar» tugmasi shu kanalga olib boradi
ALL_FILMS_URL = f"https://t.me/{CHANNEL[1:]}" if CHANNEL.startswith("@") else None


class InlineSwitchButton(InlineKeyboardButton):
    """telebot bo'sh switch_inline_query_current_chat ni tashlab yuboradi, bu yerda majburan qo'shamiz."""

    def to_dict(self):
        d = super().to_dict()
        d["switch_inline_query_current_chat"] = self.switch_inline_query_current_chat or ""
        return d


_inline_state = {"on": None, "at": 0}


def inline_mode_on():
    # BotFather'da inline mode yoqilganini tekshiradi (1 daqiqa keshlanadi)
    if _inline_state["on"] is None or time.time() - _inline_state["at"] > 60:
        try:
            _inline_state["on"] = bool(bot.get_me().supports_inline_queries)
        except Exception:
            _inline_state["on"] = bool(_inline_state["on"])
        _inline_state["at"] = time.time()
    return _inline_state["on"]


def main_menu():
    key = InlineKeyboardMarkup()
    if inline_mode_on():
        key.add(InlineSwitchButton(text="🔍 Film qidirish", switch_inline_query_current_chat=""))
        key.row(
            InlineSwitchButton(text="📊 Top Filmlar", switch_inline_query_current_chat="top"),
            InlineSwitchButton(text="⭐️ Saqlangan", switch_inline_query_current_chat="saqlangan"),
        )
    else:
        key.add(InlineKeyboardButton(text="🔍 Film qidirish", callback_data="sq_menu"))
        key.row(
            InlineKeyboardButton(text="📊 Top Filmlar", callback_data="menu_top"),
            InlineKeyboardButton(text="⭐️ Saqlangan", callback_data="menu_saved"),
        )
    if inline_mode_on():
        key.add(InlineSwitchButton(text="📺 Seriallar", switch_inline_query_current_chat="seriallar"))
    else:
        key.add(InlineKeyboardButton(text="📺 Seriallar", callback_data="menu_series"))
    row = [InlineKeyboardButton(text="💎 VIP", callback_data="vip")]
    if ALL_FILMS_URL:
        row.append(InlineKeyboardButton(text="🎬 Barcha Filmlar", url=ALL_FILMS_URL))
    key.row(*row)
    return key


GENRE_TAGS = {
    "dramma": "Drama",
    "drama": "Drama",
    "ramantika": "Romantika",
    "romantika": "Romantika",
    "jangari": "Jangari",
    "ekshn": "Ekshn",
    "action": "Ekshn",
    "aksiya": "Ekshn",
    "triller": "Triller",
    "thriller": "Triller",
    "fantastika": "Fantastika",
    "fantastik": "Fantastika",
    "sci-fi": "Fantastika",
    "kriminal": "Kriminal",
    "crime": "Kriminal",
    "komediya": "Komediya",
    "comedy": "Komediya",
    "multfilm": "Multfilm",
    "multikino": "Multfilm",
    "animation": "Multfilm",
    "horror": "Qorqinchli",
    "qo'rqinchli": "Qorqinchli",
    "qorqinchli": "Qorqinchli",
    "melodrama": "Melodrama",
    "detektiv": "Detektiv",
    "oilaviy": "Oilaviy",
    "serial": "Serial",
    "tarixiy": "Tarixiy",
    "ajoyib": "Ajoyib",
}


def get_film(code):
    return cursor.execute("SELECT * FROM video WHERE id=?", (code,)).fetchone()


def _norm_text(s):
    return (
        str(s or "")
        .replace("||", "'")
        .replace("’", "'")
        .replace("‘", "'")
        .replace("`", "'")
        .replace("´", "'")
    )


def h(s):
    return html.escape(str(s or ""), quote=False)


def _field(cap, *labels):
    for lab in labels:
        m = re.search(rf"{re.escape(lab)}\s*:\s*(.+)", cap, re.I)
        if m:
            val = m.group(1).strip().split("|")[0].strip()
            val = re.sub(r"\s+", " ", val)
            if val:
                return val
    return ""


def _norm_lang(lang):
    t = (lang or "").lower()
    if "uz" in t or "o'z" in t or "ozbek" in t:
        return "O'zbek"
    if "rus" in t:
        return "Rus"
    if "ingliz" in t or "english" in t:
        return "Ingliz"
    if "turk" in t:
        return "Turk"
    if "kore" in t:
        return "Koreys"
    if "hind" in t:
        return "Hind"
    return (lang or "").strip() or "O'zbek"


def _genre_tags(genre):
    parts = re.split(r"[,/|]| va ", genre or "")
    tags = []
    for part in parts:
        part = re.sub(r"[#]+", "", part).strip()
        if not part:
            continue
        key = part.lower().replace("'", "").replace(" ", "")
        name = GENRE_TAGS.get(key, part[:1].upper() + part[1:] if part else part)
        tag = "#" + re.sub(r"\s+", "", name)
        if tag not in tags:
            tags.append(tag)
    return " ".join(tags) if tags else "#Kino"


def _guess_age(title, genre, age):
    if age:
        m = re.search(r"(\d{1,2}\+)", age)
        return m.group(1) if m else age
    blob = f"{title} {genre}".lower()
    m = re.search(r"(\d{1,2}\+)", blob)
    if m:
        return m.group(1)
    if any(x in blob for x in ["multfilm", "multikino", "animatsiya", "animation"]):
        return "6+"
    if any(x in blob for x in ["jangari", "triller", "horror", "qo'rq", "qorqinch", "kriminal"]):
        return "18+"
    return "16+"


def parse_film(row):
    code = row[0]
    file_id = row[1] if len(row) > 1 else ""
    down = row[3] if len(row) > 3 else 0
    cap = _norm_text(row[2] if len(row) > 2 else "")
    cap = cap.replace("%stat", str(down)).replace("%code", str(code))

    title = _field(cap, "Kino nomi", "Film nomi", "Nomi")
    if not title:
        for line in cap.splitlines():
            line = re.sub(r"^[\W_🔥\s]+", "", line).strip()
            line = re.sub(r"^(premyera|kino)\s*", "", line, flags=re.I).strip(" :-")
            if not line or line.startswith("#") or "obunachilar" in line.lower():
                continue
            if re.search(r"(janr|tili|sifat|kod|davlat|yil|hajm|obuna)", line, re.I):
                continue
            title = re.sub(r"^[🎬\s]+", "", line).strip()
            if title:
                break
    title = re.sub(r"\s+", " ", title or "").strip() or f"Film {code}"

    country = _field(cap, "Davlati", "Davlat", "Mamlakat")
    lang = _norm_lang(_field(cap, "Tili", "Til"))
    quality = _field(cap, "Sifati", "Sifat")
    qmatch = re.search(r"(4K|2160p|1080p|720p|480p|360p)", f"{quality} {cap}", re.I)
    quality = qmatch.group(1) if qmatch else (quality or "720p")
    year = _field(cap, "Yili", "Yil")
    ymatch = re.search(r"\b(19\d{2}|20[0-3]\d)\b", year or cap)
    year = ymatch.group(1) if ymatch else ""
    imdb = _field(cap, "IMDb", "Imdb", "Reyting")
    if not imdb:
        m = re.search(r"IMDb\s*[:\-]?\s*([\d.]+(?:\s*/\s*10)?)", cap, re.I)
        imdb = m.group(1) if m else ""
    imdb = re.sub(r"\s+", "", imdb)
    if imdb and "/" not in imdb:
        imdb = f"{imdb}/10"
    genre = _field(cap, "Janri", "Janr")
    age = _field(cap, "Yosh chegarasi", "Yosh")
    actors = _field(cap, "Aktyorlar", "Aktyor", "Rollarda", "Rollar")
    size = _field(cap, "Hajmi", "Hajm")
    duration = _field(cap, "Davomiyligi", "Davomiylik", "Vaqti")
    trailer = ""
    m = re.search(r"(https?://\S+)", cap, re.I)
    if m:
        trailer = m.group(1).rstrip(").,")

    extra = lookup_rating(title)
    if extra:
        if not country:
            country = extra.get("country") or ""
        if not year:
            year = str(extra.get("year") or "")
        if not imdb and extra.get("rating"):
            imdb = f"{extra['rating']:.1f}/10"

    tag_parts = []
    skip_tags = {"#Kino", "#Ajoyib", "#Qiziqarli", "#Tarjimakino"}
    for source in (genre, ", ".join((extra or {}).get("genres") or [])):
        for tag in _genre_tags(source).split():
            if tag in skip_tags or tag in tag_parts:
                continue
            tag_parts.append(tag)
    if not tag_parts:
        tag_parts = [t for t in _genre_tags(genre).split() if t]
    genre_tags = " ".join(tag_parts) if tag_parts else "#Kino"

    return {
        "code": code,
        "file_id": file_id,
        "down": down,
        "title": title,
        "country": country,
        "lang": lang,
        "quality": quality,
        "year": year,
        "imdb": imdb,
        "genre": genre,
        "genre_tags": genre_tags,
        "age": _guess_age(title, f"{genre} {genre_tags}", age),
        "actors": actors,
        "size": size,
        "duration": duration,
        "trailer": trailer,
        "raw": cap,
    }


def format_film_caption(info):
    title = h(info["title"])
    country = h(info["country"] or "Noma'lum")
    lang = h(info["lang"] or "O'zbek")
    quality = h(info["quality"] or "720p")
    year = h(info["year"] or "—")
    imdb = h(info["imdb"] or "—")
    genres = h(info["genre_tags"] or "#Kino")
    age = h(info["age"] or "16+")
    return (
        f"🎬 <b>{title}</b>\n"
        f"-------------\n"
        f"🌍 Davlat: {country}\n"
        f"🎤 Til: {lang} | 🖥 Sifat: {quality}\n"
        f"📅 Yil: {year} | ⭐ IMDb: {imdb}\n"
        f"🎭 Janr: {genres}\n"
        f"🔞 Yosh chegarasi: {age}\n"
        f"📁 Film kodi: {info['code']}\n"
        f"📥 Yuklangan: {info['down']}\n"
        f"✅ <b>{html.escape(BOT_NAME)}</b> | @{bot_username()}"
    )


def film_about_text(info):
    country = h(info["country"] or "Noma'lum")
    lang = h(info["lang"] or "O'zbek")
    lines = [f"ℹ️ <b>Film haqida</b>\n\n🎬 <b>{h(info['title'])}</b>"]
    if info.get("duration"):
        lines.append(f"⏱ Davomiyligi: {h(info['duration'])}")
    if info.get("size"):
        lines.append(f"💾 Hajmi: {h(info['size'])}")
    lines.append(f"🌍 Davlat: {country}")
    lines.append(f"🎤 Til: {lang}")
    lines.append(f"🖥 Sifat: {h(info['quality'] or '720p')}")
    if info.get("year"):
        lines.append(f"📅 Yil: {h(info['year'])}")
    if info.get("imdb"):
        lines.append(f"⭐ IMDb: {h(info['imdb'])}")
    lines.append(f"🎭 Janr: {h(info['genre_tags'])}")
    lines.append(f"🔞 Yosh: {h(info['age'])}")
    lines.append(f"📁 Kod: {info['code']}")
    return "\n".join(lines)


def film_actors_text(info):
    title = h(info["title"])
    if info.get("actors"):
        return f"👥 <b>Aktyorlar</b> — {title}\n\n{h(info['actors'])}"
    return f"👥 <b>Aktyorlar</b> — {title}\n\nBu film uchun aktyorlar ro'yxati kiritilmagan."


def comments_text(code):
    row = get_film(code)
    title = parse_film(row)["title"] if row else str(code)
    rows = cursor.execute(
        "SELECT name, text FROM comments WHERE video_id=? ORDER BY id DESC LIMIT 8",
        (code,),
    ).fetchall()
    if not rows:
        body = "Hozircha sharhlar yo'q. Birinchi bo'lib yozing!"
    else:
        parts = []
        for i, (name, text) in enumerate(reversed(rows), 1):
            parts.append(f"{i}. <b>{name}</b>: {h(text)}")
        body = "\n".join(parts)
    return f"💬 <b>Sharhlar</b> — {h(title)}\n\n{body}"


def comments_key(code):
    key = InlineKeyboardMarkup()
    key.add(InlineKeyboardButton(text="✍️ Sharh yozish", callback_data=f"ca-{code}"))
    return key


def film_title(row):
    return parse_film(row)["title"]


def films_keyboard(rows, back="sq_menu"):
    key = InlineKeyboardMarkup()
    for row in rows[:15]:
        title = film_title(row)[:40]
        key.add(InlineKeyboardButton(text=f"{row[0]} • {title}", callback_data=f"film-{row[0]}"))
    if back:
        key.add(InlineKeyboardButton(text="⬅️ Ortga", callback_data=back))
    return key


def top_rated_films(limit=15):
    rows = cursor.execute("SELECT * FROM video").fetchall()
    best = {}
    for row in rows:
        info = parse_film(row)
        entry = lookup_rating(info["title"])
        rating = None
        if info.get("imdb"):
            try:
                rating = float(str(info["imdb"]).split("/")[0].replace(",", "."))
            except ValueError:
                rating = None
        if rating is None and entry:
            rating = entry["rating"]
        if rating is None:
            continue
        year = info["year"] or (str(entry["year"]) if entry else "")
        country = info["country"] or (entry["country"] if entry else "")
        genres = split_genres(info.get("genre") or "")
        if not genres and entry:
            genres = list(entry["genres"])
        views = int(info["down"] or 0)
        key = entry["key"] if entry else norm_title_key(info["title"])
        card = {
            "id": info["code"],
            "title": info["title"],
            "year": year,
            "rating": rating,
            "views": views,
            "country": country,
            "genres": genres,
            "line1": f"📅 {year or '—'} | ⭐ {rating:.1f} | 👁 {views}",
            "line2": _meta_line(country, genres),
            "key": key,
        }
        card["description"] = f"{card['line1']}\n{card['line2']}"
        prev = best.get(key)
        if prev is None or views > prev["views"]:
            best[key] = card
    cards = list(best.values())
    cards.sort(key=lambda c: (c["rating"], c["views"]), reverse=True)
    return cards[:limit]


def norm_title_key(title):
    return lookup_rating(title)["key"] if lookup_rating(title) else title.lower()


def rating_button():
    # Inline ".top:rating" so'rovi qidiruvda tanilmay bo'sh natija berardi; "top" esa endi admin
    # tanlagan Top ro'yxati. Reyting bo'yicha ro'yxat callback orqali to'g'ri ishlaydi.
    return InlineKeyboardButton(text="🏆 Reytingi yuqori", callback_data="sq_rating")


def subscribe_text():
    return (
        "🚫 <b>Botdan to'liq foydalanish uchun quyidagi kanallarga obuna bo'ling!</b>\n\n"
        "👑 Yoki «<b>VIP</b>» obunasini sotib oling va botdan hech qanday kanallarga obuna bo'lmasdan, cheklovlarsiz foydalaning!"
    )


def search_type_text():
    return "🗂 <b>Iltimos, qidiruv turini tanlang:</b>"


def search_type_key():
    key = InlineKeyboardMarkup()
    key.add(InlineKeyboardButton(text="🔍 Nom orqali qidiruv", callback_data="sq_name"))
    key.row(
        InlineKeyboardButton(text="🗂 Filter", callback_data="sq_filter"),
        InlineKeyboardButton(text="🔄 Tasodify film", callback_data="sq_random"),
    )
    key.add(rating_button())
    key.add(InlineKeyboardButton(text="📥 Eng ko'p yuklangan", callback_data="sq_top"))
    key.add(InlineKeyboardButton(text="📦 Film buyurtma qilish", callback_data="sq_order"))
    key.add(InlineKeyboardButton(text="⬅️ Ortga", callback_data="sq_back"))
    return key


FILTER_GENRES = [
    ("🎭 Drama", "drama", ["Drama"]),
    ("😂 Komediya", "komediya", ["Komediya", "Comedy"]),
    ("🔫 Jangari", "jangari", ["Jangari", "Action", "Aksiya"]),
    ("👻 Qo'rqinchli", "horror", ["Qo'rqinchli", "Qorqinchli", "Horror"]),
    ("🚀 Fantastika", "sci", ["Fantastika", "Sci-Fi", "Fantastic"]),
    ("💕 Melodrama", "melo", ["Melodrama"]),
    ("🔍 Detektiv", "det", ["Detektiv", "Detective"]),
    ("🎬 Triller", "thr", ["Triller", "Thriller"]),
    ("👨‍👩‍👧 Oilaviy", "fam", ["Oilaviy", "Family"]),
    ("🎪 Multfilm", "mult", ["Multfilm", "Animation", "Animatsiya"]),
    ("📺 Serial", "ser", ["Serial"]),
    ("⚔️ Tarixiy", "hist", ["Tarixiy", "Historical"]),
]

FILTER_COUNTRIES = [
    ("🇺🇿 O'zbekiston", "uz", ["O'zbekiston", "Ozbekiston", "Uzbekistan"]),
    ("🇺🇸 AQSH", "us", ["AQSH", "AQSh", "USA", "Amerika"]),
    ("🇮🇳 Hindiston", "in", ["Hindiston", "Hind", "India"]),
    ("🇹🇷 Turkiya", "tr", ["Turkiya", "Turkey"]),
    ("🇰🇷 Koreya", "kr", ["Koreya", "Korea"]),
    ("🇷🇺 Rossiya", "ru", ["Rossiya", "Russia"]),
    ("🇬🇧 Britaniya", "gb", ["Britaniya", "England", "Britan"]),
    ("🇫🇷 Fransiya", "fr", ["Fransiya", "France"]),
    ("🇯🇵 Yaponiya", "jp", ["Yaponiya", "Japan"]),
    ("🇨🇳 Xitoy", "cn", ["Xitoy", "China", "Kitay"]),
]

FILTER_LANGS = [
    ("🇺🇿 O'zbek", "uz", ["O'zbek", "Ozbek", "Uzbek"]),
    ("🇷🇺 Rus", "ru", ["Rus tilida", "Ruscha", "Russian"]),
    ("🇬🇧 Ingliz", "en", ["Ingliz", "English"]),
    ("🇹🇷 Turk", "tr", ["Turk tilida", "Turkcha"]),
    ("🇰🇷 Koreys", "kr", ["Koreys", "Korean"]),
    ("🇮🇳 Hind", "hi", ["Hind tilida", "Hindcha", "Hindi"]),
]


def filter_menu_text():
    return "🗂 <b>Qaysi mezon bo'yicha izlayapsiz?</b>"


def filter_menu_key():
    key = InlineKeyboardMarkup()
    key.row(
        InlineKeyboardButton(text="🎭 Janr", callback_data="flt_cat_genre"),
        InlineKeyboardButton(text="📅 Yil", callback_data="flt_cat_year"),
    )
    key.row(
        InlineKeyboardButton(text="🌍 Davlat", callback_data="flt_cat_country"),
        InlineKeyboardButton(text="🎤 Til", callback_data="flt_cat_lang"),
    )
    key.add(InlineKeyboardButton(text="🔙 Ortga", callback_data="sq_menu"))
    return key


def _two_col_key(items, prefix, back="sq_filter"):
    key = InlineKeyboardMarkup()
    row = []
    for text, cid, _terms in items:
        row.append(InlineKeyboardButton(text=text, callback_data=f"{prefix}{cid}"))
        if len(row) == 2:
            key.row(*row)
            row = []
    if row:
        key.row(*row)
    key.add(InlineKeyboardButton(text="🔙 Ortga", callback_data=back))
    return key


def filter_genre_key():
    return _two_col_key(FILTER_GENRES, "flt_g_")


def filter_country_key():
    return _two_col_key(FILTER_COUNTRIES, "flt_c_")


def filter_lang_key():
    return _two_col_key(FILTER_LANGS, "flt_l_")


def filter_year_key():
    key = InlineKeyboardMarkup()
    # Joriy yildan boshlab oxirgi 6 yil (avval 2020–2025 qattiq yozilgan edi)
    this_year = time.localtime().tm_year
    years = [str(y) for y in range(this_year, this_year - 6, -1)]
    for i in range(0, len(years), 2):
        key.row(*[InlineKeyboardButton(text=y, callback_data=f"flt_y_{y}") for y in years[i:i + 2]])
    key.add(InlineKeyboardButton(text="✍️ Boshqa", callback_data="flt_other"))
    key.add(InlineKeyboardButton(text="🔙 Ortga", callback_data="sq_filter"))
    return key


def filter_lookup(items, key):
    for text, cid, terms in items:
        if cid == key:
            return text, terms
    return None, None


def vip_text():
    return (
        "👑 <b>VIP</b>\n\n"
        "VIP bilan kanallarga obuna bo'lmasdan, cheklovlarsiz foydalaning:\n"
        "- Reklamasiz tomosha\n"
        "- Tezkor qidiruv\n"
        "- Maxsus tavsiyalar\n\n"
        "<i>Tez orada yoqiladi.</i>"
    )

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise SystemExit("BOT_TOKEN topilmadi: .env fayliga BOT_TOKEN=... yozing (README.md ga qarang)")
_admin_id = os.environ.get("ADMIN_ID", "").strip()
if not _admin_id.isdigit():
    raise SystemExit("ADMIN_ID topilmadi yoki noto'g'ri: .env fayliga ADMIN_ID=<Telegram ID raqamingiz> yozing (README.md ga qarang)")
ADMIN_ID = int(_admin_id)
bot = telebot.TeleBot(BOT_TOKEN, parse_mode='html')

back = InlineKeyboardMarkup().add(InlineKeyboardButton(text="🔙 Exit",callback_data='back'))


def add_new_admin(msg):
    if (msg.text or "").isdigit():
        try:
            cursor.execute("INSERT INTO admins(chat_id) VALUES(?)", (int(msg.text),))
            conn.commit()
            bot.reply_to(msg,f"<b>✅ Yangi {msg.text} qo'shildi!</b>",reply_markup=back)
        except:
            bot.reply_to(msg,f"<b>❌ oldindan mavjud bo'lgan!</b>",reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton(text="➕ Qo'shish",callback_data='new-admin')).add(InlineKeyboardButton(text="🔙 Exit",callback_data='back')))

    else:
        bot.reply_to(msg,f"<b>❌ admin qo'shishda xatolik!</b>",reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton(text="➕ Qo'shish",callback_data='new-admin')).add(InlineKeyboardButton(text="🔙 Exit",callback_data='back')))
def add_new_chat(msg):
    if "@" in (msg.text or ""):
        try:
            cursor.execute("INSERT INTO chats(username) VALUES(?)", (msg.text.strip(),))
            conn.commit()
            bot.reply_to(msg,f"<b>✅ Yangi {msg.text} qo'shildi!</b>",reply_markup=back)
        except:
            bot.reply_to(msg,f"<b>❌ oldindan mavjud bo'lgan kanal!</b>",reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton(text="➕ Qo'shish",callback_data='new-chat')).add(InlineKeyboardButton(text="🔙 Exit",callback_data='back')))
    else:
        bot.reply_to(msg,f"<b>❌ kanal qo'shishda xatolik!</b>",reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton(text="➕ Qo'shish",callback_data='new-chat')).add(InlineKeyboardButton(text="🔙 Exit",callback_data='back')))

def get_admin(cid):
    admin = cursor.execute("SELECT chat_id FROM admins WHERE chat_id=?", (cid,)).fetchone()
    if ADMIN_ID==cid or admin is not None:
        return True
    else:
        return False


# --- Top filmlar va saqlanganlar ---

def is_top(code):
    return cursor.execute("SELECT 1 FROM top_films WHERE video_id=?", (int(code),)).fetchone() is not None


def set_top(code, on, admin_id=None):
    """Kinoni Top ro'yxatiga qo'shadi/olib tashlaydi. Kino bazada bo'lmasa False."""
    code = int(code)
    if on:
        if get_film(code) is None:
            return False
        cursor.execute(
            "INSERT OR IGNORE INTO top_films(video_id, added_by, added_at) VALUES(?, ?, ?)",
            (code, admin_id, time.time_ns()),
        )
    else:
        cursor.execute("DELETE FROM top_films WHERE video_id=?", (code,))
    return True


def top_film_rows():
    return cursor.execute(
        "SELECT v.* FROM top_films t JOIN video v ON v.id = t.video_id ORDER BY t.added_at DESC, t.video_id DESC"
    ).fetchall()


def is_saved(cid, code):
    return cursor.execute(
        "SELECT 1 FROM saved WHERE chat_id=? AND video_id=?", (cid, int(code))
    ).fetchone() is not None


def set_saved(cid, code, on):
    """Kinoni foydalanuvchining saqlanganlariga qo'shadi/olib tashlaydi. Kino bo'lmasa False."""
    code = int(code)
    if on:
        if get_film(code) is None:
            return False
        cursor.execute("INSERT OR IGNORE INTO saved(chat_id, video_id) VALUES(?, ?)", (cid, code))
    else:
        cursor.execute("DELETE FROM saved WHERE chat_id=? AND video_id=?", (cid, code))
    return True


def saved_film_rows(cid):
    return cursor.execute(
        "SELECT v.* FROM saved s JOIN video v ON v.id = s.video_id WHERE s.chat_id=? ORDER BY s.id DESC",
        (cid,),
    ).fetchall()


def top_admin_text():
    rows = top_film_rows()
    if not rows:
        return "🏆 <b>Top filmlar</b>\n\nRo'yxat bo'sh. «➕ Qo'shish» orqali kino kodlarini kiriting."
    lines = [f"🏆 <b>Top filmlar</b> — {len(rows)} ta\n"]
    for i, row in enumerate(rows[:90], 1):
        lines.append(f"{i}. <code>{row[0]}</code> — {h(film_title(row))}")
    lines.append("\nOlib tashlash uchun kinoni bosing.")
    return "\n".join(lines)


def top_admin_key():
    key = InlineKeyboardMarkup()
    for row in top_film_rows()[:90]:
        key.add(InlineKeyboardButton(text=f"❌ {row[0]} • {film_title(row)}"[:60], callback_data=f"topdel-{row[0]}"))
    key.row(
        InlineKeyboardButton(text="➕ Qo'shish", callback_data="topadd"),
        InlineKeyboardButton(text="🔙 Ortga", callback_data="back"),
    )
    return key


def parse_codes(text):
    return [int(c) for c in re.findall(r"\d+", text or "")]


def add_top_step(msg):
    codes = parse_codes(msg.text)
    if not codes:
        bot.reply_to(msg, "<b>❌ Kino kodi topilmadi. Bekor qilindi.</b>", reply_markup=top_admin_key())
        return
    added, missing = [], []
    for code in dict.fromkeys(codes):
        (added if set_top(code, True, msg.chat.id) else missing).append(str(code))
    text = ""
    if added:
        text += f"✅ Top'ga qo'shildi: <code>{', '.join(added)}</code>\n"
    if missing:
        text += f"❌ Bazada yo'q: <code>{', '.join(missing)}</code>\n"
    bot.reply_to(msg, f"<b>{text}</b>\n" + top_admin_text(), reply_markup=top_admin_key())


def admin_film_top_key(code):
    on = is_top(code)
    return InlineKeyboardButton(
        text="🏆 Top'dan olib tashlash" if on else "🏆 Top filmlarga qo'shish",
        callback_data=f"topunset-{code}" if on else f"topset-{code}",
    )


_me = {}


def bot_username():
    # get_me() har chaqiruvda Telegram'ga so'rov — bir marta olib, keshlaymiz
    if "username" not in _me:
        _me["username"] = bot.get_me().username
    return _me["username"]


def video_key(code, saved=False, admin=False):
    uname = bot_username()
    key = InlineKeyboardMarkup()
    key.row(
        InlineKeyboardButton(text="🖥 Full HD (1080p)", callback_data=f"hd-{code}"),
        InlineKeyboardButton(text="💬 Sharhlar", callback_data=f"cm-{code}"),
    )
    key.row(
        InlineKeyboardButton(text="ℹ️ Film haqida", callback_data=f"ab-{code}"),
        InlineKeyboardButton(text="✅ Saqlangan", callback_data=f"unsave-{code}")
        if saved
        else InlineKeyboardButton(text="🔖 Saqlash", callback_data=f"save-{code}"),
    )
    key.row(
        InlineKeyboardButton(text="👥 Aktyorlar", callback_data=f"ac-{code}"),
        InlineKeyboardButton(text="▶️ Treyler", callback_data=f"tr-{code}"),
    )
    key.row(
        InlineKeyboardButton(text="👑 VIP", callback_data="vip"),
        InlineKeyboardButton(
            text="🔗 Ulashish",
            url=f"https://t.me/share/url?url=t.me/{uname}?start={code}&text=Eng so'ngi kinolar to'plami!",
        ),
    )
    if admin:
        key.add(admin_film_top_key(code))
    key.add(InlineKeyboardButton(text="🏠 Bosh menyu", callback_data="home"))
    return key


def can_watch(cid):
    """Kirish nazorati: vip.py yuklanganda o'zining tekshiruvi bilan almashtiriladi."""
    return True


def send_film(cid, code, protect=True):
    check = get_film(code)
    if check is None:
        return False
    if not can_watch(cid):
        return True  # kino bor, lekin VIP talab qilinadi — taklif yuborildi
    try:
        down = int(check[3] or 0) + 1
        info = parse_film((check[0], check[1], check[2], down))
        bot.send_video(
            cid,
            check[1],
            caption=format_film_caption(info),
            reply_markup=video_key(check[0], saved=is_saved(cid, check[0]), admin=get_admin(cid)),
            protect_content=protect,
        )
        cursor.execute("UPDATE video SET down = COALESCE(down, 0) + 1 WHERE id = ?", (check[0],))
        conn.commit()
        return True
    except Exception as e:
        print(e)
        return False

def add_new_kino_caption(msg):
    if msg.text:
        draft = BASE.get(msg.chat.id, {})
        code = draft.get('code')
        file_id = draft.get('file_id')
        if not code or not file_id:
            bot.send_message(msg.chat.id, "<b>❌ Bekor qilindi! Qaytadan boshlang.</b>", reply_markup=back)
            return
        # Bazadagi mavjud yozuvlar bilan bir xil format: apostrof "||" sifatida saqlanadi
        caption = str(msg.text).replace("'", "||")
        try:
            cursor.execute("INSERT INTO video(id, file_id, caption, down) VALUES(?, ?, ?, 0)", (int(code), file_id, caption))
            BASE.pop(msg.chat.id, None)
            info = parse_film((code, file_id, caption, 0))
            b = bot.send_video(
                msg.chat.id,
                file_id,
                caption=format_film_caption(info),
                reply_markup=video_key(code),
                protect_content=True,
            ).message_id
            bot.send_message(
                msg.chat.id,
                f"<b>✅ Kino saqlandi! Kino kodi: {code}</b>",
                reply_to_message_id=b,
                reply_markup=InlineKeyboardMarkup()
                .add(admin_film_top_key(code))
                .add(InlineKeyboardButton(text="🔙 Admin panel", callback_data="back")),
            )
        except Exception as e:
            bot.send_message(msg.chat.id, f"<b>❌ {e}</b>", reply_markup=back)
    else:
        bot.send_message(msg.chat.id, "<b>❌ Bekor qilindi!</b>", reply_markup=back)
def code_in_use(code):
    """Kod kino yoki serialga band qilinganmi (foydalanuvchi bitta kod bilan ikkalasini ham ochadi)."""
    if get_film(code) is not None:
        return True
    try:
        return cursor.execute("SELECT 1 FROM series WHERE id=?", (int(code),)).fetchone() is not None
    except sqlite3.OperationalError:
        return False


def add_new_kino_code(msg):
    text = msg.text or ""
    if text.isdigit() and code_in_use(text):
        bot.send_message(msg.chat.id, f"<b>❌ {text} kodi band (kino yoki serial). Bekor qilindi!</b>", reply_markup=back)
        return
    if text.isdigit():
        BASE.setdefault(msg.chat.id, {})['code'] = text
        a = bot.reply_to(msg,"<b>🔖 Kino tafsifini kiriting: %stat | %code</b>")
        bot.register_next_step_handler(a,add_new_kino_caption)
    else:
        bot.send_message(msg.chat.id,"<b>❌ Bekor qilindi!</b>",reply_markup=back)
def add_new_kino(msg):
    if msg.video:
        file_id = msg.video.file_id
        BASE[msg.chat.id] = {'file_id': file_id}
        a = bot.reply_to(msg, "<b>Kino kodini kiriting: 12</b>")
        bot.register_next_step_handler(a, add_new_kino_code)
    else:
        bot.send_message(msg.chat.id, "<b>❌ Bekor qilindi!</b>", reply_markup=back)

def delete_kino(msg):
    if (msg.text or "").isdigit():
        code = msg.text
        try:
            cursor.execute("DELETE FROM video WHERE id=?", (code,))
            cursor.execute("DELETE FROM top_films WHERE video_id=?", (code,))
            cursor.execute("DELETE FROM saved WHERE video_id=?", (code,))
            conn.commit()
            bot.reply_to(msg, f"<b>✅ Kino {code} muvaffaqiyatli o'chirildi!</b>", reply_markup=back)
        except Exception as e:
            bot.reply_to(msg, f"<b>❌ Xato yuz berdi: {e}</b>", reply_markup=back)
    else:
        bot.reply_to(msg, f"<b>❌ Bekor qilindi!</b>", reply_markup=back)

def join_chat_key(next_cb="check_search"):
    channels = cursor.execute("SELECT * FROM chats").fetchall()
    key = InlineKeyboardMarkup()
    key.add(InlineKeyboardButton(text="👑 VIP", callback_data="vip"))
    row = []
    for item in channels:
        username = str(item[1]).replace("@", "")
        row.append(InlineKeyboardButton(text="🔒 Obuna bo'lish", url=f"https://t.me/{username}"))
        if len(row) == 2:
            key.row(*row)
            row = []
    if row:
        key.row(*row)
    key.add(InlineKeyboardButton(text="✅ Tekshirish", callback_data=next_cb))
    return key


def is_vip_user(cid):
    """vip.py yuklanganda haqiqiy tekshiruv bilan almashtiriladi."""
    return False


_joined_cache = {}
JOINED_CACHE_TTL = 120  # obuna tasdiqlangan userni shuncha soniya qayta tekshirmaymiz


def is_joined(user_id):
    # VIP obunachilar kanallarga obuna bo'lishi shart emas (subscribe_text'da va'da qilingan)
    if is_vip_user(user_id):
        return True
    channels = cursor.execute("SELECT * FROM chats").fetchall()
    if len(channels) == 0:
        return True
    if time.time() - _joined_cache.get(user_id, 0) < JOINED_CACHE_TTL:
        return True
    ok = ["member", "creator", "administrator"]
    for item in channels:
        try:
            member = bot.get_chat_member(item[1], user_id)
        except Exception as e:
            # Kanal noto'g'ri sozlangan (bot admin emas / username xato) — hammani bloklamaslik uchun o'tkazib yuboramiz
            print(f"kanal {item[1]} tekshirib bo'lmadi:", e)
            continue
        joined = member.status in ok or (member.status == "restricted" and getattr(member, "is_member", False))
        if not joined:
            _joined_cache.pop(user_id, None)
            return False
    _joined_cache[user_id] = time.time()
    return True


def join(user_id, next_cb="check_search"):
    if is_joined(user_id):
        return True
    bot.send_message(user_id, subscribe_text(), parse_mode="html", reply_markup=join_chat_key(next_cb))
    return False

def server():
    st = speedtest.Speedtest()
    st.config['user_agent'] = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3'
    download_speed = st.download() / 10**6
    upload_speed = st.upload() / 10**6
    server = st.get_best_server()
    ping = st.results.ping
    txt = f"<b>🚀 Speed Test Result:\n\n✘ Download Speed: {download_speed:.2f} Mbps\n✘ Upload Speed: {upload_speed:.2f} Mbps\n✘ Ping: {ping} ms</b>"
    return txt
def admin_menu():
    key = InlineKeyboardMarkup().add(
        InlineKeyboardButton(text="✍️ Oddiy xabar", callback_data='send_post'),
        InlineKeyboardButton(text="📨 Forward xabar", callback_data='send_for'),
    ).add(
        InlineKeyboardButton(text="📊 Statistika", callback_data='static'),
        InlineKeyboardButton(text="🚀 Server", callback_data='server')
    ).add(
        InlineKeyboardButton(text="📋 Adminlar", callback_data='list_admin'),
        InlineKeyboardButton(text="🔉 Kanallar", callback_data='list_chat'),
    ).add(
        InlineKeyboardButton(text="➕ Kino qo'shish", callback_data='add_new_kino'),
        InlineKeyboardButton(text="➖ Kino o'chirish", callback_data='delete_kino')
    ).add(
        InlineKeyboardButton(text="🏆 Top filmlar", callback_data='top_admin'),
        InlineKeyboardButton(text="📺 Seriallar", callback_data='sr_admin'),
    ).add(
        InlineKeyboardButton(text="💎 VIP obuna", callback_data='va_menu'),
        InlineKeyboardButton(text="🎞 Reels", callback_data='rl_menu'),
    ).add(
        InlineKeyboardButton(text="🔙 Chiqish", callback_data='admin_exit'),
    )
    return key



BROADCAST_RATE = 20          # sekundiga xabar (Telegram limiti ~30)
_broadcast_lock = threading.Lock()


def _deliver(send, user_id):
    """Bitta userga yuboradi. 429 bo'lsa kutib qayta urinadi. 'ok' / 'blocked' / 'failed'."""
    for _ in range(3):
        try:
            send(user_id)
            return "ok"
        except telebot.apihelper.ApiTelegramException as e:
            if e.error_code == 429:
                time.sleep(int((e.result_json or {}).get("parameters", {}).get("retry_after", 5)) + 1)
                continue
            return "blocked" if e.error_code == 403 else "failed"
        except Exception:
            return "failed"
    return "failed"


def broadcast(msg, send, title):
    """Barcha userlarga fon oqimida, tezlik limiti bilan yuboradi va adminga hisobot beradi."""
    if not _broadcast_lock.acquire(blocking=False):
        bot.reply_to(msg, "<b>⏳ Boshqa xabar yuborilmoqda. Tugashini kuting.</b>", reply_markup=back)
        return
    users = [r[0] for r in cursor.execute("SELECT chat_id FROM users").fetchall()]
    bot.reply_to(msg, f"<b>🚀 {title} {len(users)} ta userga yuborilmoqda...</b>\nTugagach hisobot keladi.")

    def run():
        stats = {"ok": 0, "blocked": 0, "failed": 0}
        started = time.time()
        try:
            for i, uid in enumerate(users):
                stats[_deliver(send, uid)] += 1
                time.sleep(1 / BROADCAST_RATE)
        finally:
            _broadcast_lock.release()
        bot.send_message(
            msg.chat.id,
            f"<b>✅ {title} yuborildi!</b>\n\n"
            f"📨 Yetkazildi: {stats['ok']}\n"
            f"🚫 Botni bloklagan: {stats['blocked']}\n"
            f"⚠️ Xato: {stats['failed']}\n"
            f"⏱ Vaqt: {int(time.time() - started) // 60} daqiqa",
            reply_markup=back,
        )

    threading.Thread(target=run, name="broadcast", daemon=True).start()


def send_post(msg):
    # copy_message: admin yozgan formatlash saqlanadi va matndagi "<", "&" HTML xatosiga olib kelmaydi
    broadcast(msg, lambda uid: bot.copy_message(uid, msg.chat.id, msg.message_id), "Xabar")


def send_for(msg):
    broadcast(msg, lambda uid: bot.forward_message(uid, msg.chat.id, msg.message_id), "Forward xabar")
