import asyncio
import random
import time
import aiosqlite

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    Message, InlineKeyboardMarkup, InlineKeyboardButton,
    CallbackQuery, LabeledPrice, PreCheckoutQuery
)
from aiogram.client.default import DefaultBotProperties

# ═══════════════════════════════════════════════════════════
#  НАСТРОЙКИ
# ═══════════════════════════════════════════════════════════

TOKEN = "9187868633:AAHtfGSY2X_-KHHt-Q0yt3PwnURh60k63AY"   # ← вставьте токен от @BotFather

DB_NAME = "game.db"
ENERGY_INTERVAL = 120        # 1 монетка каждые 2 минуты (в секундах)
ENERGY_CAP = 60              # максимум бесплатных монеток
BASE_PRICE = 50.0            # базовая цена номера в BYN

# ═══════════════════════════════════════════════════════════
#  ИГРОВЫЕ ДАННЫЕ
# ═══════════════════════════════════════════════════════════

REGIONS = {
    "01": "Минская обл. (Минск)",
    "02": "Минская обл.",
    "03": "Гомельская обл.",
    "04": "Брестская обл.",
    "05": "Витебская обл.",
    "06": "Гродненская обл.",
    "07": "Могилёвская обл.",
    "08": "Минск (город)",
}

RARITIES = {
    "common":    {"chance": 55.0, "mult": 1,   "emoji": "⚪", "name": "Обычный"},
    "uncommon":  {"chance": 25.0, "mult": 3,   "emoji": "🟢", "name": "Необычный"},
    "rare":      {"chance": 12.0, "mult": 8,   "emoji": "🔵", "name": "Редкий"},
    "epic":      {"chance": 5.0,  "mult": 20,  "emoji": "🟣", "name": "Эпический"},
    "legendary": {"chance": 2.5,  "mult": 60,  "emoji": "🟠", "name": "Легендарный"},
    "mythic":    {"chance": 0.5,  "mult": 200, "emoji": "🔴", "name": "Мифический"},
}

SHOP_PACKS = {
    "pack_10":  {"energy": 10,  "stars": 15,  "title": "10 ⚡"},
    "pack_50":  {"energy": 50,  "stars": 60,  "title": "50 ⚡"},
    "pack_150": {"energy": 150, "stars": 150, "title": "150 ⚡"},
    "pack_500": {"energy": 500, "stars": 450, "title": "500 ⚡"},
}

# ═══════════════════════════════════════════════════════════
#  ЛОГИКА ИГРЫ
# ═══════════════════════════════════════════════════════════

def roll_rarity() -> str:
    roll = random.uniform(0, 100)
    acc = 0.0
    for name, data in RARITIES.items():
        acc += data["chance"]
        if roll <= acc:
            return name
    return "common"


def generate_plate():
    letters = random.sample("ABCEHKMOPTXY", 2)
    digits = f"{random.randint(1, 999):03d}"
    region = random.choice(list(REGIONS.keys()))
    plate = f"{digits} {letters[0]}{letters[1]}-{region}"
    return plate, region


def spin() -> dict:
    rarity = roll_rarity()
    plate, region = generate_plate()
    price = round(BASE_PRICE * RARITIES[rarity]["mult"], 2)
    return {
        "plate": plate,
        "region": REGIONS[region],
        "region_code": region,
        "rarity": rarity,
        "price": price,
    }


# ═══════════════════════════════════════════════════════════
#  БАЗА ДАННЫХ
# ═══════════════════════════════════════════════════════════

async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                balance REAL DEFAULT 100.0,
                spins INTEGER DEFAULT 0,
                energy INTEGER DEFAULT 60,
                last_energy_time INTEGER DEFAULT 0,
                bonus_energy INTEGER DEFAULT 0
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS inventory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                plate TEXT,
                region TEXT,
                rarity TEXT,
                price REAL,
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            )
        """)
        await db.commit()


def _recalc_energy(energy: int, last_time: int) -> tuple[int, int]:
    now = int(time.time())
    if last_time == 0:
        return energy, now
    if energy >= ENERGY_CAP:
        return energy, now
    gained = (now - last_time) // ENERGY_INTERVAL
    if gained <= 0:
        return energy, last_time
    new_energy = min(energy + gained, ENERGY_CAP)
    new_last = last_time + gained * ENERGY_INTERVAL
    if new_energy >= ENERGY_CAP:
        new_last = now
    return new_energy, new_last


async def get_user(user_id: int, username: str = "") -> dict:
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT * FROM users WHERE user_id=?", (user_id,)) as cur:
            row = await cur.fetchone()
.commit        if not row:
            now = int(time.time())
            await db.execute(
                "INSERT INTO users (user_id, username, energy, last_energy_time) "
                "VALUES (?, ?, 60, ?)",
                (user_id, username, now),
            )
            await db()
            return {
                "user_id": user_id, "username": username, "balance": 100.0,
                "spins": 0, "energy": 60, "last_energy_time": now,
                "bonus_energy": 0,
            }
        energy, last = _recalc_energy(row[4], row[5])
        if (energy, last) != (row[4], row[5]):
            await db.execute(
                "UPDATE users SET energy=?, last_energy_time=? WHERE user_id=?",
                (energy, last, user_id),
            )
            await db.commit()
        return {
            "user_id": row[0], "username": row[1], "balance": row[2],
            "spins": row[3], "energy": energy, "last_energy_time": last,
            "bonus_energy": row[6],
        }


async def _update_field(user_id: int, field: str, value):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(f"UPDATE users SET {field}=? WHERE user_id=?", (value, user_id))
        await db.commit()


async def update_balance(user_id: int, delta: float):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (delta, user_id))
        await db.commit()


async def spend_energy(user_id: int, amount: int = 1) -> bool:
    user = await get_user(user_id)
    bonus = user["bonus_energy"]
    energy = user["energy"]
    if bonus + energy < amount:
        return False
    if bonus >= amount:
        await _update_field(user_id, "bonus_energy", bonus - amount)
    else:
        rest = amount - bonus
        await _update_field(user_id, "bonus_energy", 0)
        await _update_field(user_id, "energy", energy - rest)
    return True


async def add_bonus_energy(user_id: int, amount: int):
    user = await get_user(user_id)
    await _update_field(user_id, "bonus_energy", user["bonus_energy"] + amount)


async def add_to_inventory(user_id: int, plate: str, region: str, rarity: str, price: float):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT INTO inventory (user_id, plate, region, rarity, price) VALUES (?, ?, ?, ?, ?)",
            (user_id, plate, region, rarity, price),
        )
        await db.commit()


async def get_inventory(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT id, plate, region, rarity, price FROM inventory WHERE user_id=?",
            (user_id,),
        ) as cur:
            return await cur.fetchall()


async def remove_item(item_id: int, user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT price FROM inventory WHERE id=? AND user_id=?", (item_id, user_id)
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return None
        await db.execute("DELETE FROM inventory WHERE id=?", (item_id,))
        await db.commit()
        return row[0]


# ═══════════════════════════════════════════════════════════
#  БОТ
# ═══════════════════════════════════════════════════════════

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()


def energy_str(user: dict) -> str:
    total = user["energy"] + user["bonus_energy"]
    return (
        f"⚡ Энергия: <b>{total}</b> "
        f"(бесплатных {user['energy']}/{ENERGY_CAP} + купленных {user['bonus_energy']})"
    )


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎰 Крутить (-1 ⚡)", callback_data="spin")],
        [InlineKeyboardButton(text="🎒 Гараж", callback_data="inventory")],
        [InlineKeyboardButton(text="💰 Баланс", callback_data="balance")],
        [InlineKeyboardButton(text="🛒 Купить монетки", callback_data="shop")],
        [InlineKeyboardButton(text="📊 Шансы", callback_data="chances")],
    ])


@dp.message(Command("start"))
async def cmd_start(message: Message):
    user = await get_user(message.from_user.id, message.from_user.username or "")
    await message.answer(
        "🚗 <b>Номера Беларуси</b>\n\n"
        "Крути номера за энергию ⚡. Бесплатно капает <b>1 монетка / 2 мин</b>, "
        f"максимум <b>{ENERGY_CAP}</b>.\n\n"
        f"{energy_str(user)}",
        reply_markup=main_menu(),
    )


@dp.callback_query(F.data == "spin")
async def cb_spin(callback: CallbackQuery):
    user = await get_user(callback.from_user.id, callback.from_user.username or "")
    if user["energy"] + user["bonus_energy"] <= 0:
        await callback.answer("❌ Нет энергии! Подождите или купите монетки.", show_alert=True)
        return

    if not await spend_energy(callback.from_user.id, 1):
        await callback.answer("❌ Недостаточно энергии.", show_alert=True)
        return

    result = spin()
    await add_to_inventory(
        callback.from_user.id, result["plate"], result["region"],
        result["rarity"], result["price"],
    )
    r = RARITIES[result["rarity"]]
    user = await get_user(callback.from_user.id)

    await callback.message.answer(
        f"{r['emoji']} <b>{r['name']} номер!</b>\n\n"
        f"🔖 <code>{result['plate']}</code>\n"
        f"📍 {result['region']}\n"
        f"💵 Цена: <b>{result['price']} BYN</b>\n\n"
        f"{energy_str(user)}",
        reply_markup=main_menu(),
    )
    await callback.answer()


@dp.callback_query(F.data == "inventory")
async def cb_inventory(callback: CallbackQuery):
    items = await get_inventory(callback.from_user.id)
    if not items:
        await callback.message.answer("🎒 Ваш гараж пуст. Крутите номера!")
        await callback.answer()
        return

    text = "🎒 <b>Ваш гараж:</b>\n\n"
    buttons = []
    for item_id, plate, region, rarity, price in items:
        r = RARITIES[rarity]
        text += f"{r['emoji']} <code>{plate}</code> — {price} BYN\n"
        buttons.append([InlineKeyboardButton(
            text=f"💸 Продать {plate} ({price} BYN)",
            callback_data=f"sell:{item_id}",
        )])
    buttons.append([InlineKeyboardButton(text="⬅️ Меню", callback_data="menu")])
    await callback.message.answer(
        text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("sell:"))
async def cb_sell(callback: CallbackQuery):
    item_id = int(callback.data.split(":")[1])
    price = await remove_item(item_id, callback.from_user.id)
    if price is None:
        await callback.answer("❌ Номер не найден.", show_alert=True)
        return
    await update_balance(callback.from_user.id, price)
    await callback.answer(f"✅ Продано за {price} BYN!", show_alert=True)
    await cb_inventory(callback)


@dp.callback_query(F.data == "balance")
async def cb_balance(callback: CallbackQuery):
    user = await get_user(callback.from_user.id)
    await callback.message.answer(
        f"💰 Баланс: <b>{user['balance']:.2f} BYN</b>\n"
        f"{energy_str(user)}"
    )
    await callback.answer()


@dp.callback_query(F.data == "chances")
async def cb_chances(callback: CallbackQuery):
    text = "📊 <b>Шансы выпадения:</b>\n\n"
    for data in RARITIES.values():
        text += f"{data['emoji']} {data['name']}: {data['chance']}% (×{data['mult']})\n"
    await callback.message.answer(text)
    await callback.answer()


@dp.callback_query(F.data == "shop")
async def cb_shop(callback: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"{p['title']} — {p['stars']} ⭐",
            callback_data=f"buy:{key}",
        )]
        for key, p in SHOP_PACKS.items()
    ] + [[InlineKeyboardButton(text="⬅️ Меню", callback_data="menu")]])
    await callback.message.answer(
        "🛒 <b>Магазин монеток</b>\n\n"
        "Оплата в Telegram Stars ⭐.\n"
        "Купленные монетки <b>не сгорают</b> и не входят в лимит 60.",
        reply_markup=kb,
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("buy:"))
async def cb_buy(callback: CallbackQuery):
    pack_key = callback.data.split(":")[1]
    pack = SHOP_PACKS.get(pack_key)
    if not pack:
        await callback.answer("Пакет не найден", show_alert=True)
        return

    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title=f"Пакет энергии: {pack['title']}",
        description=f"Вы получите {pack['energy']} монеток энергии",
        payload=f"energy:{pack_key}",
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label=pack["title"], amount=pack["stars"])],
    )
    await callback.answer()


@dp.pre_checkout_query()
async def pre_checkout(q: PreCheckoutQuery):
    await q.answer(ok=True)


@dp.message(F.successful_payment)
async def on_payment(message: Message):
    payload = message.successful_payment.invoice_payload
    _, pack_key = payload.split(":")
    pack = SHOP_PACKS.get(pack_key)
    if not pack:
        return
    await add_bonus_energy(message.from_user.id, pack["energy"])
    user = await get_user(message.from_user.id)
    await message.answer(
        f"✅ Оплата прошла! Начислено <b>{pack['energy']} ⚡</b>.\n"
        f"{energy_str(user)}",
        reply_markup=main_menu(),
    )


@dp.callback_query(F.data == "menu")
async def cb_menu(callback: CallbackQuery):
    await callback.message.answer("Главное меню:", reply_markup=main_menu())
    await callback.answer()


# ═══════════════════════════════════════════════════════════
#  ЗАПУСК
# ═══════════════════════════════════════════════════════════

async def main():
    await init_db()
    print("✅ Бот запущен. Нажмите Ctrl+C для остановки.")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())