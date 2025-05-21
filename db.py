# db.py
import aiomysql
from datetime import date
from datetime import datetime, timedelta
import os
import asyncio
db_pool = None

async def init_db_pool():
    global db_pool
    
    # Получаем строку подключения из переменных окружения
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise ValueError("DATABASE_URL не задан в переменных окружения!")
    
    # Разбираем URL
    parsed = urlparse(db_url)
    
    # Создаем пул подключений с SSL
    db_pool = await aiomysql.create_pool(
            host=parsed.hostname,
            port=parsed.port or 3306,
            user=parsed.username,
            password=parsed.password,
            db=parsed.path[1:],  # убираем первый "/"
            autocommit=True,
           
        )

    try:
        async with db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT 1")
                print("✅ Успешное подключение к MySQL!")
    except Exception as e:
        print(f"❌ Ошибка подключения к MySQL: {e}")

async def save_event(user_id: int, username: str, event_name: str, event_date: date):
    async with db_pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("""
                INSERT INTO events (user_id, username, event_name, event_date)
                VALUES (%s, %s, %s, %s)
            """, (user_id, username, event_name, event_date))

async def get_user_events(user_id: int):
    async with db_pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute("""
                SELECT id, event_name, event_date
                FROM events
                WHERE user_id = %s
                ORDER BY event_date ASC
            """, (user_id,))
            return await cur.fetchall()

async def get_all_user_ids():
    async with db_pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT DISTINCT user_id FROM events")
            return [row[0] for row in await cur.fetchall()]

async def get_nearest_event(user_id: int):
    async with db_pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute("""
                SELECT id, event_name, event_date
                FROM events
                WHERE user_id = %s AND event_date >= CURDATE()
                ORDER BY event_date ASC LIMIT 1
            """, (user_id,))
            return await cur.fetchone()

async def delete_event(event_id: int):
    async with db_pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM events WHERE id = %s", (event_id,))
# Функция для ручного удаления события по имени
async def delete_event_by_name(user_id: int, event_name: str):
    async with db_pool.acquire() as conn:
        async with conn.cursor() as cur:
            # Ищем событие по имени и пользователю
            await cur.execute("DELETE FROM events WHERE user_id = %s AND event_name = %s", (user_id, event_name))
            # Сохраняем изменения
            await conn.commit()

# db.py

async def update_user_event(user_id: int, event_id: int, new_name: str, new_date: datetime.date):
    async with db_pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                UPDATE events
                SET event_name = %s, event_date = %s
                WHERE id = %s AND user_id = %s
                """,
                (new_name, new_date, event_id, user_id)
            )
            await conn.commit()

async def check_gift_limit(user_id: int) -> bool:
    """Проверяет, может ли пользователь вызвать команду /gift"""
    async with db_pool.acquire() as conn:
        async with conn.cursor() as cur:
            today = datetime.now().date()
            
            # Получаем текущее состояние
            await cur.execute("""
                SELECT calls_today FROM gift_usage 
                WHERE user_id = %s AND last_call_date = %s
                FOR UPDATE
            """, (user_id, today))
            
            result = await cur.fetchone()
            
            if not result:  # Первый вызов сегодня
                await cur.execute("""
                    INSERT INTO gift_usage (user_id, calls_today, last_call_date)
                    VALUES (%s, 1, %s)
                    ON DUPLICATE KEY UPDATE
                    calls_today = 1,
                    last_call_date = %s
                """, (user_id, today, today))
                await conn.commit()
                return True
                
            if result[0] < 5:  # Лимит не исчерпан
                await cur.execute("""
                    UPDATE gift_usage 
                    SET calls_today = calls_today + 1
                    WHERE user_id = %s
                """, (user_id,))
                await conn.commit()
                return True
                
            return False  # Лимит исчерпан
        
async def reset_daily_limits():
    while True:
        now = datetime.now()
        if now.hour == 0 and now.minute == 0:
            async with db_pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("""
                        UPDATE gift_usage 
                        SET calls_today = 0
                    """)
                    await conn.commit()
        await asyncio.sleep(60)  # Проверяем каждую минуту

async def get_stats() -> dict:
    async with db_pool.acquire() as conn:
        async with conn.cursor() as cur:
            # Уникальные пользователи (используем DISTINCT)
            await cur.execute("SELECT COUNT(DISTINCT user_id) FROM events")
            total_users = (await cur.fetchone())[0]
            
            # Активные пользователи (DISTINCT + дата)
            await cur.execute("""
                SELECT COUNT(DISTINCT user_id)
                FROM events
                WHERE event_date >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
            """)
            active_users = (await cur.fetchone())[0]
            
            # Статистика по /gift (из таблицы gift_usage)
            await cur.execute("""
                SELECT COUNT(DISTINCT user_id)
                FROM gift_usage
                WHERE last_call_date = CURDATE()
            """)
            gift_users = (await cur.fetchone())[0] or 0

            
            
            return {
                'total_users': total_users,
                'active_users': active_users,
                'gift_users': gift_users
                   
            }


async def is_admin(user_id: int) -> bool:
    """Проверяет, является ли пользователь админом"""
    return user_id == int(os.getenv("ADMIN_ID"))

async def get_all_user_ids() -> list[int]:
    """Возвращает список всех user_id"""
    async with db_pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT DISTINCT user_id FROM events")
            return [row[0] for row in await cur.fetchall()]
async def get_today_gift_stats():
    """Возвращает статистику по использованию команды /gift за сегодня"""
    async with db_pool.acquire() as conn:
        async with conn.cursor() as cur:
            today = datetime.now().date()
            
            # Общее количество использований
            await cur.execute("""
                SELECT SUM(calls_today) 
                FROM gift_usage 
                WHERE last_call_date = %s
            """, (today,))
            total_used = (await cur.fetchone())[0] or 0
            
            # Количество уникальных пользователей
            await cur.execute("""
                SELECT COUNT(DISTINCT user_id) 
                FROM gift_usage 
                WHERE last_call_date = %s AND calls_today > 0
            """, (today,))
            users_count = (await cur.fetchone())[0] or 0
            
            return {
                'used': total_used,
                'limit': 5,  # Ваш лимит на пользователя
                'users_count': users_count
            }
async def check_events_limit(user_id: int) -> bool:
    """Проверяет, не достигнут ли лимит (10 событий)"""
    async with db_pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT COUNT(*) FROM events WHERE user_id = %s",
                (user_id,)
            )
            result = await cur.fetchone()
            return result[0] < 10 if result else True
