from aiogram import F, Router, Bot
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from aiogram.filters import Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
import asyncio
import db
from db import db_pool
import logging


admin_router = Router()

# Клавиатуры
admin_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📊 Статистика")],
        [KeyboardButton(text="📢 Сделать рассылку")],
        [KeyboardButton(text="🔙 В главное меню")]
    ],
    resize_keyboard=True
)

main_menu_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Посмотреть даты 📅")],
        [KeyboardButton(text="Загрузить дату 📅")]
    ],
    resize_keyboard=True
)

@admin_router.message(Command("admin"))
async def admin_panel(message: Message):
    if not await db.is_admin(message.from_user.id):
        return
    await message.answer("Админ-панель:", reply_markup=admin_kb)

@admin_router.message(F.text == "🔙 В главное меню")
async def back_to_main_menu(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Главное меню:", reply_markup=main_menu_kb)

@admin_router.message(F.text == "📊 Статистика")
async def show_stats(message: Message):
    if not await db.is_admin(message.from_user.id):
        return
        
    try:
        stats = await db.get_stats()
        gift_stats = await db.get_today_gift_stats()
        
        text = (
            "📊 <b>Статистика бота</b>\n\n"
            f"👥 Всего пользователей: {stats['total_users']}\n"
            f"🔥 Активных (30 дней): {stats['active_users']}\n"
            f"🎁 Использований /gift сегодня: {gift_stats['used']}/{gift_stats['limit']}\n"
            f"👤 Пользователей использовали: {gift_stats['users_count']}"
        )
        await message.answer(text, parse_mode="HTML")
    except Exception as e:
        logging.error(f"Ошибка получения статистики: {e}")
        await message.answer("⚠️ Ошибка при получении статистики")

class BroadcastStates(StatesGroup):
    waiting_for_message = State()

@admin_router.message(F.text == "📢 Сделать рассылку")
async def start_broadcast(message: Message, state: FSMContext):
    if not await db.is_admin(message.from_user.id):
        return
    await state.set_state(BroadcastStates.waiting_for_message)
    await message.answer(
        "Отправьте сообщение для рассылки:",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="❌ Отменить рассылку")]],
            resize_keyboard=True
        )
    )

@admin_router.message(F.text == "❌ Отменить рассылку", BroadcastStates.waiting_for_message)
async def cancel_broadcast(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Рассылка отменена", reply_markup=admin_kb)

@admin_router.message(BroadcastStates.waiting_for_message)
async def process_broadcast(message: Message, state: FSMContext, bot: Bot):
    user_ids = await db.get_all_user_ids()
    
    success = 0
    failed = 0
    
    progress_msg = await message.answer("🔄 Начата рассылка...")
    
    for user_id in user_ids:
        try:
            await bot.copy_message(
                chat_id=user_id,
                from_chat_id=message.chat.id,
                message_id=message.message_id
            )
            success += 1
            
            # Обновляем прогресс каждые 10 отправок
            if success % 10 == 0:
                await progress_msg.edit_text(f"🔄 Отправлено: {success}, Не удалось: {failed}")
                
        except Exception as e:
            failed += 1
            
        await asyncio.sleep(0.1)
    
    await progress_msg.delete()
    await state.clear()
    await message.answer(
        f"📢 Рассылка завершена:\n"
        f"• Успешно: {success}\n"
        f"• Не удалось: {failed}",
        reply_markup=admin_kb
    )
async def log_broadcast(admin_id: int, message: str, success: int, failed: int):
    async with db_pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("""
                INSERT INTO broadcasts 
                (admin_id, message, success_count, failed_count)
                VALUES (%s, %s, %s, %s)
            """, (admin_id, message, success, failed))
            await conn.commit()