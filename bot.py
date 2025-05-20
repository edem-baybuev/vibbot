import os
import logging
import asyncio
import aiomysql
from datetime import datetime, timedelta
from dotenv import load_dotenv
from aiogram import F
from aiogram.types import BotCommand, Message
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command, StateFilter

from aiogram.enums import ChatAction
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton
)
from openai import AsyncOpenAI
from db import save_event
from db import get_user_events
from db import init_db_pool
from daily_reminder import daily_reminder_task
import db 
from admin import admin_router
# Загрузка переменных окружения
load_dotenv()

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Инициализация бота и OpenAI
bot = Bot(token=os.getenv('TELEGRAM_TOKEN'))
openai_client = AsyncOpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.getenv('OPENROUTER_API_KEY'),  # Добавьте новый ключ в .env
)
dp = Dispatcher(storage=MemoryStorage())

# Временное хранилище данных
users_data = {}

# Состояния
class Form(StatesGroup):
    event = State()
    gift_advice = State()
   


# --- Клавиатуры ---

add_date_back_to_menu_kb = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="🔙 Назад в меню")]],
    resize_keyboard=True
)
main_menu_kb = ReplyKeyboardMarkup(
    keyboard=[

        [KeyboardButton(text="Посмотреть даты 📅")],
        [KeyboardButton(text="Загрузить дату 📅")]
    ],
    resize_keyboard=True
)

continue_keyboard = InlineKeyboardMarkup(
    inline_keyboard=[
        [
            InlineKeyboardButton(text="Ещё дату ➕", callback_data="add_more"),
            InlineKeyboardButton(text="Всё готово ✅", callback_data="finish"),
            InlineKeyboardButton(text="Изменить дату ✏️", callback_data="edit_last")
        ]
    ]
)
edit_date_kb = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад в меню", callback_data="back_to_menu")]
    ]
)
from db import update_user_event  # не забудь импортировать

@dp.callback_query(lambda c: c.data == 'edit_last')
async def edit_date(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    events = await get_user_events(user_id)
    if not events:
        await callback.answer("Нет событий для редактирования.")
        return

    last_event = events[-1]
    await state.set_state(Form.event)
    await state.update_data(editing_event_id=last_event['id'])

    await callback.message.edit_text(
        f"Вы редактируете событие:\n📅 {last_event['event_date'].strftime('%d.%m.%Y')} - {last_event['event_name']}\n\n"
        "Введите новую дату и название события в формате: ДДММГГГГ Название.",
        reply_markup=None
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data == "add_more")
async def add_more_date(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer("📅 Введите новую дату и событие в формате ДДММГГГГ Событие")
    await state.set_state(Form.event)
    await callback.answer()

@dp.callback_query(lambda c: c.data == "finish")
async def finish_adding(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer("✅ Все события сохранены!", reply_markup=main_menu_kb)
    await state.clear()
    await callback.answer()


# --- Обработчики команд ---
@dp.message(Command('start'))
async def cmd_start(message: types.Message):
    user = message.from_user
    users_data[user.id] = {"events": [], "username": user.username}
    
    await message.answer(
       f"🕒 Привет, {user.first_name}! Я помогу тебе всегда помнить важные даты.\n\n"
    "✨ Моё ключевое преимущество:\n"
    "Все напоминания приходят прямо в Telegram — туда, где ты и так проводишь время!\n\n"
    "📅 Как это работает:\n"
    "1. Отправь дату в формате <b>ДДММГГГГ</b> и название события через пробел\n"
    "2. Я сохраню это в твоём персональном календаре\n"
    "3. Ты получишь уведомление <u>в этот мессенджер</u> ровно в нужный момент\n\n"
    "<i>Пример:</i> <code>15082025 Юбилей компании</code>\n\n"
    "🚀 Попробуй прямо сейчас!",
       parse_mode="HTML",
        reply_markup=main_menu_kb
    )

@dp.message(F.text.in_({"Загрузить дату 📅", "/add"}))
async def add_date_start(message: types.Message, state: FSMContext):
    await state.set_state(Form.event)
    await message.answer("📅 Введи дату и событие в формате:\nДДММГГГГ Название",
    reply_markup=add_date_back_to_menu_kb)

@dp.message(lambda message: message.text == "🔙 Назад в меню")
async def back_to_main_menu(message: types.Message, state: FSMContext):
    await state.clear()  # Очищаем состояние
    await message.answer("🔘 Главное меню", reply_markup=main_menu_kb)    

@dp.message(Form.event)
async def process_event(message: types.Message, state: FSMContext):
    try:
        user_id = message.from_user.id
        
        # Проверка лимита (макс. 10 событий)
        if not await db.check_events_limit(user_id):
            await message.answer(
                "❌ Лимит достигнут! Нельзя сохранить больше 10 событий.\n"
                "Удалить события можно через /delete",
                reply_markup=main_menu_kb
            )
            await state.clear()
            return
        date_str, *event_parts = message.text.split(maxsplit=1)
        if not event_parts:
            await message.answer("❌ Укажите название события после даты!")
            return  # Просто завершаем обработку на этом шаге и ждем новый ввод от пользователя

        event_name = event_parts[0]
        event_date = datetime.strptime(date_str, "%d%m%Y").date()
        if event_date < datetime.now().date():
            await message.answer("❌ Дата должна быть в будущем!")
            return

        data = await state.get_data()
        editing_event_id = data.get("editing_event_id")

        if editing_event_id:
         await update_user_event(
        user_id=message.from_user.id,
        event_id=editing_event_id,
        new_name=event_name,
        new_date=event_date
    )
         await message.answer(f"✏️ Обновлено: {event_date.strftime('%d.%m.%Y')} - {event_name}")
        else:
         await save_event(
        user_id=message.from_user.id,
        username=message.from_user.username or "unknown",
        event_name=event_name,
        event_date=event_date
    )
        await message.answer(
        f"✅ Сохранено: {event_date.strftime('%d.%m.%Y')} - {event_name}\n"
        "Добавить ещё, изменить или завершить?",
        reply_markup=continue_keyboard
    )

        await asyncio.sleep(300)
        text = message.text.lower()
        if any(keyword in text for keyword in keywords):
        # Если одно из ключевых слов найдено, предлагаем выбор подарка
        
         await message.answer(
            "🎁 Кажется, ты упомянул важное событие! Хочешь, я помогу выбрать подарок? Напиши /gift, и я подберу для тебя несколько идей!",
            reply_markup=main_menu_kb  # кнопка с главного меню
        )



        await state.clear()
        

    except ValueError:
        await message.answer("❌ Неверный формат! Используйте: ДДММГГГГ Событие")
    except Exception as e:
        logging.error(f"Ошибка при сохранении: {e}")
        await message.answer("⚠️ Ошибка! Попробуйте ещё раз.")

# Список ключевых слов для фильтрации
keywords = ["день рождение", "годовщина", "праздник", "юбилей", "подарок", "др", "8 марта","14 февраля","день влюбленных"]



# --- Обработчик команды /gift ---
@dp.message(Command('gift'))
async def cmd_gift(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    
    # Проверяем лимит через БД
    if not await db.check_gift_limit(user_id):
        await message.answer("❌ Лимит исчерпан! Попробуйте завтра.")
        return
    
    await state.set_state(Form.gift_advice)
    await message.answer(
        "🎁 Расскажи, кому нужен подарок:\n"
        "• Кто это (друг, партнер, родитель)\n"
        "• Интересы/увлечения\n"
        "• Бюджет (если важно)\n\n"
        "Нейросеть подскажет свои варианты для размышлений",
        reply_markup=InlineKeyboardMarkup(
        inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад в меню", callback_data="back_to_menu")]
    ]
)
    )

@dp.callback_query(lambda c: c.data == "back_to_menu")
async def back_to_menu(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("🔙 Главное меню", reply_markup=None)
    await callback.message.answer("Выбери действие:", reply_markup=main_menu_kb)
    await callback.answer()


from collections import defaultdict
from datetime import date

gift_usage_cache = defaultdict(lambda: {'count': 0, 'date': date.today()})

async def reset_daily_limits():
    while True:
        now = datetime.now()
        if now.hour == 0 and now.minute == 0:
            async with db.db_pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("""
                        UPDATE gift_usage 
                        SET calls_today = 0
                    """)
                    await conn.commit()
        await asyncio.sleep(60)  # Проверяем каждую минуту


@dp.message(StateFilter(Form.gift_advice))
async def get_gift_advice(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    
    # Двойная проверка лимита (на случай если пользователь нашел способ обхода)
    if not await db.check_gift_limit(user_id):
        await message.answer("❌ Лимит исчерпан! Вы можете использовать эту команду только 5 раз в день.")
        await state.clear()
        return
    
    
    wait_msg = await message.answer("⏳ Подождите, генерирую варианты подарков...")

    try:
        prompt = (
            f"Ты помощник по выбору подарков. Пользователь ищет: {message.text}\n\n"
            "Дай 5 вариантов подарка в формате:\n\n"
            "1. [Название]\n"
            "- Описание (1-2 предложения)\n\n"
            "... и так далее для всех 5 вариантов\n\n"
            "Учитывай современные тренды, практичность и бюджет."
        )
        
        response = await openai_client.chat.completions.create(
            model="deepseek/deepseek-chat-v3-0324:free",
            messages=[
                {"role": "system", "content": "Ты эксперт по подаркам. Форматируй ответы четко."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7
        )
        
        clean_response = response.choices[0].message.content.strip()
        
        await wait_msg.delete()
        await message.answer(
            f"🎁 *Варианты подарков:*\n\n{clean_response}",
            parse_mode="Markdown"
        )
        
    except Exception as e:
        await wait_msg.delete()
        logging.error(f"Ошибка генерации подарков: {e}")
        await message.answer("⚠️ Не удалось сгенерировать советы. Попробуйте позже.")
    
    # Очищаем состояние после завершения
    await state.clear()

@dp.message(F.text.in_({"Посмотреть даты 📅", "/dates"}))
async def show_dates_handler(message: types.Message, state: FSMContext):
    await state.clear()
    events = await get_user_events(message.from_user.id)
    
    if not events:
        await message.answer("📅 У вас пока нет сохранённых дат.")
        return

    # Формируем список с датами
    dates_list = [f"• {e['event_date'].strftime('%d.%m.%Y')} - {e['event_name']}" for e in events]
    
    # Отправляем сообщение с подсказкой
    await message.answer(
        "📅 Ваши сохранённые даты:\n\n"
        "Список обновляется автоматически после наступления события.\n"
        "Если вы хотите удалить дату заранее, используйте команду /delete.\n\n"
        + "\n".join(dates_list)
    )




class DeleteState(StatesGroup):
    waiting_for_event_text = State()  # Ожидание ввода строки для удаления

# Команда для начала удаления
@dp.message(Command("delete"))
async def start_delete_event(message: Message, state: FSMContext):
    await message.answer(
        "Введите точную строку события, которое хотите удалить (в формате: `ДДММГГГГ Название события`).",
        parse_mode="Markdown"
    )
    await state.set_state(DeleteState.waiting_for_event_text)

@dp.message(DeleteState.waiting_for_event_text)
async def process_delete_event(message: Message, state: FSMContext):
    user_id = message.from_user.id
    text = message.text.strip()

    # Разбиваем на дату и имя
    try:
        date_part, name_part = text.split(" ", 1)
        event_date = datetime.strptime(date_part, "%d%m%Y").date()
        event_name = name_part.strip()
    except Exception:
        await message.answer("Неверный формат. Используйте: `ДДММГГГГ Название события`", parse_mode="Markdown")
        return

    # Удаляем из базы
    if db.db_pool is None:
        await message.answer("❌ Ошибка подключения к базе данных. Попробуйте позже.")
        return

    async with db.db_pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM events WHERE user_id=%s AND event_date=%s AND event_name=%s",
                (user_id, event_date, event_name)
            )
            await conn.commit()
            if cur.rowcount:
                await message.answer("Событие успешно удалено ✅")
            else:
                await message.answer("Событие не найдено. Убедитесь, что ввели всё точно.")

    await state.clear()


async def setup_bot_commands(bot):
    commands = [
        BotCommand(command="gift", description=" Подобрать подарок"),
        BotCommand(command="dates", description=" Посмотреть даты"),
        BotCommand(command="add", description=" Добавить дату"),
        BotCommand(command="delete", description=" Удалить дату")
    ]
    await bot.set_my_commands(commands)


async def main():
    await init_db_pool()  # <-- СНАЧАЛА инициализируем базу данных

    await setup_bot_commands(bot)
    dp.include_router(admin_router)
    asyncio.create_task(daily_reminder_task(bot))
    asyncio.create_task(reset_daily_limits())  # <-- потом запускаем задачи
    await dp.start_polling(bot)




if __name__ == '__main__':
    asyncio.run(main())
