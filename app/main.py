from __future__ import annotations
import asyncio, logging, os, signal, time
from contextlib import contextmanager
from pathlib import Path
import uvicorn
from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand, MenuButtonCommands
from app.admin_bot import router as admin_router, set_user_bot as set_admin_user_bot
from app.config import settings
from app.health import app as health_app, set_storage_ready
from app.user_bot import router as user_router, set_admin_bot
from app import database as db
from app.ytdlp_runtime import daily_update_loop
from app.state import USERS, HISTORY, BLOCKED, STATS
log=logging.getLogger('mediamate')

def configure_logging():
    logging.basicConfig(level=getattr(logging,settings.log_level,logging.INFO),format='%(asctime)s | %(levelname)s | %(name)s | %(message)s')

def cleanup_stale_temp_dirs(root: Path, max_age_seconds: int=6*60*60):
    root.mkdir(parents=True,exist_ok=True); now=time.time()
    for child in root.iterdir():
        try:
            if child.is_dir() and now-child.stat().st_mtime>max_age_seconds:
                import shutil; shutil.rmtree(child,ignore_errors=True)
            elif child.is_file() and now-child.stat().st_mtime>max_age_seconds: child.unlink(missing_ok=True)
        except OSError: pass
class QuietUvicornServer(uvicorn.Server):
    @contextmanager
    def capture_signals(self): yield
async def report_loop(admin_bot: Bot):
    from datetime import datetime, date
    from zoneinfo import ZoneInfo
    tz=ZoneInfo(os.getenv('REPORT_TIMEZONE','Asia/Tashkent')); report_hour=int(os.getenv('REPORT_HOUR','9'))
    if await db.get_setting('last_ten_day_report') is None:
        await db.set_setting('last_ten_day_report', datetime.now(tz).date().isoformat())
    while True:
        try:
            now=datetime.now(tz); today=now.date(); today_s=today.isoformat()
            last_daily=await db.get_setting('last_daily_report')
            if now.hour >= report_hour and last_daily != today_s:
                rows=await db.report_rows(1)
                if rows:
                    r=rows[-1]
                    text=(f"📊 MediaMate — ежедневный отчёт\nДата: {r['day']}\nНовые пользователи: {r['new_users']}\nУспешные загрузки: {r['downloads_ok']}\nОшибки: {r['downloads_failed']}\nСреднее время: {int(r['avg_ms'])/1000:.1f} сек")
                    for aid in settings.admin_ids:
                        try: await admin_bot.send_message(aid,text)
                        except Exception: pass
                await db.set_setting('last_daily_report',today_s)
            last_ten=await db.get_setting('last_ten_day_report')
            if now.hour >= report_hour and last_ten and (today-date.fromisoformat(last_ten)).days >= 10:
                rows=await db.report_rows(10)
                if rows:
                    lines=['📈 MediaMate — отчёт за 10 дней']
                    for r in rows: lines.append(f"{r['day']} {'█'*min(20,int(r['downloads_ok']))} ✓{r['downloads_ok']} ✕{r['downloads_failed']} · +{r['new_users']} пользователей")
                    for aid in settings.admin_ids:
                        try: await admin_bot.send_message(aid,'\n'.join(lines))
                        except Exception: pass
                await db.set_setting('last_ten_day_report',today_s)
        except asyncio.CancelledError: raise
        except Exception: log.exception('Scheduled report failed; will retry next minute')
        await asyncio.sleep(60)



def build_user_bot(token: str) -> Bot:
    if not settings.telegram_api_base_url:
        return Bot(token=token)
    from aiogram.client.session.aiohttp import AiohttpSession
    from aiogram.client.telegram import TelegramAPIServer
    api = TelegramAPIServer.from_base(settings.telegram_api_base_url)
    return Bot(token=token, session=AiohttpSession(api=api))


async def register_bot_commands(user_bot: Bot, admin_bot: Bot) -> None:
    user_commands = [
        BotCommand(command="start", description="Запустить MediaMate"),
        BotCommand(command="download", description="Подготовить медиа по ссылке"),
        BotCommand(command="cancel", description="Отменить текущую загрузку"),
        BotCommand(command="help", description="Инструкция по использованию"),
        BotCommand(command="menu", description="Показать главное меню"),
        BotCommand(command="profile", description="Мой профиль и настройки"),
        BotCommand(command="stats", description="Статистика текущего сеанса"),
        BotCommand(command="history", description="История только текущего сеанса"),
        BotCommand(command="clearhistory", description="Очистить историю сеанса"),
        BotCommand(command="settings", description="Качество и формат медиа"),
        BotCommand(command="language", description="Сменить язык интерфейса"),
        BotCommand(command="support", description="Создать обращение в поддержку"),
        BotCommand(command="privacy", description="Какие данные хранит бот"),
        BotCommand(command="id", description="Показать Telegram ID"),
        BotCommand(command="about", description="Информация о сервисе"),
    ]
    admin_commands = [
        BotCommand(command="start", description="Войти в админ-панель"),
        BotCommand(command="panel", description="Открыть панель управления"),
        BotCommand(command="stats", description="Статистика сервиса"),
        BotCommand(command="backup", description="Резервная копия базы данных"),
        BotCommand(command="user", description="Найти пользователя по ID/username"),
        BotCommand(command="block", description="Заблокировать пользователя"),
        BotCommand(command="unblock", description="Разблокировать пользователя"),
        BotCommand(command="reply", description="Ответить на тикет"),
        BotCommand(command="close", description="Закрыть тикет"),
        BotCommand(command="report", description="Отчёт за 10 дней"),
        BotCommand(command="ytdlp_status", description="Версия yt-dlp"),
        BotCommand(command="update_ytdlp", description="Обновить yt-dlp"),
        BotCommand(command="recentdownloads", description="Сводка загрузок без ссылок"),
        BotCommand(command="tickets", description="Открытые тикеты поддержки"),
        BotCommand(command="dbinfo", description="Размер БД и число записей"),
        BotCommand(command="audit", description="Журнал действий админов"),
        BotCommand(command="cancel", description="Отменить действие"),
        BotCommand(command="logout", description="Выйти из админ-панели"),
        BotCommand(command="setlimit", description="Изменить лимиты загрузки"),
        BotCommand(command="source", description="Включить или отключить источник"),
        BotCommand(command="broadcast_ru", description="Рассылка на русском"),
        BotCommand(command="broadcast_uz", description="Рассылка на узбекском"),
        BotCommand(command="broadcast_en", description="Рассылка на английском"),
    ]
    try:
        await user_bot.set_my_commands(user_commands)
        await admin_bot.set_my_commands(admin_commands)
        # Explicitly expose Telegram's built-in command menu next to the input field.
        await user_bot.set_chat_menu_button(menu_button=MenuButtonCommands())
        await admin_bot.set_chat_menu_button(menu_button=MenuButtonCommands())
    except Exception:
        log.exception("Could not register Telegram bot command menus")


async def main():
    configure_logging(); log=logging.getLogger('mediamate')
    settings.temp_dir.mkdir(parents=True,exist_ok=True); cleanup_stale_temp_dirs(settings.temp_dir)
    await db.init_database()
    # Restore persistent preferences only. Download link history deliberately stays in RAM.
    restored_users = await db.load_users()
    USERS.update(restored_users)
    BLOCKED.update(uid for uid, profile in restored_users.items() if profile.get('blocked'))
    restored_stats = await db.stats()
    STATS.update({'users_seen': restored_stats['users'], 'downloads_ok': restored_stats['ok'], 'downloads_failed': restored_stats['failed']})
    set_storage_ready(True)
    user_bot=build_user_bot(settings.user_bot_token); admin_bot=Bot(token=settings.admin_bot_token); set_admin_bot(admin_bot); set_admin_user_bot(user_bot)
    await register_bot_commands(user_bot, admin_bot)
    user_dp=Dispatcher(); user_dp.include_router(user_router)
    admin_dp=Dispatcher(); admin_dp.include_router(admin_router)
    port=int(os.getenv('PORT','10000'))
    server=QuietUvicornServer(uvicorn.Config(health_app,host='0.0.0.0',port=port,log_level=settings.log_level.lower(),access_log=False))
    async def notify_update_failure(message: str):
        for admin_id in settings.admin_ids:
            try:
                await admin_bot.send_message(admin_id, message)
            except Exception:
                log.exception("Could not notify admin about yt-dlp updater")

    from app.state import ACTIVE
    tasks={
        asyncio.create_task(user_dp.start_polling(user_bot,handle_signals=False,close_bot_session=False,tasks_concurrency_limit=100),name='user-bot'),
        asyncio.create_task(admin_dp.start_polling(admin_bot,handle_signals=False,close_bot_session=False,tasks_concurrency_limit=50),name='admin-bot'),
        asyncio.create_task(server.serve(),name='health-server'),
        asyncio.create_task(report_loop(admin_bot),name='report-loop'),
        asyncio.create_task(daily_update_loop(lambda: bool(ACTIVE), notify_update_failure),name='yt-dlp-update-loop'),
    }
    stop=asyncio.Event(); loop=asyncio.get_running_loop()
    for sig in (signal.SIGTERM,signal.SIGINT):
        try: loop.add_signal_handler(sig,stop.set)
        except (NotImplementedError,RuntimeError): pass
    stop_task=asyncio.create_task(stop.wait(),name='shutdown-waiter')
    log.info('MediaMate started: user bot, admin bot, SQLite storage and health endpoint')
    done,_=await asyncio.wait(tasks|{stop_task},return_when=asyncio.FIRST_COMPLETED)
    for task in done:
        if task is not stop_task and not task.cancelled():
            exc=task.exception()
            if exc: log.error('Task %s stopped: %s',task.get_name(),type(exc).__name__)
    server.should_exit=True
    for dp in (user_dp,admin_dp):
        try: await dp.stop_polling()
        except RuntimeError: pass
    for task in tasks|{stop_task}:
        if not task.done(): task.cancel()
    await asyncio.gather(*tasks,stop_task,return_exceptions=True)
    await user_bot.session.close(); await admin_bot.session.close()
    set_storage_ready(False); await db.close_database()
    log.info('MediaMate stopped cleanly')
if __name__=='__main__': asyncio.run(main())
