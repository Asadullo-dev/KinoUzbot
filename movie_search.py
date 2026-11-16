"""Inline kino qidiruvi: mavjud database.db dan faqat o'qiydi.

Bazadagi `video` jadvali: id (kino kodi), file_id (Telegram video), caption (matn), down (ko'rishlar).
Nom, davlat, yil va IMDb caption matnidan `parse_film` orqali ajratiladi.
"""
import html
import pathlib
import re
import sqlite3

from telebot.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQueryResultArticle,
    InlineQueryResultCachedVideo,
    InputTextMessageContent,
)

from config import DB_PATH, lookup_rating, parse_film

PAGE_SIZE = 20          # bitta sahifadagi natijalar (Telegram limiti 50)
SEARCH_CAP = 200        # nom bo'yicha qidiruvda ko'rib chiqiladigan maksimal yozuvlar
TOP_QUERIES = ("top", "top:rating", "rating", "reyting")
SAVED_QUERIES = ("saqlangan", "saved")
COLUMNS = "id, file_id, caption, down"

_APOSTROPHES = str.maketrans({c: "'" for c in "ʻʼ‘’`´"})


def norm(text):
    """Katta-kichik harf, apostrof turlari va ortiqcha bo'sh joylarni bir xillashtiradi."""
    text = (text or "").translate(_APOSTROPHES).casefold()
    return re.sub(r"\s+", " ", text).strip()


def _connect():
    # mode=ro: fayl bo'lmasa yangi baza yaratilmaydi, yozish ham mumkin emas
    uri = pathlib.Path(DB_PATH).as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5)
    conn.create_function("norm", 1, norm, deterministic=True)
    return conn


def _query(sql, params=()):
    conn = _connect()
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def top_rows(offset, limit):
    return _query(
        f"SELECT {COLUMNS} FROM video ORDER BY down DESC, id DESC LIMIT ? OFFSET ?",
        (limit, offset),
    )


def curated_top_rows(offset, limit):
    """Admin panel orqali tanlangan Top filmlar (top_films jadvali)."""
    return _query(
        "SELECT v.id, v.file_id, v.caption, v.down FROM top_films t JOIN video v ON v.id = t.video_id "
        "ORDER BY t.added_at DESC, t.video_id DESC LIMIT ? OFFSET ?",
        (limit, offset),
    )


def saved_rows(user_id, offset, limit):
    return _query(
        f"SELECT v.id, v.file_id, v.caption, v.down FROM saved s JOIN video v ON v.id = s.video_id "
        "WHERE s.chat_id = ? ORDER BY s.id DESC LIMIT ? OFFSET ?",
        (user_id, limit, offset),
    )


def _rank(row, q):
    title = norm(parse_film(row)["title"])
    if title == q:
        return 0
    if title.startswith(q):
        return 1
    if q in title:
        return 2
    if all(w in title for w in q.split()):
        return 3
    return 4  # faqat caption'ning boshqa joyida (janr, aktyor...) topilgan


def _like_escape(word):
    return word.replace("!", "!!").replace("%", "!%").replace("_", "!_")


def search_rows(text):
    q = norm(text)
    rows = []
    if q.isdigit():
        rows += _query(f"SELECT {COLUMNS} FROM video WHERE id = ?", (int(q),))
    words = q.split()
    where = " AND ".join("norm(caption) LIKE ? ESCAPE '!'" for _ in words)
    params = [f"%{_like_escape(w)}%" for w in words]
    found = _query(
        f"SELECT {COLUMNS} FROM video WHERE {where} ORDER BY down DESC LIMIT ?",
        (*params, SEARCH_CAP),
    )
    seen = {r[0] for r in rows}
    found = [r for r in found if r[0] not in seen]
    found.sort(key=lambda r: (_rank(r, q), -int(r[3] or 0)))
    return rows + found


def rows_for_query(text, user_id, offset, limit=PAGE_SIZE):
    """(sahifadagi qatorlar, keyingi offset) qaytaradi."""
    low = norm(text)
    if low in SAVED_QUERIES:
        page = saved_rows(user_id, offset, limit + 1)
    elif low in TOP_QUERIES:
        page = curated_top_rows(offset, limit + 1)
    elif not low:
        page = top_rows(offset, limit + 1)
    else:
        matches = search_rows(text)
        page = matches[offset:offset + limit + 1]
    next_offset = str(offset + limit) if len(page) > limit else ""
    return page[:limit], next_offset


def film_rating(info):
    if info.get("imdb"):
        try:
            return float(str(info["imdb"]).split("/")[0].replace(",", "."))
        except ValueError:
            pass
    entry = lookup_rating(info["title"])
    return entry["rating"] if entry else None


def film_description(info):
    parts = []
    if info.get("country"):
        parts.append(f"🌍 {info['country']}")
    rating = film_rating(info)
    if rating is not None:
        parts.append(f"⭐ {rating:.1f}")
    parts.append(f"👁 {int(info['down'] or 0)}")
    return " · ".join(parts)


def card_markup(code, bot_username, saved=False):
    markup = InlineKeyboardMarkup()
    # Deep link mavjud /start <kod> oqimiga olib boradi (obuna tekshiruvi bilan)
    markup.add(InlineKeyboardButton(text="🎬 Kinoni ko'rish", url=f"https://t.me/{bot_username}?start={code}"))
    if saved:
        markup.add(InlineKeyboardButton(text="🗑 Saqlanganlardan olib tashlash", callback_data=f"unsave-{code}"))
    else:
        markup.add(InlineKeyboardButton(text="⭐ Saqlanganlarga qo'shish", callback_data=f"save-{code}"))
    return markup


def film_result(row, bot_username, as_video=True, saved=False):
    info = parse_film(row)
    code = row[0]
    title = info["title"] or f"Kod {code}"
    description = film_description(info)
    lines = [f"🎬 <b>{html.escape(title)}</b>", html.escape(description)]
    if info.get("year"):
        lines.append(f"🗓 Yili: {html.escape(str(info['year']))}")
    lines.append(f"\n🆔 Kino kodi: <code>{code}</code>")
    markup = card_markup(code, bot_username, saved=saved)
    content = InputTextMessageContent("\n".join(lines), parse_mode="HTML")
    if as_video and row[1]:
        # file_id video fayliga tegishli; Telegram uning muqovasini poster sifatida ko'rsatadi
        return InlineQueryResultCachedVideo(
            id=str(code),
            video_file_id=row[1],
            title=title[:64],
            description=description,
            input_message_content=content,
            reply_markup=markup,
        )
    return InlineQueryResultArticle(
        id=str(code),
        title=title[:64],
        description=description,
        input_message_content=content,
        reply_markup=markup,
    )


# --- Inline'da poster sifatida ishlamaydigan videolarni eslab qolish ---
# Telegram ba'zi video file_id larni (masalan mp4 bo'lmaganlarini) VIDEO_CONTENT_TYPE_INVALID bilan rad etadi.
# Natija sahifadagi bitta yaroqsiz video butun javobni yiqitadi, shuning uchun ularni aniqlab, posterlarsiz ko'rsatamiz.
# Holat database.db ga emas, alohida JSON faylga yoziladi.
import json
import os
import threading

MEDIA_STATE_PATH = os.path.join(os.path.dirname(DB_PATH), "inline_media.json")
_media_lock = threading.Lock()


def _load_media_state():
    try:
        with open(MEDIA_STATE_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return {"good": set(data.get("good", [])), "bad": set(data.get("bad", []))}
    except (OSError, ValueError):
        return {"good": set(), "bad": set()}


_media = _load_media_state()


def _save_media_state():
    tmp = MEDIA_STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"good": sorted(_media["good"]), "bad": sorted(_media["bad"])}, f)
    os.replace(tmp, MEDIA_STATE_PATH)


def mark_media(good=(), bad=()):
    with _media_lock:
        _media["good"].update(good)
        _media["bad"].update(bad)
        _media["good"].difference_update(_media["bad"])
        _save_media_state()


def media_status(code):
    if code in _media["bad"]:
        return "bad"
    return "good" if code in _media["good"] else "unknown"


SYSTEMIC_BAD_LIMIT = 5


def posters_disabled():
    # Birorta ham video ishlamagan, lekin bir nechtasi rad etilgan — Telegram videolarni umuman qabul qilmayapti
    return not _media["good"] and len(_media["bad"]) >= SYSTEMIC_BAD_LIMIT


def is_video_type_error(exc):
    return "VIDEO_CONTENT_TYPE_INVALID" in str(exc)


def film_items(rows, bot_username, saved=False):
    """Kino qatorlarini answer_with_items uchun (kalit, video_bormi, build) ko'rinishiga keltiradi."""
    return [
        (r[0], bool(r[1]), lambda as_video, r=r: film_result(r, bot_username, as_video=as_video, saved=saved))
        for r in rows
    ]


def answer_with_posters(answer, rows, bot_username, max_attempts=12, saved=False):
    return answer_with_items(answer, film_items(rows, bot_username, saved), max_attempts)


def answer_with_items(answer, items, max_attempts=12):
    """Javobni iloji boricha ko'p poster bilan yuboradi.

    items: [(kalit, video_bormi, build(as_video) -> InlineQueryResult)]
    answer(results) — Telegram'ga yuboradi (muvaffaqiyatsiz bo'lsa exception).
    Telegram xatosi javobni "ishlatmaydi", shuning uchun yaroqsiz videoni ikkiga bo'lib qidirish mumkin.
    """
    def build(video_keys):
        return [make(key in video_keys) for key, _has, make in items]

    keys = [key for key, has_video, _ in items if has_video]
    if posters_disabled():
        answer(build(set()))
        return 0
    good = {k for k in keys if media_status(k) == "good"}
    pending = [k for k in keys if media_status(k) == "unknown"]
    suspects = pending

    for _ in range(max_attempts):
        trial = good | set(suspects)
        try:
            answer(build(trial))
            mark_media(good=trial)
            return len(trial)
        except Exception as e:
            if not is_video_type_error(e):
                raise
            if not suspects:
                # "good" deb hisoblangan videolar ham rad etildi — ularga bu safar ishonmaymiz
                good = set()
                break
            if len(suspects) == 1:
                mark_media(bad=suspects)
                pending = [k for k in pending if k != suspects[0]]
                suspects = pending
            else:
                suspects = suspects[: len(suspects) // 2]
    try:
        answer(build(good))
        return len(good)
    except Exception as e:
        if not is_video_type_error(e):
            raise
        answer(build(set()))
        return 0


# --- Seriallar ---
SERIES_QUERIES = ("seriallar", "serial", "series")
SERIES_COLUMNS = (
    "s.id, s.title, s.description, s.views, COUNT(e.id), "
    "(SELECT file_id FROM series_episodes f WHERE f.series_id = s.id AND f.kind = 'video' ORDER BY f.number LIMIT 1)"
)


def series_list_rows(offset, limit):
    return _query(
        f"SELECT {SERIES_COLUMNS} FROM series s LEFT JOIN series_episodes e ON e.series_id = s.id "
        "GROUP BY s.id ORDER BY s.created_at DESC, s.id DESC LIMIT ? OFFSET ?",
        (limit, offset),
    )


def search_series(text, limit=5):
    words = norm(text).split()
    if not words:
        return []
    where = " AND ".join("norm(s.title) LIKE ? ESCAPE '!'" for _ in words)
    rows = _query(
        f"SELECT {SERIES_COLUMNS} FROM series s LEFT JOIN series_episodes e ON e.series_id = s.id "
        f"WHERE {where} GROUP BY s.id ORDER BY s.views DESC LIMIT ?",
        (*[f"%{_like_escape(w)}%" for w in words], limit),
    )
    q = norm(text)
    code_match = _query(
        f"SELECT {SERIES_COLUMNS} FROM series s LEFT JOIN series_episodes e ON e.series_id = s.id "
        "WHERE s.id = ? GROUP BY s.id",
        (int(q),),
    ) if q.isdigit() else []
    return code_match + [r for r in rows if not code_match or r[0] != code_match[0][0]]


def series_result(row, bot_username, as_video=True):
    sid, title, description, views, count, first_video = row
    desc_line = f"📺 Serial · 🎞 {count} qism · 👁 {int(views or 0)}"
    lines = [f"📺 <b>{html.escape(title)}</b>", f"🎞 Qismlar: {count} ta · 👁 {int(views or 0)}"]
    if description:
        lines.append(f"\n{html.escape(description[:600])}")
    lines.append(f"\n🆔 Serial kodi: <code>{sid}</code>")
    markup = InlineKeyboardMarkup().add(
        InlineKeyboardButton(text="📺 Qismlarni ko'rish", url=f"https://t.me/{bot_username}?start={sid}")
    )
    content = InputTextMessageContent("\n".join(lines), parse_mode="HTML")
    if as_video and first_video:
        return InlineQueryResultCachedVideo(
            id=f"s{sid}", video_file_id=first_video, title=title[:64], description=desc_line,
            input_message_content=content, reply_markup=markup,
        )
    return InlineQueryResultArticle(
        id=f"s{sid}", title=title[:64], description=desc_line,
        input_message_content=content, reply_markup=markup,
    )


def series_items(rows, bot_username):
    # Seriallar media kaliti manfiy: kino kodlari bilan aralashmasin
    return [(-r[0], bool(r[5]), lambda as_video, r=r: series_result(r, bot_username, as_video)) for r in rows]


def items_for_query(text, user_id, offset, bot_username, limit=PAGE_SIZE):
    """Inline javob uchun (items, keyingi offset): kinolar va seriallar."""
    low = norm(text)
    if low in SERIES_QUERIES:
        page = series_list_rows(offset, limit + 1)
        next_offset = str(offset + limit) if len(page) > limit else ""
        return series_items(page[:limit], bot_username), next_offset
    rows, next_offset = rows_for_query(text, user_id, offset, limit)
    items = film_items(rows, bot_username, saved=low in SAVED_QUERIES)
    if offset == 0 and low and low not in SAVED_QUERIES + TOP_QUERIES:
        items = series_items(search_series(text), bot_username) + items
    return items, next_offset
