import html
import re
import unicodedata

from telebot.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQueryResultArticle,
    InputTextMessageContent,
)

GENERIC_GENRE = {
    "ajoyib",
    "qiziqarli",
    "qiziqarli komedik",
    "tarjima kino",
    "tarjima",
    "kino",
    "multikino",
    "uzbek kino",
    "jaxon kino",
}

GENRE_MAP = {
    "drama": "Drama",
    "dramma": "Drama",
    "kriminal": "Kriminal",
    "crime": "Kriminal",
    "jangari": "Jangari",
    "action": "Jangari",
    "aksiya": "Ekshn",
    "ekshn": "Ekshn",
    "sarguzasht": "Sarguzasht",
    "adventure": "Sarguzasht",
    "fentezi": "Fentezi",
    "fantasy": "Fentezi",
    "fantastika": "Fantastika",
    "fantastic": "Fantastika",
    "komediya": "Komediya",
    "comedy": "Komediya",
    "komedik": "Komediya",
    "triller": "Triller",
    "thriller": "Triller",
    "treller": "Triller",
    "melodrama": "Melodrama",
    "ramantika": "Melodrama",
    "romantika": "Melodrama",
    "multfilm": "Multfilm",
    "detektiv": "Detektiv",
    "horror": "Qo'rqinchli",
    "qorqinchli": "Qo'rqinchli",
}

# rating, year, country, genres, aliases (longest / most specific first in lookup)
RATING_CATALOG = [
    (9.3, 1994, "AQSH", ["Drama", "Kriminal"], ["shoushenkdan qochish", "shawshank redemption", "shawshank"]),
    (9.2, 1972, "AQSH", ["Drama", "Kriminal"], ["choqintirgan ota", "the godfather", "godfather"]),
    (9.0, 1974, "AQSH", ["Drama", "Kriminal"], ["choqintirgan ota 2", "godfather 2"]),
    (9.1, 2008, "AQSH", ["Jangari", "Ekshn"], ["qora ritsar", "the dark knight", "dark knight"]),
    (9.0, 2003, "AQSH", ["Sarguzasht", "Fentezi"], ["uzuklar hukmdori qirolning qaytishi", "return of the king"]),
    (8.9, 2001, "AQSH", ["Sarguzasht", "Fentezi"], ["uzuklar hukmdori uzuk ittifoqi", "fellowship of the ring"]),
    (8.8, 2002, "AQSH", ["Sarguzasht", "Fentezi"], ["uzuklar hukmdori ikki qala", "the two towers"]),
    (8.8, 1999, "AQSH", ["Drama", "Kriminal"], ["jang klubi", "fight club"]),
    (8.7, 1999, "AQSH", ["Fantastika", "Ekshn"], ["matritsa 1", "the matrix", "matritsa", "matrix"]),
    (8.6, 1995, "AQSH", ["Kriminal", "Triller"], ["yetti seven", "se7en", "seven"]),
    (8.6, 2006, "AQSH", ["Kriminal", "Drama"], ["dexter"]),
    (8.4, 2018, "Livan", ["Drama"], ["kapernaum", "capernaum"]),
    (8.4, 2000, "AQSH", ["Detektiv", "Triller"], ["momento", "memento", "eslab kor momento"]),
    (8.3, 1983, "AQSH", ["Kriminal", "Drama"], ["chandiqli yuz", "chandiq yuz tony montana", "scarface"]),
    (8.2, 2022, "AQSH", ["Jangari", "Drama"], ["topgun meverik 2", "top gun maverick", "topgun meverik"]),
    (8.2, 2019, "Turkiya", ["Drama", "Komediya"], ["7 bolinmadagi mojiza", "yetinchi bolmadagi mojiza", "miracle in cell"]),
    (8.1, 2019, "AQSH", ["Sport", "Drama"], ["ford ferrariga qarshi", "ford ferarriga qarshi", "ford v ferrari"]),
    (8.1, 2009, "AQSH", ["Drama"], ["hattiko", "hachi", "hachiko"]),
    (8.0, 2016, "AQSH", ["Melodrama", "Musiqiy"], ["la la land"]),
    (8.0, 2006, "AQSH", ["Sarguzasht", "Drama"], ["qonli olmos", "blood diamond"]),
    (8.0, 2022, "AQSH", ["Komediya", "Fentezi"], ["wednesday"]),
    (8.0, 2010, "AQSH", ["Drama", "Triller"], ["qora oqqush", "black swan"]),
    (7.9, 2017, "AQSH", ["Fantastika", "Jangari"], ["thor ragnarok"]),
    (7.8, 2007, "AQSH", ["Kriminal", "Drama"], ["amerikalik gangster"]),
    (7.8, 2024, "AQSH", ["Komediya", "Drama"], ["anora"]),
    (7.8, 2004, "AQSH", ["Melodrama"], ["xotira kundaligi", "the notebook"]),
    (7.8, 2005, "Britaniya", ["Melodrama"], ["gurur va andisha", "pride and prejudice"]),
    (7.8, 2010, "AQSH", ["Drama"], ["ijtimoiy tarmoq facebook", "the social network"]),
    (7.8, 2019, "AQSH", ["Drama"], ["kichik xonimlar", "little women"]),
    (7.8, 2006, "AQSH", ["Drama"], ["hayot qadri", "the pursuit of happyness"]),
    (7.8, 1987, "AQSH", ["Jangari"], ["olja xishnik", "predator"]),
    (7.7, 2007, "AQSH", ["Detektiv", "Kriminal"], ["zodiak", "zodiac"]),
    (7.7, 1997, "AQSH", ["Fantastika", "Drama"], ["gattaca"]),
    (7.7, 2017, "AQSH", ["Jangari", "Komediya"], ["kingsman 2"]),
    (7.6, 2008, "AQSH", ["Multfilm", "Komediya"], ["kungfu panda", "kung fu panda"]),
    (7.6, 2001, "AQSH", ["Drama"], ["men sem", "i am sam"]),
    (7.6, 2008, "AQSH", ["Drama"], ["yetti taqdir", "seven pounds"]),
    (7.5, 2004, "AQSH", ["Jangari", "Fantastika"], ["orgimchak odam 2", "spider man 2"]),
    (7.5, 2016, "AQSH", ["Fantastika", "Jangari"], ["doktor strenj", "doctor strange"]),
    (7.5, 2014, "AQSH", ["Jangari"], ["jon uik", "john wick"]),
    (7.4, 2006, "AQSH", ["Sarguzasht"], ["karib dengizi qaroqchilari murdaning sandigi"]),
    (7.4, 2021, "AQSH", ["Jangari", "Fantastika"], ["shang chi"]),
    (7.4, 2017, "AQSH", ["Jangari", "Fantastika"], ["maymunlar sayyorasi 3", "war for the planet of the apes"]),
    (7.3, 2006, "AQSH", ["Multfilm"], ["chaqmoq makvin 1", "cars"]),
    (7.3, 2014, "AQSH", ["Jangari", "Drama"], ["amerikalik snayper", "american sniper"]),
    (7.3, 2025, "AQSH", ["Jangari"], ["balerina jon uik olamidan", "ballerina"]),
    (7.2, 2013, "AQSH", ["Drama"], ["getsbi", "the great gatsby"]),
    (7.2, 2007, "AQSH", ["Fantastika", "Jangari"], ["men afsonaman", "i am legend"]),
    (7.2, 2005, "Yangi Zelandiya", ["Sarguzasht", "Fentezi"], ["king kong"]),
    (7.2, 2011, "AQSH", ["Multfilm"], ["rango"]),
    (7.1, 2003, "Tailand", ["Jangari"], ["ongbak", "ong bak"]),
    (7.1, 2002, "AQSH", ["Drama"], ["ota mehri john q", "john q"]),
    (6.9, 2024, "AQSH", ["Multfilm", "Jangari"], ["sonic 3"]),
    (6.9, 2012, "AQSH", ["Komediya"], ["ted"]),
    (6.9, 2005, "AQSH", ["Fentezi", "Sarguzasht"], ["narniya saltanati", "narnia"]),
    (6.8, 2001, "AQSH", ["Jangari"], ["forsaj 1", "the fast and the furious"]),
    (6.8, 2010, "AQSH", ["Fantastika"], ["tron"]),
    (6.8, 2004, "AQSH", ["Jangari", "Fentezi"], ["hellboy"]),
    (6.7, 2014, "Fransiya", ["Fantastika", "Jangari"], ["lyusi", "lucy", "lusy"]),
    (6.6, 2024, "AQSH", ["Multfilm"], ["moana 2"]),
    (6.6, 2024, "AQSH", ["Multfilm"], ["mufasa"]),
    (6.6, 2017, "AQSH", ["Jangari"], ["forsaj 8"]),
    (6.6, 2023, "AQSH", ["Fantastika"], ["flash"]),
    (6.6, 2015, "AQSH", ["Komediya", "Kriminal"], ["fokus", "focus"]),
    (6.5, 2006, "AQSH", ["Qo'rqinchli"], ["saylent hill", "silent hill"]),
    (6.5, 2019, "AQSH", ["Qo'rqinchli"], ["ono 2", "it chapter two"]),
    (6.4, 2008, "AQSH", ["Fantastika", "Jangari"], ["henkok", "hancock"]),
    (6.4, 2014, "AQSH", ["Fantastika"], ["divergent"]),
    (6.3, 2021, "AQSH", ["Fantastika"], ["abadiylar", "eternals"]),
    (6.2, 2022, "AQSH", ["Fantastika"], ["thor sevgi va chaqmoq", "thor love and thunder"]),
    (6.2, 2010, "AQSH", ["Drama"], ["uoll strit 2", "money never sleeps"]),
    (6.1, 2017, "AQSH", ["Fantastika"], ["adolat ligasi", "justice league"]),
    (6.1, 2018, "AQSH", ["Jangari"], ["rampage"]),
    (6.1, 2023, "AQSH", ["Sport"], ["gran turismo"]),
    (6.0, 2024, "AQSH", ["Fantastika", "Jangari"], ["venom 3"]),
    (6.0, 2006, "AQSH", ["Jangari"], ["forsaj 3"]),
    (6.0, 2023, "AQSH", ["Fantastika"], ["shazam 2"]),
    (5.9, 2016, "AQSH", ["Jangari"], ["buyuk devor", "the great wall"]),
    (5.6, 2018, "AQSH", ["Jangari"], ["megaladon", "the meg"]),
    (5.6, 2025, "AQSH", ["Sarguzasht"], ["minecraft"]),
    (5.4, 1999, "AQSH", ["Sarguzasht"], ["mumiyo", "the mummy"]),
    (5.3, 2008, "AQSH", ["Melodrama", "Fentezi"], ["sumerki 1", "twilight"]),
]

THUMB_URL = "https://cdn-icons-png.flaticon.com/128/2948/2948088.png"


def norm_title(s):
    s = str(s or "").lower()
    for a, b in [
        ("o‘", "o"),
        ("g‘", "g"),
        ("o'", "o"),
        ("g'", "g"),
        ("o`", "o"),
        ("g`", "g"),
        ("o’", "o"),
        ("g’", "g"),
        ("ў", "o"),
        ("ғ", "g"),
    ]:
        s = s.replace(a, b)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"[^a-z0-9]+", " ", s)
    s = re.sub(r"\b(qism|fasl|kino|film|the|a|part)\b", " ", s)
    return " ".join(s.split())


def _catalog_index():
    items = []
    for rating, year, country, genres, aliases in RATING_CATALOG:
        entry = {
            "rating": rating,
            "year": year,
            "country": country,
            "genres": genres,
            "key": norm_title(aliases[0]),
        }
        for alias in aliases:
            n = norm_title(alias)
            if n:
                items.append((n, entry))
    items.sort(key=lambda x: len(x[0]), reverse=True)
    return items


RATING_INDEX = _catalog_index()


def lookup_rating(title):
    n = norm_title(title)
    if not n:
        return None
    for alias, entry in RATING_INDEX:
        if n == alias:
            return entry
    for alias, entry in RATING_INDEX:
        if len(alias) >= 4 and n.startswith(alias + " "):
            return entry
    return None


def parse_film_title(caption):
    cap = str(caption or "").replace("||", "'")
    raw = ""
    m = re.search(r"(?:🎬\s*)?(?:Kino nomi|Nomi)\s*:\s*(.+)", cap, re.I)
    if m:
        raw = m.group(1).strip()
    if not raw or "janri" in raw.lower():
        for line in cap.splitlines():
            line = re.sub(r"^[\W_]+", "", line.strip())
            if not line or "janri" in line.lower() or "http" in line.lower():
                continue
            raw = line
            break
    raw = re.sub(r"https?://\S+", "", raw)
    raw = raw.replace("👇", " ")
    raw = " ".join(raw.split())
    return raw[:80]


def split_genres(text):
    parts = re.split(r"[,/;|#]+|\s+va\s+", str(text))
    out = []
    for part in parts:
        part = " ".join(part.replace("#", " ").split()).strip()
        if not part:
            continue
        key = norm_title(part)
        if key in GENERIC_GENRE:
            continue
        mapped = GENRE_MAP.get(key) or GENRE_MAP.get(key.replace(" ", ""))
        if mapped is None:
            mapped = part[:1].upper() + part[1:] if part else part
        if mapped and mapped not in out:
            out.append(mapped)
    return out[:3]


def parse_caption_meta(caption):
    cap = str(caption or "").replace("||", "'")
    year = None
    m = re.search(r"(?:🗓\s*)?(?:Yili|Yil)\s*:\s*(\d{4})", cap, re.I)
    if m:
        year = int(m.group(1))
    else:
        m = re.search(r"\((19\d{2}|20[0-2]\d)\)", cap)
        if m:
            year = int(m.group(1))
    country = ""
    m = re.search(r"(?:🌐\s*)?(?:Davlati|Davlat)\s*:\s*(.+)", cap, re.I)
    if m:
        country = " ".join(m.group(1).split())[:40]
    genres = []
    m = re.search(r"Janri\s*:\s*(.+)", cap, re.I)
    if m:
        genres = split_genres(m.group(1))
    return year, country, genres


def _meta_line(country, genres):
    parts = []
    if country:
        parts.append(country)
    parts.extend(genres[:2] if country else genres[:3])
    return " | ".join(parts) if parts else "Kino"


def make_rating_card(row, entry):
    title = parse_film_title(row[2]) or f"Kod {row[0]}"
    cap_year, cap_country, cap_genres = parse_caption_meta(row[2])
    year = cap_year or entry["year"]
    country = cap_country or entry["country"]
    genres = cap_genres or entry["genres"]
    views = int(row[3] or 0)
    rating = entry["rating"]
    line1 = f"📅 {year} | ⭐ {rating:.1f} | 👁 {views}"
    line2 = _meta_line(country, genres)
    return {
        "row": row,
        "id": row[0],
        "title": title,
        "year": year,
        "rating": rating,
        "views": views,
        "country": country,
        "genres": genres,
        "line1": line1,
        "line2": line2,
        "key": entry["key"],
        "description": f"{line1}\n{line2}",
    }


def build_top_rated(rows, limit=15):
    best = {}
    for row in rows:
        title = parse_film_title(row[2])
        entry = lookup_rating(title)
        if not entry:
            continue
        card = make_rating_card(row, entry)
        prev = best.get(entry["key"])
        if prev is None or card["views"] > prev["views"]:
            best[entry["key"]] = card
    cards = list(best.values())
    cards.sort(key=lambda c: (c["rating"], c["views"]), reverse=True)
    return cards[:limit]


def is_rating_query(query):
    q = (query or "").strip().lower().lstrip(".")
    return q in {"top:rating", "top:reyting", "rating", "reyting"}


def rating_list_text(cards):
    lines = ["🏆 <b>Reytingi yuqori filmlar:</b>", ""]
    for card in cards:
        lines.append(f"<b>{html.escape(card['title'])}</b>")
        lines.append(card["line1"])
        lines.append(html.escape(card["line2"]))
        lines.append("")
    return "\n".join(lines).strip()


def rating_films_keyboard(cards, back="sq_menu"):
    key = InlineKeyboardMarkup()
    for card in cards:
        label = f"⭐ {card['rating']:.1f}  {card['title']}"[:64]
        key.add(InlineKeyboardButton(text=label, callback_data=f"film-{card['id']}"))
    if back:
        key.add(InlineKeyboardButton(text="⬅️ Ortga", callback_data=back))
    return key


def cards_from_search_rows(rows, limit=15):
    rated = build_top_rated(rows, limit)
    if rated:
        return rated
    cards = []
    for row in rows[:limit]:
        title = parse_film_title(row[2]) or f"Kod {row[0]}"
        year, country, genres = parse_caption_meta(row[2])
        views = int(row[3] or 0)
        line1 = f"📅 {year or '—'} | 👁 {views}"
        line2 = _meta_line(country, genres)
        cards.append(
            {
                "id": row[0],
                "title": title,
                "line1": line1,
                "line2": line2,
                "description": f"{line1}\n{line2}",
            }
        )
    return cards


def inline_rating_results(cards):
    results = []
    for card in cards:
        markup = InlineKeyboardMarkup().add(
            InlineKeyboardButton(text="📥 Tomosha qilish", callback_data=f"film-{card['id']}")
        )
        body = (
            f"<b>{html.escape(card['title'])}</b>\n"
            f"{card['line1']}\n"
            f"{html.escape(card['line2'])}\n\n"
            f"🆔 Kod: <code>{card['id']}</code>"
        )
        results.append(
            InlineQueryResultArticle(
                id=str(card["id"]),
                title=card["title"][:64],
                description=card["description"][:255],
                thumbnail_url=THUMB_URL,
                input_message_content=InputTextMessageContent(body, parse_mode="HTML"),
                reply_markup=markup,
            )
        )
    return results
