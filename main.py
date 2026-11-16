from config import *
import movie_search
import series  # o'z handlerlari bor — main'dagi umumiy callback'dan oldin ro'yxatdan o'tishi kerak
import vip  # VIP obuna (to'lov cheki, admin tasdig'i, eslatmalar) — o'z handlerlari bor
import reels  # Reels: kanalga «Kinoni ko'rish» tugmali post joylash — o'z handlerlari bor

@bot.message_handler(commands=['panel'])
def admin_handler(msg):
    cid = msg.chat.id
    if(get_admin(cid)):
        bot.send_message(cid,"<b>⚙️ Assalomu alaykum panlega xush kelibsiz!</b>",reply_markup=admin_menu())


@bot.message_handler(commands=['start'])
def welcome(msg):
    cid = msg.chat.id
    code = str(msg.text).replace("/start", "").strip()

    if code.isdigit():
        if join(cid, "check"):
            if not (send_film(cid, code) or series.send_series(cid, code)):
                bot.send_message(cid, "<b>❌ Kino yoki serial kodi noto'g'ri!</b>", reply_markup=main_menu())
    else:
        try:
            tmp = bot.send_message(cid, "⏳", reply_markup=ReplyKeyboardRemove())
            bot.delete_message(cid, tmp.message_id)
        except Exception:
            pass
        bot.send_message(cid, welcome_text(msg.from_user), reply_markup=main_menu())

    try:
        cursor.execute("INSERT INTO users(chat_id) VALUES(?)", (cid,))
        conn.commit()
    except:
        pass






ADMIN_CALLBACKS = {
    'static', 'send_post', 'send_for', 'list_admin', 'list_chat', 'new-admin',
    'new-chat', 'server', 'add_new_kino', 'delete_kino', 'back', 'top_admin', 'topadd',
}
ADMIN_CALLBACK_PREFIXES = ('del-', 'chan-', 'topdel-', 'topset-', 'topunset-')


def refresh_film_markup(call, code, cid):
    """Saqlash/Top holati o'zgargach, bosilgan xabardagi tugmalarni yangilaydi."""
    try:
        if call.inline_message_id:
            bot.edit_message_reply_markup(
                inline_message_id=call.inline_message_id,
                reply_markup=movie_search.card_markup(code, bot_username(), saved=is_saved(cid, code)),
            )
        elif call.message and call.message.content_type == "video":
            bot.edit_message_reply_markup(
                cid, call.message.id,
                reply_markup=video_key(code, saved=is_saved(cid, code), admin=get_admin(cid)),
            )
        elif call.message:
            bot.edit_message_reply_markup(
                cid, call.message.id,
                reply_markup=InlineKeyboardMarkup()
                .add(admin_film_top_key(code))
                .add(InlineKeyboardButton(text="🔙 Admin panel", callback_data="back")),
            )
    except Exception as e:
        if "message is not modified" not in str(e):
            print("tugmani yangilash xato:", e)


@bot.callback_query_handler(func=lambda call:True)
def callback(call):
    try:
        handle_callback(call)
    except Exception as e:
        print("callback xato:", call.data, e)
    # Javob berilmagan tugmalar Telegramda "loading" holatida qolib ketmasligi uchun
    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass


def handle_callback(call):
    data = call.data or ""
    # Inline rejimda yuborilgan xabarlarda call.message bo'lmaydi
    cid = call.message.chat.id if call.message else call.from_user.id
    mid = call.message.id if call.message else None
    is_admin_action = data in ADMIN_CALLBACKS or data.startswith(ADMIN_CALLBACK_PREFIXES)
    if is_admin_action and not get_admin(cid):
        return
    if data=='remove':
        bot.delete_message(cid,mid)
    if data=='static' and get_admin(cid):
        users = cursor.execute("SELECT count(chat_id) FROM users").fetchone()[0]

        bot.edit_message_text(f"""📊 BOT STATISTIKASI

👤 Userlar: {users}

📱 Hammasi bo'lib: {users}""",cid,mid,reply_markup=back)
    elif data=='send_post':
        a = bot.edit_message_text("<b>Kerakli xabar matnini yubring!\nTuri: Text</b>",cid,mid,reply_markup=back)
        bot.register_next_step_handler(a,send_post)
    elif data=='send_for':
        a = bot.edit_message_text("<b>Kerakli xabar matnini yubring!\nTuri: Forward</b>",cid,mid,reply_markup=back)
        bot.register_next_step_handler(a,send_for)
    elif data=='list_admin':
        admins = cursor.execute("SELECT * FROM admins").fetchall()
        key = InlineKeyboardMarkup()
        for i in admins:
            key.add(InlineKeyboardButton(text="❌ "+str(i[1]),callback_data=f"del-{i[1]}"))
        key.add(InlineKeyboardButton(text="➕ Qo'shish",callback_data='new-admin')).add(InlineKeyboardButton(text="🔙 Exit",callback_data='back'))
        bot.edit_message_text("<b>O'chiqmoqchi bo'lgan admini tanlang!</b>",cid,mid,reply_markup=key)
    elif data=='list_chat':
        admins = cursor.execute("SELECT * FROM chats").fetchall()
        key = InlineKeyboardMarkup()
        for i in admins:
            key.add(InlineKeyboardButton(text="❌ "+str(i[1]),callback_data=f"chan-{i[1]}"))
        key.add(InlineKeyboardButton(text="➕ Qo'shish",callback_data='new-chat')).add(InlineKeyboardButton(text="🔙 Exit",callback_data='back'))
        bot.edit_message_text("<b>O'chiqmoqchi bo'lgan kanalni tanlang!</b>",cid,mid,reply_markup=key)
    elif data=='new-admin':
        a = bot.edit_message_text(f"<b>Yangi id kiriting: {cid}</b>",cid,mid,reply_markup=back)
        bot.register_next_step_handler(a,add_new_admin)
    elif data=='new-chat':
        a = bot.edit_message_text(f"<b>Yangi kanal  kiriting: @kanal_username</b>",cid,mid,reply_markup=back)
        bot.register_next_step_handler(a,add_new_chat)
    elif data.startswith('del-'):
        admin = data.split('-', 1)[1]
        cursor.execute("DELETE FROM admins WHERE chat_id=?", (admin,))
        conn.commit()
        bot.edit_message_text(f"<b>❌ admin {admin} o'chirildi!</b>",cid,mid,reply_markup=back)
    elif data.startswith('chan-'):
        username = data.split('-', 1)[1]
        cursor.execute("DELETE FROM chats WHERE username=?", (username,))
        conn.commit()
        bot.edit_message_text(f"<b>❌ kanal {username} o'chirildi!</b>",cid,mid,reply_markup=back)




    elif data=='admin_exit':
        bot.clear_step_handler_by_chat_id(cid)
        try:
            bot.delete_message(cid, mid)
        except:
            pass
        bot.send_message(cid, welcome_text(call.from_user), reply_markup=main_menu())
    elif data=='back':
        bot.clear_step_handler_by_chat_id(cid)
        if(get_admin(cid)):
            bot.edit_message_text("<b>⚙️ Assalomu alaykum panlega xush kelibsiz!</b>",cid,mid,reply_markup=admin_menu())
    elif data in ("check", "check_search"):
        if is_joined(cid):
            bot.answer_callback_query(call.id, "✅ Obuna tasdiqlandi!")
            if data == "check_search":
                bot.edit_message_text(search_type_text(), cid, mid, reply_markup=search_type_key())
            else:
                try:
                    bot.delete_message(cid, mid)
                except:
                    pass
                bot.send_message(cid, welcome_text(call.from_user), reply_markup=main_menu())
        else:
            bot.answer_callback_query(call.id, "❌ Hali barcha kanallarga obuna bo'lmadingiz!", show_alert=True)
    elif data == "vip":
        bot.answer_callback_query(call.id)
        bot.send_message(cid, vip_text(), reply_markup=main_menu())
    elif data == "menu_saved":
        if not is_joined(cid):
            bot.answer_callback_query(call.id, "❌ Avval kanallarga obuna bo'ling!", show_alert=True)
            bot.send_message(cid, subscribe_text(), reply_markup=join_chat_key("check"))
        else:
            rows = saved_film_rows(cid)
            if not rows:
                bot.answer_callback_query(call.id, "🔖 Saqlangan filmlar yo'q. Film ostidagi «Saqlash» tugmasini bosing.", show_alert=True)
            else:
                bot.answer_callback_query(call.id)
                bot.edit_message_text("🔖 <b>Saqlangan filmlar:</b>", cid, mid, reply_markup=films_keyboard(rows, back="sq_back"))
    elif data == "menu_top":
        if not is_joined(cid):
            bot.answer_callback_query(call.id, "❌ Avval kanallarga obuna bo'ling!", show_alert=True)
            bot.send_message(cid, subscribe_text(), reply_markup=join_chat_key("check"))
        else:
            rows = top_film_rows()
            if not rows:
                bot.answer_callback_query(call.id, "🏆 Top filmlar hali qo'shilmagan.", show_alert=True)
            else:
                bot.answer_callback_query(call.id)
                bot.edit_message_text("🏆 <b>Top filmlar:</b>", cid, mid, reply_markup=films_keyboard(rows, back="sq_back"))
    elif data == "top_admin":
        bot.clear_step_handler_by_chat_id(cid)
        bot.edit_message_text(top_admin_text(), cid, mid, reply_markup=top_admin_key())
    elif data == "topadd":
        a = bot.edit_message_text(
            "🏆 <b>Top filmlarga qo'shish</b>\n\nKino kodini yuboring. Bir nechtasini birdan ham yuborish mumkin:\n<code>164 203 233</code>",
            cid, mid, reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton(text="🔙 Ortga", callback_data="top_admin")),
        )
        bot.register_next_step_handler(a, add_top_step)
    elif data.startswith("topdel-"):
        code = data.split("-", 1)[1]
        set_top(code, False)
        bot.answer_callback_query(call.id, f"❌ {code} Top'dan olib tashlandi")
        bot.edit_message_text(top_admin_text(), cid, mid, reply_markup=top_admin_key())
    elif data.startswith(("topset-", "topunset-")):
        code = data.split("-", 1)[1]
        on = data.startswith("topset-")
        if not set_top(code, on, cid):
            bot.answer_callback_query(call.id, "Kino topilmadi", show_alert=True)
        else:
            bot.answer_callback_query(call.id, "🏆 Top filmlarga qo'shildi" if on else "Top'dan olib tashlandi")
            refresh_film_markup(call, code, cid)
    elif data == "sq_menu":
        bot.edit_message_text(search_type_text(), cid, mid, reply_markup=search_type_key())
    elif data == "sq_back":
        try:
            bot.delete_message(cid, mid)
        except:
            pass
        bot.send_message(cid, welcome_text(call.from_user), reply_markup=main_menu())
    elif data == "sq_name":
        if not is_joined(cid):
            bot.answer_callback_query(call.id, "❌ Avval kanallarga obuna bo'ling!", show_alert=True)
            bot.edit_message_text(subscribe_text(), cid, mid, reply_markup=join_chat_key("check_search"))
        else:
            bot.edit_message_text("🔍 <b>Nom orqali qidiruv</b>\n\nFilm nomini yoki kodini yuboring:", cid, mid)
            bot.register_next_step_handler(call.message, search_film_step)
    elif data == "sq_filter":
        if not is_joined(cid):
            bot.answer_callback_query(call.id, "❌ Avval kanallarga obuna bo'ling!", show_alert=True)
        else:
            bot.edit_message_text(filter_menu_text(), cid, mid, reply_markup=filter_menu_key())
    elif data == "flt_cat_genre":
        if not is_joined(cid):
            bot.answer_callback_query(call.id, "❌ Avval kanallarga obuna bo'ling!", show_alert=True)
        else:
            bot.edit_message_text("🎭 <b>Janrni tanlang:</b>", cid, mid, reply_markup=filter_genre_key())
    elif data == "flt_cat_year":
        if not is_joined(cid):
            bot.answer_callback_query(call.id, "❌ Avval kanallarga obuna bo'ling!", show_alert=True)
        else:
            bot.edit_message_text("📅 <b>Yilni tanlang:</b>", cid, mid, reply_markup=filter_year_key())
    elif data == "flt_cat_country":
        if not is_joined(cid):
            bot.answer_callback_query(call.id, "❌ Avval kanallarga obuna bo'ling!", show_alert=True)
        else:
            bot.edit_message_text("🌍 <b>Davlatni tanlang:</b>", cid, mid, reply_markup=filter_country_key())
    elif data == "flt_cat_lang":
        if not is_joined(cid):
            bot.answer_callback_query(call.id, "❌ Avval kanallarga obuna bo'ling!", show_alert=True)
        else:
            bot.edit_message_text("🎤 <b>Tilni tanlang:</b>", cid, mid, reply_markup=filter_lang_key())
    elif data == "sq_random":
        if not is_joined(cid):
            bot.answer_callback_query(call.id, "❌ Avval kanallarga obuna bo'ling!", show_alert=True)
        else:
            row = cursor.execute("SELECT * FROM video ORDER BY RANDOM() LIMIT 1").fetchone()
            if row is None:
                bot.answer_callback_query(call.id, "Hozircha film yo'q", show_alert=True)
            else:
                bot.answer_callback_query(call.id)
                send_film(cid, row[0])
    elif data == "sq_rating":
        if not is_joined(cid):
            bot.answer_callback_query(call.id, "❌ Avval kanallarga obuna bo'ling!", show_alert=True)
        else:
            cards = top_rated_films(15)
            if not cards:
                bot.answer_callback_query(call.id, "Hozircha film yo'q", show_alert=True)
            else:
                bot.answer_callback_query(call.id)
                bot.edit_message_text(
                    rating_list_text(cards),
                    cid,
                    mid,
                    reply_markup=rating_films_keyboard(cards),
                )
    elif data == "sq_top":
        if not is_joined(cid):
            bot.answer_callback_query(call.id, "❌ Avval kanallarga obuna bo'ling!", show_alert=True)
        else:
            rows = cursor.execute("SELECT * FROM video ORDER BY down DESC LIMIT 10").fetchall()
            if not rows:
                bot.answer_callback_query(call.id, "Hozircha film yo'q", show_alert=True)
            else:
                bot.edit_message_text("📥 <b>Eng ko'p yuklangan filmlar:</b>", cid, mid, reply_markup=films_keyboard(rows))
    elif data == "sq_order":
        if not is_joined(cid):
            bot.answer_callback_query(call.id, "❌ Avval kanallarga obuna bo'ling!", show_alert=True)
        else:
            bot.edit_message_text("📦 <b>Film buyurtma qilish</b>\n\nKerakli film nomini yuboring:", cid, mid)
            bot.register_next_step_handler(call.message, film_order_step)
    elif data.startswith("flt_"):
        if not is_joined(cid):
            bot.answer_callback_query(call.id, "❌ Avval kanallarga obuna bo'ling!", show_alert=True)
        elif data == "flt_other":
            bot.edit_message_text("🗂 Filter uchun so'z yoki yil yuboring:", cid, mid)
            bot.register_next_step_handler(call.message, search_film_step)
        else:
            title, terms, back_cb = resolve_filter(data)
            if not terms:
                bot.answer_callback_query(call.id, "Noma'lum filter", show_alert=True)
            else:
                rows = search_films_caption(terms)
                if not rows:
                    bot.answer_callback_query(call.id, "Film topilmadi", show_alert=True)
                else:
                    bot.edit_message_text(f"🗂 <b>{title}</b>", cid, mid, reply_markup=films_keyboard(rows, back=back_cb))
    elif data == "home":
        bot.answer_callback_query(call.id)
        bot.send_message(cid, welcome_text(call.from_user), reply_markup=main_menu())
    elif data.startswith("hd-"):
        row = get_film(data.split("-", 1)[1])
        if row is None:
            bot.answer_callback_query(call.id, "Kino topilmadi", show_alert=True)
        else:
            info = parse_film(row)
            q = (info.get("quality") or "").lower()
            if "1080" in q or "4k" in q:
                bot.answer_callback_query(call.id, "✅ Bu film Full HD sifatda.", show_alert=True)
            else:
                bot.answer_callback_query(
                    call.id,
                    f"🖥 Hozirgi sifat: {info.get('quality') or '720p'}\nFull HD (1080p) tez orada qo'shiladi.",
                    show_alert=True,
                )
    elif data.startswith("cm-"):
        code = data.split("-", 1)[1]
        bot.answer_callback_query(call.id)
        bot.send_message(cid, comments_text(code), reply_markup=comments_key(code))
    elif data.startswith("ca-"):
        code = data.split("-", 1)[1]
        bot.answer_callback_query(call.id)
        a = bot.send_message(cid, "💬 <b>Sharhingizni yozing:</b>")
        bot.register_next_step_handler(a, lambda m, c=code: comment_step(m, c))
    elif data.startswith("ab-"):
        row = get_film(data.split("-", 1)[1])
        if row is None:
            bot.answer_callback_query(call.id, "Kino topilmadi", show_alert=True)
        else:
            bot.answer_callback_query(call.id)
            bot.send_message(cid, film_about_text(parse_film(row)))
    elif data.startswith("ac-"):
        row = get_film(data.split("-", 1)[1])
        if row is None:
            bot.answer_callback_query(call.id, "Kino topilmadi", show_alert=True)
        else:
            bot.answer_callback_query(call.id)
            bot.send_message(cid, film_actors_text(parse_film(row)))
    elif data.startswith("tr-"):
        row = get_film(data.split("-", 1)[1])
        if row is None:
            bot.answer_callback_query(call.id, "Kino topilmadi", show_alert=True)
        else:
            info = parse_film(row)
            if info.get("trailer"):
                bot.answer_callback_query(call.id)
                bot.send_message(cid, f"▶️ <b>Treyler</b>\n{html.escape(info['trailer'])}")
            else:
                bot.answer_callback_query(call.id, "▶️ Treyler hozircha qo'shilmagan.", show_alert=True)
    elif data.startswith(('save-', 'unsave-')):
        film_id = data.split('-', 1)[1]
        on = data.startswith('save-')
        if not set_saved(cid, film_id, on):
            bot.answer_callback_query(call.id, "Kino topilmadi", show_alert=True)
        else:
            bot.answer_callback_query(
                call.id,
                "✅ Saqlanganlarga qo'shildi! «⭐ Saqlangan» bo'limida topasiz." if on else "🗑 Saqlanganlardan olib tashlandi",
            )
            refresh_film_markup(call, film_id, cid)
    elif data.startswith('film-'):
        film_id = data.split('-', 1)[1]
        if not is_joined(cid):
            bot.answer_callback_query(call.id, "❌ Avval kanallarga obuna bo'ling!", show_alert=True)
            bot.send_message(cid, subscribe_text(), reply_markup=join_chat_key("check_search"))
        elif not send_film(cid, film_id):
            bot.answer_callback_query(call.id, "Kino topilmadi")
        else:
            bot.answer_callback_query(call.id)


    if data=='server':
        a = bot.edit_message_text("<b>🚀 Iltimos kuting...</b>",cid,mid,reply_markup=back).message_id

        def run_speedtest():
            try:
                text = server()
            except Exception as e:
                text = f"<b>❌ Speedtest xato: {html.escape(str(e))}</b>"
            bot.edit_message_text(text, cid, a, reply_markup=back)

        # Speedtest 30+ soniya davom etadi — handler oqimini band qilmaslik uchun fonda
        threading.Thread(target=run_speedtest, daemon=True).start()
    elif data=="add_new_kino":
        a = bot.edit_message_text("<b>➕ Kino video formatda yuboring!</b>",cid,mid,reply_markup=back)
        bot.register_next_step_handler(a,add_new_kino)
    elif data == 'delete_kino':
        a = bot.edit_message_text("<b>➖ Kino kodini kiriting:</b>", cid, mid, reply_markup=back)
        bot.register_next_step_handler(a, delete_kino)


def search_films(query):
    q = f"%{query}%"
    if query.isdigit():
        rows = cursor.execute("SELECT * FROM video WHERE id=?", (query,)).fetchall()
        if rows:
            return rows
    return cursor.execute(
        "SELECT * FROM video WHERE caption LIKE ? OR CAST(id AS TEXT) LIKE ? LIMIT 15",
        (q, q),
    ).fetchall()


def search_films_caption(terms):
    clauses = []
    params = []
    for term in terms:
        clauses.append("caption LIKE ?")
        params.append(f"%{term}%")
    return cursor.execute(
        f"SELECT * FROM video WHERE {' OR '.join(clauses)} ORDER BY down DESC LIMIT 15",
        params,
    ).fetchall()


def resolve_filter(data):
    if data.startswith("flt_g_"):
        text, terms = filter_lookup(FILTER_GENRES, data[6:])
        return (text or "Janr", terms, "flt_cat_genre")
    if data.startswith("flt_c_"):
        text, terms = filter_lookup(FILTER_COUNTRIES, data[6:])
        return (text or "Davlat", terms, "flt_cat_country")
    if data.startswith("flt_l_"):
        text, terms = filter_lookup(FILTER_LANGS, data[6:])
        return (text or "Til", terms, "flt_cat_lang")
    if data.startswith("flt_y_"):
        year = data[6:]
        return (f"{year} yil filmlari", [year], "flt_cat_year")
    return None, None, "sq_filter"


def comment_step(msg, code):
    if not msg.text:
        bot.send_message(msg.chat.id, "<b>❌ Bekor qilindi!</b>", reply_markup=main_menu())
        return
    if msg.text in MENU_BUTTONS:
        send_media(msg)
        return
    text = msg.text.strip()[:300]
    name = user_display_name(msg.from_user)
    cursor.execute(
        "INSERT INTO comments(video_id, chat_id, name, text, created) VALUES(?, ?, ?, ?, ?)",
        (code, msg.chat.id, name, text, int(time.time())),
    )
    conn.commit()
    bot.send_message(msg.chat.id, "✅ <b>Sharhingiz qo'shildi!</b>", reply_markup=comments_key(code))


def search_film_step(msg):
    if not msg.text:
        bot.send_message(msg.chat.id, "<b>❌ Bekor qilindi!</b>", reply_markup=main_menu())
        return
    if msg.text in MENU_BUTTONS:
        send_media(msg)
        return
    rows = search_films(msg.text.strip())
    if not rows:
        bot.send_message(msg.chat.id, "<b>❌ Hech narsa topilmadi.</b>\nFilm kodini yoki nomini yuboring.", reply_markup=search_type_key())
        return
    bot.send_message(msg.chat.id, "🔍 <b>Qidiruv natijasi:</b>\nKerakli filmni tanlang yoki kodini yuboring.", reply_markup=films_keyboard(rows))


def film_order_step(msg):
    if not msg.text:
        bot.send_message(msg.chat.id, "<b>❌ Bekor qilindi!</b>", reply_markup=main_menu())
        return
    if msg.text in MENU_BUTTONS:
        send_media(msg)
        return
    user = msg.from_user
    name = user_display_name(user)
    try:
        bot.send_message(
            ADMIN_ID,
            f"📦 <b>Yangi film buyurtma</b>\n\n👤 {name}\n🆔 <code>{user.id}</code>\n🎬 {html.escape(msg.text)}",
        )
    except Exception as e:
        print(e)
    bot.send_message(msg.chat.id, "✅ <b>Buyurtmangiz yuborildi!</b>\nTez orada ko'rib chiqiladi.", reply_markup=search_type_key())


def handle_ai_step(msg):
    if not msg.text:
        bot.send_message(msg.chat.id, "<b>❌ Bekor qilindi!</b>", reply_markup=main_menu())
        return
    if msg.text in MENU_BUTTONS:
        send_media(msg)
        return
    rows = search_films(msg.text.strip())
    if not rows:
        bot.send_message(msg.chat.id, "✨ <b>Tavsiya topilmadi.</b>\nBoshqa nom yoki janr yozib ko'ring.", reply_markup=main_menu())
        return
    bot.send_message(msg.chat.id, "✨ <b>Sizga tavsiya:</b>", reply_markup=films_keyboard(rows))


@bot.message_handler(content_types=['text'])
def send_media(message: Message):
    text = message.text
    cid = message.chat.id

    if text == BTN_SEARCH:
        if join(cid, "check_search"):
            bot.send_message(cid, search_type_text(), reply_markup=search_type_key())
        return
    if text == BTN_TOP:
        if join(cid, "check"):
            rows = top_film_rows()
            if not rows:
                bot.send_message(cid, "<b>🏆 Hozircha TOP filmlar yo'q.</b>", reply_markup=main_menu())
            else:
                bot.send_message(cid, "🏆 <b>TOP filmlar</b>", reply_markup=films_keyboard(rows))
        return
    if text == BTN_AI:
        if join(cid, "check"):
            a = bot.send_message(cid, "✨ <b>Sun'iy intellekt</b>\n\nQanday film izlayapsiz? Nom, janr yoki tavsif yozing.")
            bot.register_next_step_handler(a, handle_ai_step)
        return
    if text == BTN_SAVED:
        if join(cid, "check"):
            rows = saved_film_rows(cid)
            if not rows:
                bot.send_message(cid, "🔖 <b>Saqlangan filmlar yo'q.</b>\nFilm ostidagi «Saqlash» tugmasini bosing.", reply_markup=main_menu())
            else:
                bot.send_message(cid, "🔖 <b>Saqlangan filmlar:</b>", reply_markup=films_keyboard(rows))
        return
    if text == BTN_VIP:
        vip.show_vip(cid)
        return
    if text == BTN_HELP:
        bot.send_message(
            cid,
            "ℹ️ <b>Yordam</b>\n\n1) Film kodini yuboring\n2) «Film qidiruvi» orqali nom bo'yicha qidiring\n3) «TOP filmlar» dan mashhurlarni oching\n4) Yoqtirgan filmlarni «Saqlash» qiling"
            + (f"\n\nKodlar: {CHANNEL}" if CHANNEL.startswith("@") else ""),
            reply_markup=main_menu(),
        )
        return

    if text.isdigit() and join(cid, "check"):
        if not (send_film(cid, text) or series.send_series(cid, text)):
            bot.reply_to(message, "<b>❌ Kino yoki serial kodi noto'g'ri!</b>")

_bot_username = {}


def bot_username():
    if "name" not in _bot_username:
        _bot_username["name"] = bot.get_me().username
    return _bot_username["name"]


@bot.inline_handler(lambda query: True)
def inline_query(query):
    text = (query.query or "").strip()[:64]
    try:
        offset = max(int(query.offset or 0), 0)
    except ValueError:
        offset = 0
    low = movie_search.norm(text)
    personal = low in movie_search.SAVED_QUERIES
    is_top = low in movie_search.TOP_QUERIES
    is_series = low in movie_search.SERIES_QUERIES
    try:
        username = bot_username()
        items, next_offset = movie_search.items_for_query(text, query.from_user.id, offset, username)
        # Saqlangan, Top va seriallar ro'yxati admin tomonidan tez-tez o'zgaradi — qisqa kesh
        short_cache = personal or is_top or is_series
        opts = dict(cache_time=5 if short_cache else 30, is_personal=personal, next_offset=next_offset)
        if not items and offset == 0 and short_cache:
            hint = (
                "🔖 Saqlangan kinolar yo'q — kino ostidagi «Saqlash»ni bosing" if personal
                else "🏆 Top filmlar hali qo'shilmagan" if is_top
                else "📺 Seriallar hali qo'shilmagan"
            )
            opts["button"] = InlineQueryResultsButton(text=hint, start_parameter="menu")
        posters = movie_search.answer_with_items(
            lambda results: bot.answer_inline_query(query.id, results, **opts), items
        )
        print(f"inline: {text!r} offset={offset} -> {len(items)} ta natija, {posters} ta posterli")
    except Exception as e:
        print("inline xato:", e)
        try:
            bot.answer_inline_query(query.id, [], cache_time=1)
        except Exception:
            pass


print(bot.get_me())

def loop():
    vip.start_reminders()
    try:
        bot.infinity_polling()
    except Exception as e:
        print(e)
        bot.send_message(ADMIN_ID,e)
        bot.infinity_polling()

if __name__ == "__main__":
    loop()
