import asyncio
from datetime import datetime
import db

last_notified = {}

async def daily_reminder_task(bot):
    while True:
        if db.db_pool is None:
            print("[Напоминание] Ожидание инициализации базы данных...")
            await asyncio.sleep(5)
            continue

        now = datetime.now()
        today = now.date()

        # Проверка: запускаем логику только в 9 утра
        if now.hour == 20:
            try:
                user_ids = await db.get_all_user_ids()
                for user_id in user_ids:
                    event = await db.get_nearest_event(user_id)
                    if not event:
                        continue

                    event_date = event['event_date']
                    days_left = (event_date - today).days
                    last_date = last_notified.get(user_id)

                    if days_left == 0:
                        await bot.send_message(
                            user_id,
                            f"🎉 Сегодня событие: *{event['event_name']}*!",
                            parse_mode="Markdown"
                        )
                        await db.delete_event(event['id'])
                        last_notified[user_id] = today
                        continue

                    if 0 < days_left <= 3:
                        if last_date != today:
                            await bot.send_message(
                                user_id,
                                f"⏳ До события *{event['event_name']}* осталось {days_left} дн.",
                                parse_mode="Markdown"
                            )
                            last_notified[user_id] = today
                        continue

                    if days_left > 3:
                        if last_date is None or (today - last_date).days >= 7:
                            await bot.send_message(
                                user_id,
                                f"📅 Напоминание: ближайшее событие *{event['event_name']}* через {days_left} дн.",
                                parse_mode="Markdown"
                            )
                            last_notified[user_id] = today

            except Exception as e:
                print(f"[Напоминание] Ошибка: {e}")

            # Ждём до следующего дня (24 часа), чтобы снова попасть в 9:00
            await asyncio.sleep(86400)

        else:
            # Если сейчас не 9 утра — подождать 60 секунд и снова проверить
            await asyncio.sleep(60)
