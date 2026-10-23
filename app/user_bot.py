from __future__ import annotations
import asyncio, logging, re, shutil, time, uuid, os
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit
from aiogram import Bot, F, Router
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, ChatMemberUpdated, FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto, InputMediaVideo
from app.config import settings
from app.locales import tr, language_name
from app.keyboards import main_menu, accept_rules, support_keyboard, settings_keyboard, preference_keyboard, format_choices, cancel_download_keyboard, subscription_keyboard
from app.security import validate_public_https_url, sanitize_url
from app.downloader import get_info, media_summary, download_media, DownloadError, DownloadCancelled, remove_temp_dir, set_runtime_file_size_mb, is_youtube_bot_check_error, is_youtube_host
from app.state import USERS, PENDING, ACTIVE, ACTIVE_TASKS, ACTIVE_PROCESSES, ACTIVE_STATUS, CHECKING_TASKS, HISTORY, STATS, BLOCKED
from app import database as db
router=Router(); log=logging.getLogger(__name__); download_semaphore=asyncio.Semaphore(settings.max_global_downloads); metadata_semaphore=asyncio.Semaphore(4); ticket_mode=set()
CHECKING: set[int] = set()
CHECKING_STATUS: dict[int, Message] = {}
admin_bot: Bot|None=None
_last_youtube_notice = 0.0
STICKER_DIR = Path(__file__).resolve().parent.parent / 'assets' / 'stickers'
STICKER_FILES = [STICKER_DIR / name for name in ('hello.webp','download.webp','music.webp','done.webp')]

async def send_brand_sticker(bot: Bot, chat_id: int, purpose: str = 'hello'):
    """Send a monochrome bundled sticker; optional purpose-specific Telegram file IDs may override it."""
    env_key = {'hello': 'STICKER_HELLO_ID', 'download': 'STICKER_DOWNLOAD_ID', 'audio': 'STICKER_AUDIO_ID', 'done': 'STICKER_DONE_ID'}.get(purpose, 'STICKER_HELLO_ID')
    custom = os.getenv(env_key, '').strip()
    chosen_name = {'done': 'done.webp', 'audio': 'music.webp', 'download': 'download.webp'}.get(purpose, 'hello.webp')
    chosen = STICKER_DIR / chosen_name
    try:
        if custom:
            await bot.send_sticker(chat_id, custom)
        elif chosen.exists():
            await bot.send_sticker(chat_id, FSInputFile(chosen))
    except Exception:
        log.debug('Optional MediaMate sticker could not be sent', exc_info=True)


def set_admin_bot(bot: Bot):
    global admin_bot; admin_bot=bot

def get_user(uid: int, name: str='', username: str|None=None):
    if uid not in USERS:
        USERS[uid]={'language':'ru','accepted':False,'name':name or str(uid),'successes':0,'first_seen':datetime.utcnow().strftime('%Y-%m-%d'),'quality':'best','format':'video','last_download':0.0,'offered':False,'username':username,'blocked':False}
        STATS['users_seen']=len(USERS)
    elif name: USERS[uid]['name']=name
    if username is not None: USERS[uid]['username']=username
    return USERS[uid]

async def persist_user(uid:int, user=None):
    user=user or USERS[uid]
    await db.save_user(uid,user,user.get('name'),user.get('username'))

def lang_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='Русский',callback_data='firstlang:ru'),InlineKeyboardButton(text='O‘zbekcha',callback_data='firstlang:uz')],[InlineKeyboardButton(text='English',callback_data='firstlang:en')]])

def safe_format_duration(seconds):
    if not seconds: return '—'
    m,s=divmod(int(seconds),60); return f'{m}:{s:02d}'

@router.message(CommandStart())
async def start(m: Message):
    is_new=m.from_user.id not in USERS
    u=get_user(m.from_user.id, m.from_user.full_name, m.from_user.username)
    await persist_user(m.from_user.id,u)
    if is_new:
        await db.record_new_user()
        if admin_bot:
            for aid in settings.admin_ids:
                try:
                    await admin_bot.send_message(aid, f"Новый пользователь\nИмя: {m.from_user.full_name}\nUsername: @{m.from_user.username or '—'}\nID: {m.from_user.id}\nПервое действие: /start")
                except Exception: pass
    if not u['accepted']:
        if not u.get('language_chosen'):
            await send_brand_sticker(m.bot, m.chat.id, 'hello')
            await m.answer('Til / Язык / Language:', reply_markup=lang_keyboard())
            return
        await m.answer(tr(u['language'],'rules'),reply_markup=accept_rules(u['language']))
        return
    await m.answer(tr(u['language'],'welcome'),reply_markup=main_menu(u['language']))

@router.callback_query(F.data.startswith('firstlang:'))
async def first_language(c: CallbackQuery):
    u=get_user(c.from_user.id,c.from_user.full_name,c.from_user.username); lang=c.data.split(':')[1]
    await persist_user(c.from_user.id,u)
    if lang not in ('ru','uz','en'): await c.answer(); return
    u['language']=lang; u['language_chosen']=True
    await persist_user(c.from_user.id,u)
    if u.get('accepted'):
        await c.message.edit_text(tr(lang,'language_saved'), reply_markup=InlineKeyboardMarkup(inline_keyboard=[]))
        await c.message.answer(tr(lang,'welcome'), reply_markup=main_menu(lang))
    else:
        await c.message.edit_text(tr(lang,'rules'),reply_markup=accept_rules(lang))
    await c.answer()

@router.callback_query(F.data=='rules:accept')
async def accept(c: CallbackQuery):
    u=get_user(c.from_user.id,c.from_user.full_name,c.from_user.username); u['accepted']=True
    await persist_user(c.from_user.id,u)
    await c.message.edit_text(tr(u['language'],'welcome'), reply_markup=InlineKeyboardMarkup(inline_keyboard=[]))
    await c.message.answer(tr(u['language'],'how'),reply_markup=main_menu(u['language']))
    await c.answer()

async def allowed_user(m: Message) -> bool:
    u=get_user(m.from_user.id,m.from_user.full_name,m.from_user.username)
    if u.get('blocked') or m.from_user.id in BLOCKED:
        await m.answer(tr(u['language'],'blocked')); return False
    if not u['accepted']:
        await m.answer(tr(u['language'],'rules_required'),reply_markup=accept_rules(u['language'])); return False
    return True

@router.message(Command('menu'))
async def menu_cmd(m: Message):
    u=get_user(m.from_user.id,m.from_user.full_name,m.from_user.username)
    if not u.get('accepted'):
        if not u.get('language_chosen'):
            return await m.answer('Til / Язык / Language:',reply_markup=lang_keyboard())
        return await m.answer(tr(u['language'],'rules'),reply_markup=accept_rules(u['language']))
    await m.answer(tr(u['language'],'welcome'),reply_markup=main_menu(u['language']))

@router.message(Command('profile'))
async def profile_cmd(m: Message):
    u=get_user(m.from_user.id,m.from_user.full_name,m.from_user.username); lang=u['language']
    if not await allowed_user(m): return
    await m.answer(tr(lang,'profile',id=m.from_user.id,name=u['name'],language=language_name(lang),successes=u['successes'],first_seen=u['first_seen'],quality=u['quality'],format=u['format']),reply_markup=main_menu(lang))

@router.message(Command('history'))
async def history_cmd(m: Message):
    u=get_user(m.from_user.id,m.from_user.full_name,m.from_user.username); lang=u['language']
    if not await allowed_user(m): return
    items=list(HISTORY[m.from_user.id])
    if not items: return await m.answer(tr(lang,'history_empty'),reply_markup=main_menu(lang))
    lines=[tr(lang,'history_title')]
    for i,item in enumerate(reversed(items),1):
        status_key={'success':'history_success','failed':'history_failed','processing':'history_processing','cancelled':'history_cancelled','interrupted':'history_interrupted'}.get(item['status'],'history_unknown')
        # Display only the host, not a full URL or its query parameters.
        host=(urlsplit(item.get('url','')).hostname or 'media')[:70]
        lines.append(f"{i}. {host} — {tr(lang,status_key)}")
    await m.answer('\n'.join(lines),reply_markup=main_menu(lang))


@router.message(Command('favorites'))
async def favorites_cmd(m: Message):
    u=get_user(m.from_user.id,m.from_user.full_name,m.from_user.username); lang=u['language']
    if not await allowed_user(m): return
    await m.answer(tr(lang,'favorites_empty'),reply_markup=main_menu(lang))

@router.message(Command('download'))
async def download_cmd(m: Message):
    u=get_user(m.from_user.id,m.from_user.full_name,m.from_user.username)
    if not await allowed_user(m): return
    await m.answer(tr(u['language'],'send_link'), reply_markup=main_menu(u['language']))


@router.message(Command('privacy'))
async def privacy_cmd(m: Message):
    u=get_user(m.from_user.id,m.from_user.full_name,m.from_user.username)
    await m.answer(tr(u['language'],'privacy_text'), reply_markup=main_menu(u['language']) if u.get('accepted') else None)


@router.message(Command('clearhistory'))
async def clear_history_cmd(m: Message):
    u=get_user(m.from_user.id,m.from_user.full_name,m.from_user.username); lang=u['language']
    if not await allowed_user(m): return
    HISTORY[m.from_user.id].clear()
    await m.answer(tr(lang,'history_cleared'), reply_markup=main_menu(lang))


@router.message(Command('stats'))
async def user_stats_cmd(m: Message):
    u=get_user(m.from_user.id,m.from_user.full_name,m.from_user.username); lang=u['language']
    if not await allowed_user(m): return
    items=list(HISTORY[m.from_user.id])
    success=sum(1 for x in items if x.get('status')=='success')
    failed=sum(1 for x in items if x.get('status')=='failed')
    lines={'ru':f"Статистика текущего сеанса\n\nУспешно: {success}\nОшибки: {failed}\n\nПолные ссылки не сохраняются в базе.", 'uz':f"Joriy sessiya statistikasi\n\nMuvaffaqiyatli: {success}\nXatolar: {failed}\n\nTo‘liq havolalar bazada saqlanmaydi.", 'en':f"Current session statistics\n\nSuccessful: {success}\nFailed: {failed}\n\nFull URLs are not stored in the database."}
    await m.answer(lines[lang], reply_markup=main_menu(lang))


@router.message(Command('id'))
async def id_cmd(m: Message):
    u=get_user(m.from_user.id,m.from_user.full_name,m.from_user.username)
    await m.answer(f"Telegram ID: <code>{m.from_user.id}</code>", parse_mode='HTML', reply_markup=main_menu(u['language']) if u.get('accepted') else None)

@router.message(Command('about'))
async def about_cmd(m: Message):
    u=get_user(m.from_user.id,m.from_user.full_name,m.from_user.username); lang=u['language']
    await m.answer(tr(lang,'about_text'), reply_markup=main_menu(lang) if u.get('accepted') else None)

@router.message(Command('settings'))
async def settings_cmd(m: Message):
    u=get_user(m.from_user.id,m.from_user.full_name,m.from_user.username); lang=u['language']
    if not await allowed_user(m): return
    await m.answer(tr(lang,'settings_title'),reply_markup=settings_keyboard(lang,lang,u.get('quality','best'),u.get('format','video')))

@router.message(Command('support'))
async def support_cmd(m: Message):
    u=get_user(m.from_user.id,m.from_user.full_name,m.from_user.username); lang=u['language']
    if not await allowed_user(m): return
    await m.answer(tr(lang,'support_menu'),reply_markup=support_keyboard(lang))

@router.message(Command('language'))
async def language_cmd(m: Message):
    u=get_user(m.from_user.id,m.from_user.full_name,m.from_user.username)
    await m.answer(tr(u['language'],'choose_language'),reply_markup=lang_keyboard())

@router.message(Command('help'))
async def help_cmd(m: Message):
    u=get_user(m.from_user.id,m.from_user.full_name,m.from_user.username); lang=u['language']
    help_text={
        'ru':'MediaMate · Справка\n\n1. Отправьте публичную ссылку.\n2. Выберите видео, MP3 или оригинальные фото из доступных вариантов.\n3. Кнопка «Отмена» остановит активную загрузку.\n\nОсновные разделы находятся на клавиатуре под полем ввода. /privacy — какие данные хранятся; /cancel — отмена.',
        'uz':'MediaMate · Qo‘llanma\n\n1. Ommaviy havolani yuboring.\n2. Mavjud bo‘lsa video, MP3 yoki asl rasmlarni tanlang.\n3. «Bekor qilish» faol yuklashni to‘xtatadi.\n\nAsosiy bo‘limlar xabar yozish maydoni ostidagi klaviaturada. /privacy — ma’lumotlar; /cancel — bekor qilish.',
        'en':'MediaMate · Help\n\n1. Send a public link.\n2. Choose video, MP3, or original photos when available.\n3. Cancel stops an active download.\n\nMain sections are on the keyboard below the input field. /privacy explains data storage; /cancel stops the current action.',
    }
    await m.answer(help_text[lang],reply_markup=main_menu(lang) if u.get('accepted') else (lang_keyboard() if not u.get('language_chosen') else accept_rules(lang)))

@router.message(Command('cancel'))
async def cancel_cmd(m: Message):
    uid=m.from_user.id
    u=get_user(uid,m.from_user.full_name,m.from_user.username); lang=u['language']
    ticket_mode.discard(uid)
    event=ACTIVE.get(uid)
    if event is not None:
        event.set()
        proc=ACTIVE_PROCESSES.get(uid)
        if proc is not None and proc.returncode is None:
            try: proc.terminate()
            except ProcessLookupError: pass
        active_status=ACTIVE_STATUS.get(uid)
        task=ACTIVE_TASKS.get(uid)
        if task is not None and not task.done(): task.cancel()
        if active_status is not None:
            try: await active_status.edit_text(tr(lang,'download_cancelled'), reply_markup=InlineKeyboardMarkup(inline_keyboard=[]))
            except Exception: pass
        if active_status is None:
            await m.answer(tr(lang,'download_cancelled'), reply_markup=main_menu(lang))
        return
    check_task=CHECKING_TASKS.get(uid)
    if check_task is not None and not check_task.done() and check_task is not asyncio.current_task():
        check_task.cancel()
        check_status=CHECKING_STATUS.get(uid)
        if check_status is not None:
            try: await check_status.edit_text(tr(lang,'download_cancelled'), reply_markup=InlineKeyboardMarkup(inline_keyboard=[]))
            except Exception: pass
        CHECKING.discard(uid)
        if check_status is None:
            await m.answer(tr(lang,'download_cancelled'), reply_markup=main_menu(lang))
        return
    pending=PENDING.pop(uid,None)
    if pending and pending.get('message') is not None:
        try:
            await pending['message'].edit_text(tr(lang,'cancelled_step'),reply_markup=InlineKeyboardMarkup(inline_keyboard=[]))
        except Exception: pass
        return
    await m.answer(tr(lang,'cancelled_step'),reply_markup=main_menu(lang))

@router.message(F.text)
async def text_handler(m: Message):
    if not await allowed_user(m): return
    u=get_user(m.from_user.id,m.from_user.full_name,m.from_user.username); lang=u['language']; text=(m.text or '').strip()
    if m.from_user.id in ticket_mode:
        ticket_mode.discard(m.from_user.id)
        tid=await db.create_ticket(m.from_user.id,text)
        if tid is None:
            await m.answer({'ru':'У вас уже есть два открытых обращения. Дождитесь ответа.','uz':'Sizda ikkita ochiq murojaat bor. Javobni kuting.','en':'You already have two open tickets. Please wait for a reply.'}[lang])
        else:
            await m.answer(tr(lang,'support_created',ticket_id=tid))
            if admin_bot:
                for aid in settings.admin_ids:
                    try: await admin_bot.send_message(aid,f"Ticket · Новый тикет #{tid}\nПользователь: {u['name']} (@{u.get('username') or '—'})\nID: {m.from_user.id}\n\n{text[:3000]}\nОтвет: /reply {tid} текст")
                    except Exception: pass
        return
    if text.startswith('/'):
        return
    if text==tr(lang,'menu_download'):
        await m.answer(tr(lang,'send_link'), reply_markup=main_menu(lang)); return
    if text==tr(lang,'menu_profile'):
        await m.answer(tr(lang,'profile',id=m.from_user.id,name=u['name'],language=language_name(lang),successes=u['successes'],first_seen=u['first_seen'],quality=u['quality'],format=u['format'])); return
    if text==tr(lang,'language'):
        await m.answer(tr(lang,'choose_language'),reply_markup=lang_keyboard()); return
    if text==tr(lang,'menu_history'):
        items=list(HISTORY[m.from_user.id])
        if not items: await m.answer(tr(lang,'history_empty')); return
        lines=[tr(lang,'history_title')]
        for i,item in enumerate(reversed(items),1):
            status_key={'success':'history_success','failed':'history_failed','processing':'history_processing','cancelled':'history_cancelled','interrupted':'history_interrupted'}.get(item['status'],'history_unknown')
            host=(urlsplit(item.get('url','')).hostname or 'media')[:70]
            lines.append(f"{i}. {host} — {tr(lang,status_key)}")
        await m.answer('\n'.join(lines),reply_markup=main_menu(lang)); return
    if text==tr(lang,'menu_support'):
        await m.answer(tr(lang,'support_menu'),reply_markup=support_keyboard(lang)); return
    if text==tr(lang,'menu_how'):
        await m.answer(tr(lang,'how'),reply_markup=main_menu(lang)); return
    if text==tr(lang,'menu_settings'):
        await m.answer(tr(lang,'settings_title'),reply_markup=settings_keyboard(lang,lang,u.get('quality','best'),u.get('format','video'))); return
    match=re.search(r'https?://\S+',text)
    if match:
        await process_link(m,match.group(0).rstrip(').,]')); return
    await m.answer(tr(lang,'send_link'),reply_markup=main_menu(lang))

async def notify_youtube_restriction(reason: str) -> None:
    """Rate-limited admin diagnostics; never sends URLs or credentials."""
    global _last_youtube_notice
    now = time.monotonic()
    if not admin_bot or now - _last_youtube_notice < 3600:
        return
    _last_youtube_notice = now
    for admin_id in settings.admin_ids:
        try:
            await admin_bot.send_message(
                admin_id,
                "YouTube временно отклоняет автоматические запросы с сервера. "
                "Проверьте версию yt-dlp через /ytdlp_status и обновите её через /update_ytdlp. "
                "Обновление не гарантирует доступ, если ограничение установлено на стороне YouTube."
            )
        except Exception:
            log.exception("Could not notify admin about YouTube source restriction")


async def process_link(m: Message, url: str):
    u=get_user(m.from_user.id,m.from_user.full_name,m.from_user.username); lang=u['language']
    if m.from_user.id in CHECKING or m.from_user.id in ACTIVE:
        await m.answer({'ru':'Сначала дождитесь обработки предыдущей ссылки или отмените загрузку через /cancel.', 'uz':'Avval oldingi havola ishlanishini kuting yoki /cancel orqali bekor qiling.', 'en':'Please wait for the previous link to finish processing or cancel it with /cancel.'}[lang])
        return
    CHECKING.add(m.from_user.id)
    current_task=asyncio.current_task()
    if current_task is not None:
        CHECKING_TASKS[m.from_user.id]=current_task
        def cleanup_check(_task, uid=m.from_user.id):
            CHECKING.discard(uid)
            CHECKING_TASKS.pop(uid,None)
            CHECKING_STATUS.pop(uid,None)
        current_task.add_done_callback(cleanup_check)
    status=await m.answer(tr(lang,'checking'), reply_markup=cancel_download_keyboard(lang))
    CHECKING_STATUS[m.from_user.id]=status
    host=(urlsplit(url).hostname or '').lower()
    if host in {'youtu.be'} or host=='youtube.com' or host.endswith('.youtube.com'): source_key='youtube'
    elif host=='instagram.com' or host.endswith('.instagram.com'): source_key='instagram'
    elif host=='pin.it' or host=='pinterest.com' or host.endswith('.pinterest.com'): source_key='pinterest'
    elif host=='fb.watch' or host.endswith('.fb.watch') or host=='facebook.com' or host.endswith('.facebook.com'): source_key='facebook'
    elif host=='tiktok.com' or host.endswith('.tiktok.com'): source_key='tiktok'
    elif host=='redd.it' or host=='reddit.com' or host.endswith('.reddit.com'): source_key='reddit'
    elif host in {'x.com','twitter.com','www.twitter.com'}: source_key='x'
    elif host in {'vk.com','m.vk.com','vkvideo.ru','www.vkvideo.ru'}: source_key='vk'
    else: source_key='other'
    try:
        source_settings=await db.get_setting('source_enabled',{})
        maintenance=await db.get_setting('maintenance',False)
        downloads_enabled=await db.get_setting('downloads_enabled',True)
    except Exception:
        log.exception('Could not read runtime settings')
        CHECKING.discard(m.from_user.id)
        await status.edit_text({'ru':'Временная техническая ошибка. Попробуйте позже.','uz':'Vaqtinchalik texnik xato. Keyinroq urinib ko‘ring.','en':'Temporary technical error. Please try again later.'}[lang], reply_markup=InlineKeyboardMarkup(inline_keyboard=[])); return
    if isinstance(source_settings,dict) and source_settings.get(source_key) is False:
        CHECKING.discard(m.from_user.id)
        await status.edit_text({'ru':'Этот источник временно отключён администратором.','uz':'Bu manba administrator tomonidan vaqtincha o‘chirilgan.','en':'This source is temporarily disabled by the administrator.'}[u['language']], reply_markup=InlineKeyboardMarkup(inline_keyboard=[])); return
    if maintenance:
        CHECKING.discard(m.from_user.id)
        await status.edit_text({'ru':'Бот временно на обслуживании. Попробуйте позже.','uz':'Bot vaqtincha xizmatda. Keyinroq urinib ko‘ring.','en':'The bot is under maintenance. Please try later.'}[lang], reply_markup=InlineKeyboardMarkup(inline_keyboard=[])); return
    if not downloads_enabled:
        CHECKING.discard(m.from_user.id)
        await status.edit_text({'ru':'Загрузки временно отключены.','uz':'Yuklab olish vaqtincha o‘chirilgan.','en':'Downloads are temporarily disabled.'}[lang], reply_markup=InlineKeyboardMarkup(inline_keyboard=[])); return
    ok, reason=await validate_public_https_url(url)
    if not ok:
        CHECKING.discard(m.from_user.id)
        await status.edit_text(tr(lang,'bad_url'), reply_markup=InlineKeyboardMarkup(inline_keyboard=[])); return
    # Fast path: start downloading immediately. The old flow first called yt-dlp
    # for full metadata and then called it a second time for the actual download.
    # That doubled extractor work and delayed delivery by up to several seconds.
    uid=m.from_user.id
    key=uuid.uuid4().hex[:8]
    summary={'title':'Media','extractor':source_key,'duration':None,'ext':'','entries_count':0}
    pending={'key':key,'url':url,'summary':summary,'created':time.monotonic(),'message':status}
    PENDING[uid]=pending
    u=get_user(uid,m.from_user.full_name,m.from_user.username)
    cooldown=int(await db.get_setting('download_cooldown_seconds',settings.download_cooldown_seconds))
    now_epoch=time.time(); last_epoch=float(u.get('last_download',0) or 0)
    elapsed=(now_epoch-last_epoch) if last_epoch > 1_000_000_000 else cooldown+1
    if elapsed < cooldown:
        wait=max(1,int(cooldown-elapsed))
        PENDING.pop(uid,None)
        await status.edit_text(tr(lang,'cooldown',seconds=wait),reply_markup=InlineKeyboardMarkup(inline_keyboard=[]))
        return
    # Prefer a single-file video stream to avoid separate audio/video merging.
    mode='audio' if u.get('format')=='audio' else ('original' if u.get('format')=='original' else 'best')
    event=asyncio.Event(); ACTIVE[uid]=event; u['last_download']=now_epoch
    ACTIVE_STATUS[uid]=status
    task=asyncio.create_task(run_download(status,uid,pending,mode,event),name=f'mediamate-download-{uid}')
    ACTIVE_TASKS[uid]=task
    def cleanup_unstarted_download(done_task, user_id=uid):
        if done_task.cancelled():
            ACTIVE.pop(user_id,None); ACTIVE_TASKS.pop(user_id,None); ACTIVE_PROCESSES.pop(user_id,None); ACTIVE_STATUS.pop(user_id,None); PENDING.pop(user_id,None)
    task.add_done_callback(cleanup_unstarted_download)
    return

@router.message(F.caption)
async def caption_handler(m:Message):
    if not await allowed_user(m): return
    caption=(m.caption or '').strip()
    u=get_user(m.from_user.id,m.from_user.full_name,m.from_user.username); lang=u['language']
    if m.from_user.id in ticket_mode:
        ticket_mode.discard(m.from_user.id)
        media=m.photo[-1] if m.photo else (m.document or m.video or m.audio or m.voice)
        file_type='photo' if m.photo else ('document' if m.document else ('video' if m.video else ('audio' if m.audio else ('voice' if m.voice else None))))
        tid=await db.create_ticket(m.from_user.id,caption or '[вложение]',getattr(media,'file_id',None),file_type)
        if tid is None: return await m.answer({'ru':'У вас уже есть два открытых обращения. Дождитесь ответа.','uz':'Sizda ikkita ochiq murojaat bor. Javobni kuting.','en':'You already have two open tickets. Please wait for a reply.'}[lang])
        await m.answer(tr(u['language'],'support_created',ticket_id=tid))
        for aid in settings.admin_ids:
            try:
                await m.bot.send_message(aid,f'Ticket · Тикет #{tid} с вложением ({file_type})\nПользователь ID: {m.from_user.id}\nОтвет: /reply {tid} текст')
                if file_type=='photo': await m.bot.send_photo(aid,media.file_id,caption=f'Тикет #{tid}')
                elif file_type=='document': await m.bot.send_document(aid,media.file_id,caption=f'Тикет #{tid}')
                elif file_type=='video': await m.bot.send_video(aid,media.file_id,caption=f'Тикет #{tid}')
                elif file_type=='audio': await m.bot.send_audio(aid,media.file_id,caption=f'Тикет #{tid}')
                elif file_type=='voice': await m.bot.send_voice(aid,media.file_id,caption=f'Тикет #{tid}')
            except Exception: pass
        return
    match=re.search(r'https?://\S+',caption)
    if match:
        await process_link(m,match.group(0).rstrip(').,]'))

@router.message(F.photo | F.document | F.video | F.audio | F.voice)
async def support_attachment(m:Message):
    if m.from_user.id not in ticket_mode: return
    u=get_user(m.from_user.id,m.from_user.full_name,m.from_user.username); lang=u['language']
    if not await allowed_user(m): return
    ticket_mode.discard(m.from_user.id)
    file_id=None; file_type=None
    if m.photo: file_id=m.photo[-1].file_id; file_type='photo'
    elif m.document: file_id=m.document.file_id; file_type='document'
    elif m.video: file_id=m.video.file_id; file_type='video'
    elif m.audio: file_id=m.audio.file_id; file_type='audio'
    elif m.voice: file_id=m.voice.file_id; file_type='voice'
    body=m.caption or '[вложение]'
    tid=await db.create_ticket(m.from_user.id,body,file_id,file_type)
    if tid is None: await m.answer({'ru':'У вас уже есть два открытых обращения. Дождитесь ответа.','uz':'Sizda ikkita ochiq murojaat bor. Javobni kuting.','en':'You already have two open tickets. Please wait for a reply.'}[lang]); return
    await m.answer(tr(u['language'],'support_created',ticket_id=tid))
    for aid in settings.admin_ids:
        try:
            await m.bot.send_message(aid,f'Ticket · Тикет #{tid} с вложением ({file_type})\nПользователь ID: {m.from_user.id}\nОтвет: /reply {tid} текст')
            if file_type=='photo': await m.bot.send_photo(aid,file_id,caption=f'Тикет #{tid}')
            elif file_type=='document': await m.bot.send_document(aid,file_id,caption=f'Тикет #{tid}')
            elif file_type=='video': await m.bot.send_video(aid,file_id,caption=f'Тикет #{tid}')
            elif file_type=='audio': await m.bot.send_audio(aid,file_id,caption=f'Тикет #{tid}')
            elif file_type=='voice': await m.bot.send_voice(aid,file_id,caption=f'Тикет #{tid}')
        except Exception: pass

@router.callback_query(F.data=='subscription:check')
async def check_subscription(c:CallbackQuery):
    u=get_user(c.from_user.id,c.from_user.full_name,c.from_user.username); lang=u['language']
    try:
        member=await c.bot.get_chat_member(settings.channel_id,c.from_user.id)
        subscribed=_member_is_subscribed(member)
        if subscribed:
            u['subscription_confirmed']=True; await persist_user(c.from_user.id,u)
            await c.answer(tr(lang,'subscribed'),show_alert=True)
            try: await c.message.edit_text(tr(lang,'subscribed'), reply_markup=InlineKeyboardMarkup(inline_keyboard=[]))
            except Exception: pass
        else: await c.answer(tr(lang,'not_subscribed'),show_alert=True)
    except Exception:
        await c.answer(tr(lang,'sub_check_error'),show_alert=True)

@router.callback_query(F.data=='settings:quality')
async def settings_quality(c: CallbackQuery):
    u=get_user(c.from_user.id,c.from_user.full_name,c.from_user.username); lang=u['language']
    if not u.get('accepted'): return await c.answer(tr(lang,'rules_required'),show_alert=True)
    await c.answer(); await c.message.edit_text(tr(lang,'quality'),reply_markup=preference_keyboard(lang,'quality',u.get('quality','best')))

@router.callback_query(F.data=='settings:format')
async def settings_format(c: CallbackQuery):
    u=get_user(c.from_user.id,c.from_user.full_name,c.from_user.username); lang=u['language']
    if not u.get('accepted'): return await c.answer(tr(lang,'rules_required'),show_alert=True)
    await c.answer(); await c.message.edit_text(tr(lang,'format'),reply_markup=preference_keyboard(lang,'format',u.get('format','video')))

@router.callback_query(F.data.startswith('settings:set:'))
async def settings_set(c: CallbackQuery):
    u=get_user(c.from_user.id,c.from_user.full_name,c.from_user.username); lang=u['language']
    if not u.get('accepted'): return await c.answer(tr(lang,'rules_required'),show_alert=True)
    parts=c.data.split(':')
    if len(parts)!=4 or parts[2] not in {'quality','format'}: return await c.answer()
    kind,value=parts[2],parts[3]
    valid={'quality':{'best','q1080','q720','q480'},'format':{'video','audio','original'}}
    if value not in valid[kind]: return await c.answer()
    u[kind]=value; await persist_user(c.from_user.id,u); await c.answer(tr(lang,'setting_saved'))
    await c.message.edit_text(tr(lang,'settings_title'),reply_markup=settings_keyboard(lang,lang,u.get('quality','best'),u.get('format','video')))

@router.callback_query(F.data=='history:clear')
async def history_clear(c: CallbackQuery):
    u=get_user(c.from_user.id,c.from_user.full_name,c.from_user.username); lang=u['language']
    HISTORY[c.from_user.id].clear()
    await c.answer(tr(lang,'history_cleared'),show_alert=True)

@router.callback_query(F.data=='support:mine')
async def support_mine(c: CallbackQuery):
    u=get_user(c.from_user.id,c.from_user.full_name,c.from_user.username); lang=u['language']
    rows=await db.user_tickets(c.from_user.id,10)
    if not rows: return await c.answer(tr(lang,'tickets_empty'),show_alert=True)
    kb=[]; lines=[tr(lang,'support_menu')]
    for row in rows:
        status_key={'open':'ticket_status_open','in_progress':'ticket_status_in_progress','closed':'ticket_status_closed'}.get(row['status'],'ticket_status_open')
        lines.append(f"#{row['id']} · {tr(lang,status_key)}")
        if row['status']!='closed': kb.append([InlineKeyboardButton(text=f"#{row['id']}",callback_data=f"ticket:userclose:{row['id']}")])
    kb.append([InlineKeyboardButton(text=tr(lang,'back'),callback_data='menu:support')])
    await c.answer(); await c.message.edit_text('\n'.join(lines),reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@router.callback_query(F.data.startswith('ticket:userclose:'))
async def user_close_ticket(c: CallbackQuery):
    u=get_user(c.from_user.id,c.from_user.full_name,c.from_user.username); lang=u['language']
    try: tid=int(c.data.rsplit(':',1)[1])
    except ValueError: return await c.answer()
    row=await db.get_ticket(tid)
    if not row or row['telegram_id']!=c.from_user.id: return await c.answer(tr(lang,'ticket_not_found'),show_alert=True)
    if row['status']=='closed': return await c.answer(tr(lang,'ticket_not_open'),show_alert=True)
    await db.close_ticket(tid); await c.answer(tr(lang,'ticket_closed'),show_alert=True)
    await c.message.edit_text(tr(lang,'support_menu'),reply_markup=support_keyboard(lang))

@router.callback_query(F.data=='support:ticket')
async def support_ticket(c:CallbackQuery):
    u=get_user(c.from_user.id,c.from_user.full_name,c.from_user.username)
    if not u.get('accepted'):
        await c.answer(tr(u['language'],'rules_required'),show_alert=True); return
    ticket_mode.add(c.from_user.id); await c.answer()
    await c.message.edit_text(tr(u['language'],'support_prompt'),reply_markup=InlineKeyboardMarkup(inline_keyboard=[]))

@router.callback_query(F.data=='support:direct')
async def support_direct(c:CallbackQuery):
    u=get_user(c.from_user.id,c.from_user.full_name,c.from_user.username)
    await c.answer()
    await c.message.answer(tr(u['language'],'support_direct')+'\nhttps://t.me/'+settings.support_username)

@router.callback_query(F.data.startswith('lang:'))
async def change_lang(c: CallbackQuery):
    u=get_user(c.from_user.id,c.from_user.full_name)
    if not u['accepted']:
        await c.answer(tr(u['language'],'rules_required'),show_alert=True); return
    lang=c.data.split(':')[1]
    if lang not in ('ru','uz','en'): await c.answer(); return
    if u.get('language') == lang:
        await c.answer(tr(lang,'language_saved'))
        return
    u['language']=lang; u['language_chosen']=True
    await persist_user(c.from_user.id,u)
    await c.message.edit_text(tr(lang,'settings_title'),reply_markup=settings_keyboard(lang,lang,u.get('quality','best'),u.get('format','video')))
    await c.message.answer(tr(lang,'menu_updated'),reply_markup=main_menu(lang))
    await c.answer(tr(lang,'language_saved'))

@router.callback_query(F.data.in_({'menu:download','menu:profile','menu:history','menu:favorites','menu:support','menu:how','menu:settings'}))
async def menu_action(c: CallbackQuery):
    u=get_user(c.from_user.id,c.from_user.full_name,c.from_user.username); lang=u['language']
    if not u.get('accepted'):
        await c.answer(tr(lang,'rules_required'),show_alert=True); return
    if u.get('blocked') or c.from_user.id in BLOCKED:
        await c.answer(tr(lang,'blocked'),show_alert=True); return
    action=c.data.split(':',1)[1]
    await c.answer()
    if action=='download':
        return await c.message.answer(tr(lang,'send_link'),reply_markup=main_menu(lang))
    if action=='profile':
        return await c.message.answer(tr(lang,'profile',id=c.from_user.id,name=u['name'],language=language_name(lang),successes=u['successes'],first_seen=u['first_seen'],quality=u['quality'],format=u['format']),reply_markup=main_menu(lang))
    if action=='history':
        items=list(HISTORY[c.from_user.id])
        if not items: return await c.message.answer(tr(lang,'history_empty'),reply_markup=main_menu(lang))
        lines=[tr(lang,'history_title')]
        for i,item in enumerate(reversed(items),1):
            status_key={'success':'history_success','failed':'history_failed','processing':'history_processing','cancelled':'history_cancelled','interrupted':'history_interrupted'}.get(item['status'],'history_unknown')
            host=(urlsplit(item.get('url','')).hostname or 'media')[:70]
            lines.append(f"{i}. {host} — {tr(lang,status_key)}")
        return await c.message.answer('\n'.join(lines),reply_markup=main_menu(lang))
    if action=='favorites':
        return await c.message.answer(tr(lang,'favorites_empty'),reply_markup=main_menu(lang))
    if action=='support':
        return await c.message.edit_text(tr(lang,'support_menu'),reply_markup=support_keyboard(lang))
    if action=='how':
        return await c.message.answer(tr(lang,'how'),reply_markup=main_menu(lang))
    if action=='settings':
        return await c.message.edit_text(tr(lang,'settings_title'),reply_markup=settings_keyboard(lang,lang,u.get('quality','best'),u.get('format','video')))

async def show_favorites(message, uid: int, lang: str):
    return await message.answer(tr(lang, 'favorites_empty'), reply_markup=main_menu(lang))


@router.callback_query(F.data.startswith('history:favorite:'))
async def add_favorite_from_history(c: CallbackQuery):
    u=get_user(c.from_user.id,c.from_user.full_name,c.from_user.username)
    await c.answer(tr(u['language'],'favorites_empty'),show_alert=True)


@router.callback_query(F.data.startswith('favorite:remove:'))
async def remove_favorite_callback(c: CallbackQuery):
    u=get_user(c.from_user.id,c.from_user.full_name,c.from_user.username)
    await c.answer(tr(u['language'],'favorites_empty'),show_alert=True)


async def _callback_allowed(c: CallbackQuery, u: dict) -> bool:
    lang=u.get('language','ru')
    if not u.get('accepted'):
        await c.answer(tr(lang,'rules_required'),show_alert=True); return False
    if u.get('blocked') or c.from_user.id in BLOCKED:
        await c.answer(tr(lang,'blocked'),show_alert=True); return False
    return True


@router.callback_query(F.data=='menu:back')
async def back(c: CallbackQuery):
    u=get_user(c.from_user.id,c.from_user.full_name)
    if not u['accepted']:
        await c.answer(tr(u['language'],'rules_required'),show_alert=True); return
    await c.message.edit_text(tr(u['language'],'welcome'), reply_markup=InlineKeyboardMarkup(inline_keyboard=[])); await c.answer()

@router.callback_query(F.data.startswith('pickcancel:'))
async def pick_cancel(c: CallbackQuery):
    u=get_user(c.from_user.id)
    if not u['accepted']:
        await c.answer(tr(u['language'],'rules_required'),show_alert=True); return
    PENDING.pop(c.from_user.id,None)
    await c.message.edit_text(tr(u['language'],'cancelled_step'), reply_markup=InlineKeyboardMarkup(inline_keyboard=[])); await c.answer()

@router.callback_query(F.data.startswith('pick:'))
async def pick(c: CallbackQuery):
    uid=c.from_user.id; u=get_user(uid,c.from_user.full_name,c.from_user.username); lang=u['language']
    if u.get('blocked') or uid in BLOCKED:
        await c.answer(tr(lang,'blocked'),show_alert=True); return
    if not u['accepted']:
        await c.answer(tr(lang,'rules_required'),show_alert=True); return
    parts=c.data.split(':'); key=parts[1]; mode=parts[2]
    pending=PENDING.get(uid)
    if not pending or pending['key']!=key or time.monotonic()-pending['created']>900:
        await c.answer(tr(lang,'link_expired'),show_alert=True); return
    if uid in ACTIVE:
        await c.answer(tr(lang,'already_downloading'),show_alert=True); return
    cooldown=int(await db.get_setting('download_cooldown_seconds',settings.download_cooldown_seconds))
    # This timestamp is persisted in the profile, so use wall-clock time rather than
    # monotonic time (which resets across process restarts).
    now_epoch=time.time(); last_epoch=float(u.get('last_download',0) or 0)
    elapsed=(now_epoch-last_epoch) if last_epoch > 1_000_000_000 else cooldown+1
    if elapsed < cooldown:
        wait=max(1,int(cooldown-elapsed))
        await c.answer(tr(lang,'cooldown',seconds=wait),show_alert=True); return
    event=asyncio.Event(); ACTIVE[uid]=event; u['last_download']=now_epoch
    ACTIVE_STATUS[uid]=c.message
    task=asyncio.create_task(run_download(c.message,uid,pending,mode,event), name=f'mediamate-download-{uid}')
    ACTIVE_TASKS[uid]=task
    def cleanup_unstarted_download(done_task, user_id=uid):
        # If cancellation arrives before the coroutine's first instruction, its finally block won't run.
        if done_task.cancelled():
            ACTIVE.pop(user_id,None); ACTIVE_TASKS.pop(user_id,None); ACTIVE_PROCESSES.pop(user_id,None); ACTIVE_STATUS.pop(user_id,None); PENDING.pop(user_id,None)
    task.add_done_callback(cleanup_unstarted_download)
    await c.answer()

@router.callback_query(F.data=='download:cancel')
async def cancel_download(c: CallbackQuery):
    u=get_user(c.from_user.id,c.from_user.full_name,c.from_user.username)
    if not u.get('accepted'):
        await c.answer(tr(u['language'],'rules_required'),show_alert=True); return
    uid=c.from_user.id; event=ACTIVE.get(uid)
    if event is not None:
        event.set()
        proc=ACTIVE_PROCESSES.get(uid)
        if proc is not None and proc.returncode is None:
            try: proc.terminate()
            except ProcessLookupError: pass
        active_status=ACTIVE_STATUS.get(uid)
        task=ACTIVE_TASKS.get(uid)
        if task is not None and not task.done(): task.cancel()
        if active_status is not None:
            try: await active_status.edit_text(tr(u['language'],'download_cancelled'), reply_markup=InlineKeyboardMarkup(inline_keyboard=[]))
            except Exception: pass
        await c.answer(tr(u['language'],'download_cancelled'))
        return
    check_task=CHECKING_TASKS.get(uid)
    if check_task is not None and not check_task.done():
        check_task.cancel()
        check_status=CHECKING_STATUS.get(uid)
        if check_status is not None:
            try: await check_status.edit_text(tr(u['language'],'download_cancelled'), reply_markup=InlineKeyboardMarkup(inline_keyboard=[]))
            except Exception: pass
        CHECKING.discard(uid)
        await c.answer(tr(u['language'],'download_cancelled'))
        return
    pending=PENDING.pop(uid,None)
    if pending and pending.get('message') is not None:
        try: await pending['message'].edit_text(tr(u['language'],'cancelled_step'),reply_markup=InlineKeyboardMarkup(inline_keyboard=[]))
        except Exception: pass
        await c.answer(tr(u['language'],'cancelled_step'))
        return
    await c.answer(tr(u['language'],'no_active_download'),show_alert=True)

def _member_is_subscribed(member) -> bool:
    status=getattr(member, 'status', '')
    return status in {'member','administrator','creator'} or (status=='restricted' and bool(getattr(member,'is_member',False)))


async def offer_subscription_if_needed(bot: Bot, uid: int, user: dict, language: str):
    """Only advertise after the API confirms non-membership; unknown status means no ad."""
    if not settings.channel_id or not settings.channel_url or user.get('successes',0) < 2:
        return
    try:
        member=await bot.get_chat_member(settings.channel_id,uid)
    except Exception:
        log.info('Subscription status unavailable for user %s; suppressing invitation',uid)
        return
    if _member_is_subscribed(member):
        user['subscription_confirmed']=True
        await persist_user(uid,user)
        return
    now=int(time.time())
    last=int(user.get('last_subscription_offer_at',0) or 0)
    interval=max(0,int(settings.subscription_offer_interval_hours))*3600
    if now-last < interval:
        return
    user['last_subscription_offer_at']=now
    await persist_user(uid,user)
    try:
        await bot.send_message(uid,tr(language,'subscription_offer'),reply_markup=subscription_keyboard(language,settings.channel_url))
    except Exception:
        log.info('Could not send subscription invitation to user %s',uid,exc_info=True)


@router.chat_member()
async def channel_member_changed(event: ChatMemberUpdated, bot: Bot):
    """Notify only on an actual membership-to-nonmembership transition."""
    if not settings.channel_id or not settings.channel_url: return
    configured=str(settings.channel_id).strip()
    is_target = str(event.chat.id)==configured or (event.chat.username and event.chat.username.casefold()==configured.lstrip('@').casefold())
    if not is_target and configured.startswith('@'):
        try: is_target=(event.chat.username or '').casefold()==configured[1:].casefold()
        except Exception: is_target=False
    if not is_target: return
    before=_member_is_subscribed(event.old_chat_member)
    after=_member_is_subscribed(event.new_chat_member)
    if not before or after: return
    uid=event.new_chat_member.user.id
    user=USERS.get(uid)
    if not user: return
    lang=user.get('language','ru')
    try:
        await bot.send_message(uid,tr(lang,'subscription_left_notice'),reply_markup=subscription_keyboard(lang,settings.channel_url))
    except Exception:
        log.info('Could not send unsubscribe reminder to user %s',uid,exc_info=True)


def _session_history_url(url: str) -> str:
    """Keep only the source host/path in transient session memory; discard query strings."""
    parsed=urlsplit(sanitize_url(url))
    return (parsed.scheme + '://' + parsed.netloc + parsed.path)[:500]


async def run_download(message,uid,pending,mode,event):
    u=get_user(uid); lang=u['language']; jobdir=settings.temp_dir/str(uid)/uuid.uuid4().hex
    jobdir.mkdir(parents=True,exist_ok=True); status=None; last_edit=0.0
    try:
        # Reuse the exact media-summary message the user clicked: no extra status-message chain.
        status=message
        try:
            await status.edit_text('🚀 ' + tr(lang,'download_started'),reply_markup=cancel_download_keyboard(lang))
        except Exception:
            status=await message.answer(tr(lang,'download_started'),reply_markup=cancel_download_keyboard(lang))
        ACTIVE_STATUS[uid]=status
        acquired=False
        while not event.is_set():
            try:
                await asyncio.wait_for(download_semaphore.acquire(),timeout=0.25)
                acquired=True
                break
            except asyncio.TimeoutError:
                continue
        if event.is_set(): raise DownloadCancelled('cancelled while waiting for a slot')
        async def progress(data):
            nonlocal last_edit
            now=time.monotonic()
            if now-last_edit<2: return
            last_edit=now
            try: await status.edit_text(tr(lang,'progress',percent=data.get('percent','?'),speed=data.get('speed',''),eta=data.get('eta','')),reply_markup=cancel_download_keyboard(lang))
            except Exception: pass
        proc_box={'proc':None}
        def on_process(proc):
            proc_box['proc']=proc
            if proc is None: ACTIVE_PROCESSES.pop(uid,None)
            else: ACTIVE_PROCESSES[uid]=proc
        download_started=time.monotonic()
        set_runtime_file_size_mb(int(await db.get_setting('max_file_size_mb',settings.max_file_size_mb)))
        paths=await download_media(pending['url'],mode,jobdir,progress,on_process,settings.download_timeout_seconds,event)
        if event.is_set(): raise DownloadCancelled('cancelled')
        if status:
            try: await status.edit_text('📤 ' + tr(lang,'processing'), reply_markup=InlineKeyboardMarkup(inline_keyboard=[]))
            except Exception: pass
        if event.is_set(): raise DownloadCancelled('cancelled')
        # Batch photos/videos into albums when Telegram accepts the combination.
        media_group=[]
        all_groupable = 2 <= len(paths) <= 10 and all(p.suffix.lower() in {'.jpg','.jpeg','.png','.mp4'} for p in paths) and mode != 'audio'
        if all_groupable:
            for path in paths:
                upload=FSInputFile(path,filename=path.name)
                if path.suffix.lower() in {'.jpg','.jpeg','.png'}: media_group.append(InputMediaPhoto(media=upload))
                else: media_group.append(InputMediaVideo(media=upload,supports_streaming=True))
            if event.is_set(): raise DownloadCancelled('cancelled')
            await message.answer_media_group(media=media_group)
        else:
            for path in paths:
                if event.is_set(): raise DownloadCancelled('cancelled')
                file=FSInputFile(path,filename=path.name)
                suffix=path.suffix.lower()
                if mode=='audio' or suffix in {'.mp3','.m4a','.ogg','.opus','.wav'}: await message.answer_audio(file)
                elif suffix in {'.jpg','.jpeg','.png'}: await message.answer_photo(file)
                elif suffix in {'.mp4','.m4v','.mov','.webm'}: await message.answer_video(file,supports_streaming=True)
                else: await message.answer_document(file)
        u['successes']+=1; STATS['downloads_ok']+=1
        try:
            await persist_user(uid,u)
            await db.record_download(uid,'','success',str(pending.get('summary',{}).get('extractor','')),str(pending.get('summary',{}).get('ext','')),int((time.monotonic()-download_started)*1000))
        except Exception:
            log.exception('Media was sent, but success metrics could not be persisted')
            if admin_bot:
                for aid in settings.admin_ids:
                    try: await admin_bot.send_message(aid,f'DB write failed after successful delivery. User ID: {uid}')
                    except Exception: pass
        if u['successes']==1 and admin_bot:
            for aid in settings.admin_ids:
                try: await admin_bot.send_message(aid, f"Первая загрузка нового пользователя\nID: {uid}\nИмя: {u.get('name','—')}\nИсточник: {pending.get('summary',{}).get('extractor','—')}\nМедиа: {pending.get('summary',{}).get('title','—')}")
                except Exception: pass
        HISTORY[uid].append({'url':_session_history_url(pending['url']),'status':'success','at':time.time()})
        await send_brand_sticker(message.bot, uid, 'audio' if mode=='audio' else 'done')
        if status:
            try: await status.edit_text(tr(lang,'download_ok'), reply_markup=InlineKeyboardMarkup(inline_keyboard=[]))
            except Exception: pass
        await offer_subscription_if_needed(message.bot,uid,u,lang)
    except (DownloadCancelled, asyncio.CancelledError):
        HISTORY[uid].append({'url':_session_history_url(pending['url']),'status':'cancelled','at':time.time()})
        if status:
            try: await status.edit_text(tr(lang,'download_cancelled'), reply_markup=InlineKeyboardMarkup(inline_keyboard=[]))
            except Exception: pass
    except Exception as exc:
        STATS['downloads_failed']+=1
        try: await db.record_download(uid,'','failed',str(pending.get('summary',{}).get('extractor','')),str(pending.get('summary',{}).get('ext','')),int((time.monotonic()-download_started)*1000) if 'download_started' in locals() else 0)
        except Exception: log.exception('Could not persist failed download')
        HISTORY[uid].append({'url':_session_history_url(pending['url']),'status':'failed','at':time.time()})
        log.exception('Download failed for user %s (%s)',uid,type(exc).__name__)
        if status:
            try:
                message_key = 'youtube_limited' if is_youtube_bot_check_error(str(exc)) and is_youtube_host(urlsplit(pending.get('url','')).hostname or '') else 'download_failed_generic'
                await status.edit_text(tr(lang,message_key), reply_markup=InlineKeyboardMarkup(inline_keyboard=[]))
                if message_key == 'youtube_limited': await notify_youtube_restriction(str(exc))
            except Exception: pass
        if admin_bot:
            for aid in settings.admin_ids:
                try: await admin_bot.send_message(aid,f'MediaMate download error\nUser: {uid}\nError type: {type(exc).__name__}')
                except Exception: pass
    finally:
        if locals().get('acquired', False):
            try: download_semaphore.release()
            except ValueError: pass
        ACTIVE.pop(uid,None); ACTIVE_PROCESSES.pop(uid,None); ACTIVE_STATUS.pop(uid,None); PENDING.pop(uid,None)
        current=asyncio.current_task()
        if ACTIVE_TASKS.get(uid) is current: ACTIVE_TASKS.pop(uid,None)
        remove_temp_dir(jobdir)
        try:
            parent=jobdir.parent
            if parent.exists() and not any(parent.iterdir()): parent.rmdir()
        except OSError: pass
