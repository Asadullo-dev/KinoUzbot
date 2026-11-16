# 🎬 Kino Bot — o'rnatish yo'riqnomasi

Telegram kino bot: kino/serial kodlari, qidiruv, TOP filmlar, saqlanganlar, VIP obuna, Reels.

---

## 1. Kerakli narsalar

| Nima | Qayerdan |
|------|----------|
| Python 3.9 yoki yangiroq | https://www.python.org/downloads/ |
| Telegram bot tokeni | https://t.me/BotFather |
| Telegram ID raqamingiz | https://t.me/userinfobot |
| Kino kanal (ixtiyoriy) | Telegram'da o'zingiz yaratasiz |

---

## 2. `.env` faylini to'ldirish

`.env.example` faylidan nusxa olib, nomini **`.env`** qiling:

```bash
cp .env.example .env
```

Keyin `.env` ni oching va quyidagilarni yozing:

### `BOT_TOKEN` — majburiy
1. Telegram'da **[@BotFather](https://t.me/BotFather)** ni oching.
2. `/newbot` yuboring, botga nom va username bering (username `bot` bilan tugashi kerak).
3. BotFather bergan tokenni nusxalang, masalan: `7123456789:AAH...`
4. `.env` ga yozing: `BOT_TOKEN=7123456789:AAH...`

> Inline qidiruv ishlashi uchun BotFather'da: `/setinline` → botingizni tanlang → istalgan matn yozing.

### `ADMIN_ID` — majburiy
1. **[@userinfobot](https://t.me/userinfobot)** ga `/start` yozing.
2. U ko'rsatgan **Id** raqamini oling (masalan `123456789`).
3. `.env` ga yozing: `ADMIN_ID=123456789`

Bu odam bosh admin bo'ladi: `/panel` buyrug'i, to'lov cheklari va film buyurtmalari unga keladi.
Qo'shimcha adminlarni keyin `/panel` orqali qo'shish mumkin.

### `BOT_NAME` — ixtiyoriy
Xabarlarda chiqadigan nom, masalan `BOT_NAME=Mening Kino Botim`.

### `CHANNEL` — ixtiyoriy
Kino kanalingiz username'i, masalan `CHANNEL=@mening_kanalim`.
- «🎬 Barcha Filmlar» tugmasi shu kanalga olib boradi (bo'sh qolsa tugma chiqmaydi).
- Reels postlari shu kanalga joylanadi — buning uchun **botni kanalga admin qiling** («Xabar joylash» huquqi bilan).

Tayyor `.env` namunasi:

```env
BOT_TOKEN=7123456789:AAHxxxxxxxxxxxxxxxxxxxxxxxxxxxx
ADMIN_ID=123456789
BOT_NAME=Mening Kino Botim
CHANNEL=@mening_kanalim
```

---

## 3. O'rnatish va ishga tushirish

```bash
pip install -r requirements.txt
python main.py
```

Birinchi ishga tushganda `database.db` avtomatik yaratiladi (bo'sh baza).

---

## 4. Botni sozlash (Telegram ichida)

Botga `/panel` yuboring va:
- 🎬 kinolarni qo'shing (har biriga kod beriladi);
- 📢 majburiy obuna kanallarini qo'shing (bot o'sha kanallarda admin bo'lishi kerak);
- 💎 VIP bo'limida **to'lov kartangizni** va tariflarni kiriting;
- 👨‍💻 foydalanuvchilar murojaat qiladigan admin username'ini kiriting.

---

## ⚠️ Xavfsizlik

- `.env` va `database.db` fayllarini **hech kimga bermang** — ularda token va foydalanuvchilar ma'lumotlari bor.
- Token tarqalib ketsa: @BotFather → `/revoke` orqali yangisini oling.
