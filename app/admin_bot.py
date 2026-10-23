from __future__ import annotations
import asyncio, logging, time, html, json, tempfile, os
from io import BytesIO
from aiogram import Router, F, Bot
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile, FSInputFile
from app.config import settings
from app import database as db
from app.state import ADMIN_SESSIONS, USERS, BLOCKED, ACTIVE, ACTIVE_PROCESSES, ACTIVE_TASKS
from app.ytdlp_runtime import current_version, update_ytdlp
router=Router(); log=logging.getLogger(__name__); user_bot:Bot|None=None
def set_user_bot(bot:Bot):
 global user_bot; user_bot=bot
_sessions: dict[int,float]={}; _bad_attempts:dict[int,tuple[int,float]]={}; _broadcast_preview:dict[int,str]={}; _admin_lang:dict[int,str]={}
class Flow(StatesGroup): password=State(); broadcast=State(); send_one=State(); search_user=State(); ticket_reply=State()
ADMIN_UI={
 'ru':{'stats':'Статистика','backup':'Резервная копия','users':'Пользователи','tickets':'Тикеты','report':'Отчёт','broadcast':'Рассылка','sendone':'Одному пользователю','control':'Управление ботом','sources':'Источники','limits':'Лимиты','search':'Найти пользователя','diag':'Диагностика','update':'Обновить yt-dlp','downloads':'Загрузки','audit':'Аудит','recent':'Последние пользователи','logout':'Выйти','language':'Язык','home':'Меню','on':'Включить загрузки','off':'Отключить загрузки','maint_on':'Включить обслуживание','maint_off':'Отключить обслуживание','choose_lang':'Выберите язык панели:'},
 'uz':{'stats':'Statistika','backup':'Zaxira nusxasi','users':'Foydalanuvchilar','tickets':'Murojaatlar','report':'Hisobot','broadcast':'Xabar tarqatish','sendone':'Bitta foydalanuvchiga','control':'Bot boshqaruvi','sources':'Manbalar','limits':'Cheklovlar','search':'Foydalanuvchini topish','diag':'Diagnostika','update':'yt-dlp yangilash','downloads':'Yuklashlar','audit':'Audit','recent':'So‘nggi foydalanuvchilar','logout':'Chiqish','language':'Til','home':'Menyu','on':'Yuklashni yoqish','off':'Yuklashni o‘chirish','maint_on':'Texnik xizmatni yoqish','maint_off':'Texnik xizmatni o‘chirish','choose_lang':'Panel tilini tanlang:'},
 'en':{'stats':'Statistics','backup':'Database backup','users':'Users','tickets':'Tickets','report':'Report','broadcast':'Broadcast','sendone':'Message one user','control':'Bot controls','sources':'Sources','limits':'Limits','search':'Find user','diag':'Diagnostics','update':'Update yt-dlp','downloads':'Downloads','audit':'Audit log','recent':'Recent users','logout':'Log out','language':'Language','home':'Menu','on':'Enable downloads','off':'Disable downloads','maint_on':'Enable maintenance','maint_off':'Disable maintenance','choose_lang':'Choose panel language:'},
}
def al(uid:int,key:str)->str:
 lang=_admin_lang.get(uid,'ru'); return ADMIN_UI.get(lang,ADMIN_UI['ru']).get(key,ADMIN_UI['ru'].get(key,key))

def panel(lang='ru'):
 t=ADMIN_UI.get(lang,ADMIN_UI['ru'])
 return InlineKeyboardMarkup(inline_keyboard=[
 [InlineKeyboardButton(text=t['stats'],callback_data='adm:stats'),InlineKeyboardButton(text=t['users'],callback_data='adm:users')],
 [InlineKeyboardButton(text=t['backup'],callback_data='adm:backup')],
 [InlineKeyboardButton(text=t['tickets'],callback_data='adm:tickets'),InlineKeyboardButton(text=t['report'],callback_data='adm:report')],
 [InlineKeyboardButton(text=t['broadcast'],callback_data='adm:broadcast'),InlineKeyboardButton(text=t['sendone'],callback_data='adm:sendone')],
 [InlineKeyboardButton(text=t['control'],callback_data='adm:control'),InlineKeyboardButton(text=t['sources'],callback_data='adm:sources')],
 [InlineKeyboardButton(text=t['limits'],callback_data='adm:limits'),InlineKeyboardButton(text=t['search'],callback_data='adm:search')],
 [InlineKeyboardButton(text=t['diag'],callback_data='adm:ytdlp_status'),InlineKeyboardButton(text=t['update'],callback_data='adm:ytdlp_update')],
 [InlineKeyboardButton(text=t['downloads'],callback_data='adm:recentdownloads'),InlineKeyboardButton(text=t['audit'],callback_data='adm:audit')],
 [InlineKeyboardButton(text=t['recent'],callback_data='adm:recent'),InlineKeyboardButton(text=t['language'],callback_data='adm:language')],
 [InlineKeyboardButton(text=t['logout'],callback_data='adm:logout')]])

def control_keyboard(maintenance:bool,enabled:bool,lang='ru'):
 t=ADMIN_UI.get(lang,ADMIN_UI['ru'])
 return InlineKeyboardMarkup(inline_keyboard=[
 [InlineKeyboardButton(text=(t['on'] if not enabled else t['off']),callback_data='adm:toggle_downloads')],
 [InlineKeyboardButton(text=(t['maint_off'] if maintenance else t['maint_on']),callback_data='adm:toggle_maintenance')],
 [InlineKeyboardButton(text=t['sources'],callback_data='adm:sources'),InlineKeyboardButton(text=t['limits'],callback_data='adm:limits')],
 [InlineKeyboardButton(text=t['home'],callback_data='adm:home')]])

def source_keyboard(values,lang='ru'):
 sources=[('youtube','YouTube'),('instagram','Instagram'),('tiktok','TikTok'),('pinterest','Pinterest'),('facebook','Facebook'),('x','X / Twitter'),('reddit','Reddit'),('vk','VK'),('other',{'ru':'Другие ссылки','uz':'Boshqa havolalar','en':'Other links'}.get(lang,'Другие ссылки'))]
 rows=[]
 for key,label in sources:
  enabled=values.get(key,True) if isinstance(values,dict) else True
  rows.append([InlineKeyboardButton(text=('✓ ON · ' if enabled else '× OFF · ')+label,callback_data=f'adm:source:{key}')])
 rows.append([InlineKeyboardButton(text=ADMIN_UI.get(lang,ADMIN_UI['ru'])['home'],callback_data='adm:home')])
 return InlineKeyboardMarkup(inline_keyboard=rows)

def limits_keyboard(lang='ru'):
 return InlineKeyboardMarkup(inline_keyboard=[
 [InlineKeyboardButton(text='20 MB',callback_data='adm:limit:file_mb:20'),InlineKeyboardButton(text='30 MB',callback_data='adm:limit:file_mb:30'),InlineKeyboardButton(text='45 MB',callback_data='adm:limit:file_mb:45')],
 [InlineKeyboardButton(text='30 min',callback_data='adm:limit:duration_min:30'),InlineKeyboardButton(text='60 min',callback_data='adm:limit:duration_min:60'),InlineKeyboardButton(text='120 min',callback_data='adm:limit:duration_min:120')],
 [InlineKeyboardButton(text='0 s',callback_data='adm:limit:cooldown:0'),InlineKeyboardButton(text='5 s',callback_data='adm:limit:cooldown:5'),InlineKeyboardButton(text='10 s',callback_data='adm:limit:cooldown:10')],
 [InlineKeyboardButton(text=ADMIN_UI.get(lang,ADMIN_UI['ru'])['home'],callback_data='adm:home')]])

def ticket_keyboard(ticket_id:int, status:str, lang='ru'):
 rows=[]
 if status!='closed': rows.append([InlineKeyboardButton(text={'ru':'Ответить','uz':'Javob berish','en':'Reply'}.get(lang,'Ответить'),callback_data=f'adm:ticketreply:{ticket_id}'),InlineKeyboardButton(text={'ru':'Закрыть','uz':'Yopish','en':'Close'}.get(lang,'Закрыть'),callback_data=f'adm:ticketclose:{ticket_id}')])
 rows.append([InlineKeyboardButton(text={'ru':'← К тикетам','uz':'← Murojaatlarga','en':'← Tickets'}.get(lang,'← К тикетам'),callback_data='adm:tickets'),InlineKeyboardButton(text=ADMIN_UI.get(lang,ADMIN_UI['ru'])['home'],callback_data='adm:home')])
 return InlineKeyboardMarkup(inline_keyboard=rows)

def authed(uid:int)->bool:
 if uid not in settings.admin_ids or _sessions.get(uid,0)<=time.monotonic(): return False
 _sessions[uid]=time.monotonic()+1800
 return True
async def deny(m:Message): await m.answer('Доступ запрещён.')
async def dashboard(m:Message):
 s=await db.stats(); maint=await db.get_setting('maintenance',False); enabled=await db.get_setting('downloads_enabled',True)
 lang=_admin_lang.get(m.from_user.id,'ru')
 if lang=='uz':
  body=('<b>MediaMate Admin</b>\n\n'+f'Foydalanuvchilar: <b>{s["users"]}</b>\n24 soatda faol: <b>{s["active_24h"]}</b>\n'+f'Muvaffaqiyatli yuklashlar: <b>{s["ok"]}</b>\nXatolar: <b>{s["failed"]}</b>\nOchiq murojaatlar: <b>{s["open_tickets"]}</b>\n\n'+f'Yuklashlar: {"yoqilgan" if enabled else "o‘chirilgan"}\nTexnik xizmat: {"yoqilgan" if maint else "o‘chirilgan"}')
 elif lang=='en':
  body=('<b>MediaMate Admin</b>\n\n'+f'Users: <b>{s["users"]}</b>\nActive in 24h: <b>{s["active_24h"]}</b>\n'+f'Successful downloads: <b>{s["ok"]}</b>\nFailures: <b>{s["failed"]}</b>\nOpen tickets: <b>{s["open_tickets"]}</b>\n\n'+f'Downloads: {"enabled" if enabled else "disabled"}\nMaintenance: {"enabled" if maint else "disabled"}')
 else:
  body=('<b>MediaMate Admin</b>\n\n'+f'Пользователей: <b>{s["users"]}</b>\nАктивны за 24 ч: <b>{s["active_24h"]}</b>\n'+f'Успешных загрузок: <b>{s["ok"]}</b>\nОшибок: <b>{s["failed"]}</b>\nОткрытых тикетов: <b>{s["open_tickets"]}</b>\n\n'+f'Загрузки: {"включены" if enabled else "отключены"}\nОбслуживание: {"включено" if maint else "выключено"}')
 await m.answer(body,parse_mode='HTML',reply_markup=panel(lang))
@router.message(CommandStart())
async def start(m:Message,state:FSMContext):
 uid=m.from_user.id
 if uid not in settings.admin_ids: return await deny(m)
 if uid not in _admin_lang:
  stored_lang=await db.get_setting(f'admin_language:{uid}','ru')
  _admin_lang[uid]=stored_lang if stored_lang in {'ru','uz','en'} else 'ru'
 count,until=_bad_attempts.get(uid,(0,0))
 if until>time.monotonic(): return await m.answer(f'Слишком много попыток. Повторите через {int(until-time.monotonic())} сек.')
 await state.set_state(Flow.password); await m.answer('Введите код доступа администратора. Сообщение с кодом будет удалено, если Telegram это позволит.')
@router.message(Flow.password)
async def password(m:Message,state:FSMContext):
 uid=m.from_user.id
 try: await m.delete()
 except Exception: pass
 if uid not in settings.admin_ids: await state.clear(); return
 if (m.text or '').strip()!=settings.admin_access_code:
  count,until=_bad_attempts.get(uid,(0,0)); count+=1
  delay=min(900,5*(2**min(count-1,7))); _bad_attempts[uid]=(count,time.monotonic()+delay)
  await state.clear(); return await m.answer(f'× Неверный код. Новая попытка через {delay} сек.')
 _bad_attempts.pop(uid,None); _sessions[uid]=time.monotonic()+1800; ADMIN_SESSIONS.add(uid); await state.clear(); await db.audit(uid,'login'); await dashboard(m)
@router.message(Command('backup'))
async def backup_cmd(m:Message):
 if not authed(m.from_user.id): return await m.answer('Authenticate first with /start.')
 try:
  with tempfile.TemporaryDirectory(prefix='mediamate-backup-') as tmp:
   path=await db.backup_database(os.path.join(tmp,'mediamate-backup.sqlite3'))
   await m.answer_document(FSInputFile(path),caption='MediaMate SQLite backup. Contains user data; store securely.')
  await db.audit(m.from_user.id,'database_backup')
 except Exception as exc:
  log.exception('Database backup failed')
  await m.answer(f'Backup failed ({type(exc).__name__}).')

@router.message(Command('logout'))
async def logout(m:Message):
 _sessions.pop(m.from_user.id,None); ADMIN_SESSIONS.discard(m.from_user.id); await m.answer('Вы вышли из панели.')
@router.message(Command('panel'))
@router.message(Command('stats'))
async def panel_cmd(m:Message):
 if not authed(m.from_user.id): return await m.answer('Сессия истекла. Выполните /start.')
 _sessions[m.from_user.id]=time.monotonic()+1800; await dashboard(m)
@router.callback_query(F.data.startswith('adm:') & ~F.data.in_({'adm:confirm_broadcast','adm:cancel_broadcast'}))
async def callbacks(c:CallbackQuery,state:FSMContext,bot:Bot):
 uid=c.from_user.id
 if not authed(uid): return await c.answer('Сессия истекла. Выполните /start.',show_alert=True)
 _sessions[uid]=time.monotonic()+1800; action=c.data.split(':',1)[1]; await c.answer()
 if action=='logout': _sessions.pop(uid,None); ADMIN_SESSIONS.discard(uid); return await c.message.answer({'ru':'Вы вышли из панели.','uz':'Paneldan chiqdingiz.','en':'You have logged out.'}.get(_admin_lang.get(uid,'ru'),'Вы вышли из панели.'))
 if action=='language':
  kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='🇷🇺 Русский',callback_data='adm:lang:ru')],[InlineKeyboardButton(text='🇺🇿 O‘zbekcha',callback_data='adm:lang:uz')],[InlineKeyboardButton(text='🇬🇧 English',callback_data='adm:lang:en')],[InlineKeyboardButton(text=al(uid,'home'),callback_data='adm:home')]])
  return await c.message.answer(al(uid,'choose_lang'),reply_markup=kb)
 if action.startswith('lang:'):
  lang=action.split(':',1)[1]
  if lang not in {'ru','uz','en'}: return
  _admin_lang[uid]=lang; await db.set_setting(f'admin_language:{uid}',lang); await db.audit(uid,'admin_language',{'language':lang})
  return await dashboard(c.message)
 if action=='home': return await dashboard(c.message)
 if action=='stats': return await dashboard(c.message)
 if action=='backup':
  try:
   await c.message.answer({'ru':'Подготовка резервной копии базы…','uz':'Ma’lumotlar bazasi zaxirasi tayyorlanmoqda…','en':'Preparing a database backup…'}.get(_admin_lang.get(uid,'ru'),'Preparing backup…'))
   with tempfile.TemporaryDirectory(prefix='mediamate-backup-') as tmp:
    path=await db.backup_database(os.path.join(tmp,'mediamate-backup.sqlite3'))
    if user_bot is None: return await c.message.answer('Backup prepared, but document delivery is unavailable.')
    from aiogram.types import FSInputFile
    await user_bot.send_document(uid, FSInputFile(path), caption='MediaMate SQLite backup. Contains user data; store securely.')
   await db.audit(uid,'database_backup')
   return await c.message.answer({'ru':'Резервная копия отправлена в этот чат. Храните её безопасно.','uz':'Zaxira nusxasi shu chatga yuborildi. Uni xavfsiz saqlang.','en':'Backup sent to this chat. Store it securely.'}.get(_admin_lang.get(uid,'ru'),'Backup sent.'))
  except Exception as exc:
   log.exception('Database backup failed')
   return await c.message.answer(f'Backup failed ({type(exc).__name__}).')
 if action=='sources':
  values=await db.get_setting('source_enabled',{})
  return await c.message.answer({'ru':'<b>Источники загрузки</b>\nНажмите на источник, чтобы включить или отключить его.','uz':'<b>Yuklash manbalari</b>\nManbani yoqish yoki o‘chirish uchun bosing.','en':'<b>Download sources</b>\nTap a source to enable or disable it.'}.get(_admin_lang.get(uid,'ru'),'<b>Источники загрузки</b>\nНажмите на источник, чтобы включить или отключить его.'),parse_mode='HTML',reply_markup=source_keyboard(values,_admin_lang.get(uid,'ru')))
 if action=='sources_stats':
  rows=await db.source_stats(7)
  if not rows: return await c.message.answer('За последние 7 дней загрузок нет.',reply_markup=source_keyboard(await db.get_setting('source_enabled',{}),_admin_lang.get(uid,'ru')))
  lines=['<b>Источники за 7 дней</b>']
  for r in rows: lines.append(f"{html.escape(r['source'])}: ✓{r['ok']} · ✕{r['failed']} · всего {r['total']}")
  return await c.message.answer('\n'.join(lines),parse_mode='HTML',reply_markup=source_keyboard(await db.get_setting('source_enabled',{}),_admin_lang.get(uid,'ru')))
 if action=='limits':
  max_mb=await db.get_setting('max_file_size_mb',settings.max_file_size_mb)
  max_seconds=await db.get_setting('max_video_duration_seconds',settings.max_video_duration)
  cooldown=await db.get_setting('download_cooldown_seconds',settings.download_cooldown_seconds)
  return await c.message.answer({'ru':f'<b>Лимиты</b>\nФайл: {max_mb} MB\nДлительность: {int(max_seconds)//60} мин\nПауза: {cooldown} сек\n\nВыберите новое значение:','uz':f'<b>Cheklovlar</b>\nFayl: {max_mb} MB\nDavomiyligi: {int(max_seconds)//60} daqiqa\nTanaffus: {cooldown} soniya\n\nYangi qiymatni tanlang:','en':f'<b>Limits</b>\nFile: {max_mb} MB\nDuration: {int(max_seconds)//60} min\nCooldown: {cooldown} sec\n\nChoose a new value:'}.get(_admin_lang.get(uid,'ru'),'<b>Лимиты</b>'),parse_mode='HTML',reply_markup=limits_keyboard(_admin_lang.get(uid,'ru')))
 if action.startswith('source:'):
  source=action.split(':',1)[1]; values=await db.get_setting('source_enabled',{})
  if not isinstance(values,dict): values={}
  values[source]=not values.get(source,True); await db.set_setting('source_enabled',values)
  await db.audit(uid,'source_toggle',{'source':source,'enabled':values[source]})
  return await c.message.edit_reply_markup(reply_markup=source_keyboard(values,_admin_lang.get(uid,'ru')))
 if action.startswith('limit:'):
  parts=action.split(':')
  if len(parts)!=3: return
  key,value=parts[1],int(parts[2])
  max_mb=2000 if settings.telegram_api_base_url else 45
  if key=='file_mb' and 1<=value<=max_mb: await db.set_setting('max_file_size_mb',value)
  elif key=='duration_min' and 1<=value<=240: await db.set_setting('max_video_duration_seconds',value*60)
  elif key=='cooldown' and 0<=value<=120: await db.set_setting('download_cooldown_seconds',value)
  else: return await c.message.answer('Значение недопустимо для текущего хостинга.')
  await db.audit(uid,'set_limit',{'key':key,'value':value})
  return await c.message.edit_text(f'Лимит обновлён: {key} = {value}.\nВыберите другое значение или вернитесь в меню.',reply_markup=limits_keyboard(_admin_lang.get(uid,'ru')))
 if action.startswith('user:'):
  try: target=int(action.split(':',1)[1])
  except ValueError: return
  rows=await db.find_users(str(target),1)
  if not rows: return await c.message.answer('Пользователь не найден.')
  r=rows[0]; p=r.get('profile') or {}; blocked=bool(r['blocked'])
  kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=({'ru':('Разблокировать' if blocked else '× Заблокировать'),'uz':('Blokdan chiqarish' if blocked else '× Bloklash'),'en':('Unblock' if blocked else '× Block')}.get(_admin_lang.get(uid,'ru'),'× Заблокировать')),callback_data=f"adm:{'unblock' if blocked else 'block'}:{target}")],[InlineKeyboardButton(text='← Пользователи',callback_data='adm:recent'),InlineKeyboardButton(text='Меню',callback_data='adm:home')]])
  return await c.message.answer(f"<b>{html.escape(r['name'])}</b>\nID: <code>{target}</code>\nUsername: @{html.escape(r['username'] or '—')}\nЯзык: {r['language']}\nУспешных загрузок: {p.get('successes',0) if isinstance(p,dict) else 0}\nЗаблокирован: {'да' if blocked else 'нет'}",parse_mode='HTML',reply_markup=kb)
 if action.startswith('block:') or action.startswith('unblock:'):
  parts=action.split(':')
  if len(parts)!=2 or not parts[1].isdigit(): return
  target=int(parts[1]); blocked=parts[0]=='block'; await db.set_blocked(target,blocked)
  if target in USERS: USERS[target]['blocked']=blocked
  if blocked: BLOCKED.add(target); ACTIVE[target].set() if target in ACTIVE else None
  else: BLOCKED.discard(target)
  await db.audit(uid,parts[0],{'user_id':target})
  try:
   if user_bot: await user_bot.send_message(target,'Ваш доступ к MediaMate ограничен администратором.' if blocked else 'Доступ к MediaMate восстановлен.')
  except Exception: pass
  rows=await db.find_users(str(target),1)
  if rows:
   r=rows[0]; p=r.get('profile') or {}; kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=({'ru':('Разблокировать' if blocked else '× Заблокировать'),'uz':('Blokdan chiqarish' if blocked else '× Bloklash'),'en':('Unblock' if blocked else '× Block')}.get(_admin_lang.get(uid,'ru'),'× Заблокировать')),callback_data=f"adm:{'unblock' if blocked else 'block'}:{target}")],[InlineKeyboardButton(text='← Пользователи',callback_data='adm:recent'),InlineKeyboardButton(text='Меню',callback_data='adm:home')]])
   return await c.message.edit_text(f"<b>{html.escape(r['name'])}</b>\nID: <code>{target}</code>\nUsername: @{html.escape(r['username'] or '—')}\nЗаблокирован: {'да' if blocked else 'нет'}",parse_mode='HTML',reply_markup=kb)
 if action.startswith('ticket:'):
  try: tid=int(action.split(':',1)[1])
  except ValueError: return
  ticket=await db.get_ticket(tid)
  if not ticket: return await c.message.answer('Тикет не найден.')
  lines=[f"<b>Тикет #{tid}</b>",f"Пользователь: {html.escape(ticket['name'])} · <code>{ticket['telegram_id']}</code>",f"Статус: {html.escape(ticket['status'])}",'']
  for msgrow in ticket['messages'][-8:]:
   sender='Пользователь' if msgrow['sender_type']=='user' else 'Поддержка'
   lines.append(f"<b>{sender}:</b> {html.escape(msgrow['body'][:800])}")
  return await c.message.answer('\n'.join(lines),parse_mode='HTML',reply_markup=ticket_keyboard(tid,ticket['status'],_admin_lang.get(uid,'ru')))
 if action.startswith('ticketreply:'):
  try: tid=int(action.split(':',1)[1])
  except ValueError: return
  ticket=await db.get_ticket(tid)
  if not ticket or ticket['status']=='closed': return await c.message.answer('Тикет закрыт или не найден.')
  await state.set_state(Flow.ticket_reply); await state.update_data(ticket_id=tid)
  return await c.message.answer(f'Отправьте ответ для тикета #{tid} одним сообщением. Отмена: /cancel')
 if action.startswith('ticketclose:'):
  try: tid=int(action.split(':',1)[1])
  except ValueError: return
  row=await db.close_ticket(tid)
  if not row: return await c.message.answer('Тикет не найден.')
  try:
   if user_bot: await user_bot.send_message(row['telegram_id'],f'Обращение #{tid} закрыто поддержкой.')
  except Exception: pass
  await db.audit(uid,'close_ticket',{'ticket_id':tid})
  return await c.message.answer(f'Тикет #{tid} закрыт.',reply_markup=panel(_admin_lang.get(uid,'ru')))
 if action=='recentdownloads':
  rows=await db.recent_downloads(15)
  if not rows: return await c.message.answer('Сводных данных о загрузках пока нет.',reply_markup=panel(_admin_lang.get(uid,'ru')))
  lines=['<b>Сводка загрузок по источникам за 7 дней</b>']
  for r in rows: lines.append(f"{r['created_at']:%m-%d %H:%M} · {html.escape(r['name'][:30])} · {html.escape(r['source'] or 'unknown')} · {html.escape(r['status'])}")
  return await c.message.answer('\n'.join(lines),parse_mode='HTML',reply_markup=panel(_admin_lang.get(uid,'ru')))
 if action=='audit':
  rows=await db.recent_audit(15)
  if not rows: return await c.message.answer('Журнал действий пока пуст.',reply_markup=panel(_admin_lang.get(uid,'ru')))
  lines=['<b>Последние действия</b>']
  for r in rows: lines.append(f"{r['created_at']:%Y-%m-%d %H:%M} · ID {r['admin_id']} · {html.escape(r['action'])}")
  return await c.message.answer('\n'.join(lines),parse_mode='HTML',reply_markup=panel(_admin_lang.get(uid,'ru')))
 if action=='ytdlp_status':
  try:
   deno=await asyncio.create_subprocess_exec('deno','--version',stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.STDOUT)
   out,_=await asyncio.wait_for(deno.communicate(),timeout=5)
   deno_status=out.decode('utf-8','replace').splitlines()[0] if deno.returncode==0 and out else 'не найден'
  except Exception: deno_status='не найден'
  return await c.message.answer(f'<b>Диагностика загрузчика</b>\nyt-dlp: <code>{html.escape(current_version())}</code>\nDeno: <code>{html.escape(deno_status)}</code>\n\nАвтообновление включается переменной YTDLP_UPDATE_ON_START и повторяется ежедневно. Обновление не обходит ограничения сайтов.',parse_mode='HTML')
 if action=='ytdlp_update':
  msg=await c.message.answer('Обновляю yt-dlp. Текущие загрузки будут завершены перед заменой пакета…')
  result=await update_ytdlp(force=True)
  await db.audit(uid,'ytdlp_update',{'ok':bool(result['ok']),'version':result['version']})
  return await msg.edit_text(('' if result['ok'] else '')+str(result['message'])+f'\nВерсия: {result["version"]}')
 if action=='users':
  rows=await db.recent_users(15)
  if not rows: return await c.message.answer('Пользователей пока нет.',reply_markup=panel(_admin_lang.get(uid,'ru')))
  kb=[[InlineKeyboardButton(text=f"{'×' if r['blocked'] else ''} {r['name'][:22]} · {r['telegram_id']}",callback_data=f"adm:user:{r['telegram_id']}")] for r in rows]
  kb.append([InlineKeyboardButton(text=ADMIN_UI.get(_admin_lang.get(uid,'ru'),ADMIN_UI['ru'])['search'],callback_data='adm:search'),InlineKeyboardButton(text=ADMIN_UI.get(_admin_lang.get(uid,'ru'),ADMIN_UI['ru'])['home'],callback_data='adm:home')])
  return await c.message.answer('Выберите пользователя:',reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
 if action=='recent':
  rows=await db.recent_users(15)
  if not rows:return await c.message.answer('Пользователей пока нет.',reply_markup=panel(_admin_lang.get(uid,'ru')))
  lines=['<b>Последние пользователи</b>']; rows_kb=[]
  for r in rows:
   lines.append(f'• <code>{r["telegram_id"]}</code> — {html.escape(r["name"][:50])} (@{html.escape(r["username"] or "—")}), {r["language"]}, '+('×' if r['blocked'] else ''))
   rows_kb.append([InlineKeyboardButton(text=f"{'×' if r['blocked'] else ''} {r['name'][:22]} · {r['telegram_id']}",callback_data=f"adm:user:{r['telegram_id']}")])
  rows_kb.append([InlineKeyboardButton(text=ADMIN_UI.get(_admin_lang.get(uid,'ru'),ADMIN_UI['ru'])['home'],callback_data='adm:home')])
  return await c.message.answer('\n'.join(lines),parse_mode='HTML',reply_markup=InlineKeyboardMarkup(inline_keyboard=rows_kb))
 if action=='tickets':
  rows=[r for r in await db.list_tickets('all',30) if r['status'] in {'open','in_progress'}][:20]
  if not rows:return await c.message.answer('Открытых тикетов нет.',reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='Меню',callback_data='adm:home')]]))
  lines=['<b>Открытые тикеты</b>']; rows_kb=[]
  for r in rows:
   lines.append(f'#{r["id"]} — {html.escape(r["name"][:40])} (<code>{r["telegram_id"]}</code>) · {r["status"]}')
   rows_kb.append([InlineKeyboardButton(text=f"#{r['id']} · {r['name'][:22]}",callback_data=f"adm:ticket:{r['id']}")])
  rows_kb.append([InlineKeyboardButton(text=ADMIN_UI.get(_admin_lang.get(uid,'ru'),ADMIN_UI['ru'])['home'],callback_data='adm:home')])
  return await c.message.answer('\n'.join(lines),parse_mode='HTML',reply_markup=InlineKeyboardMarkup(inline_keyboard=rows_kb))
 if action=='report': return await send_report(c.message,10)
 if action=='control':
  return await c.message.answer({'ru':'Управление User Bot\nИспользуйте кнопки ниже для загрузок, обслуживания, источников и лимитов.','uz':'User Bot boshqaruvi\nYuklashlar, texnik xizmat, manbalar va cheklovlarni quyidagi tugmalar orqali boshqaring.','en':'User Bot controls\nUse the buttons below to manage downloads, maintenance, sources and limits.'}.get(_admin_lang.get(uid,'ru'),'Управление User Bot'),reply_markup=control_keyboard(await db.get_setting('maintenance',False),await db.get_setting('downloads_enabled',True),_admin_lang.get(uid,'ru')))
 if action=='toggle_maintenance':
  value=not await db.get_setting('maintenance',False); await db.set_setting('maintenance',value); await db.audit(uid,'maintenance',{'enabled':value})
  return await c.message.answer({'ru':'Режим обслуживания '+('включён' if value else 'выключен'),'uz':'Texnik xizmat '+('yoqildi' if value else 'o‘chirildi'),'en':'Maintenance '+('enabled' if value else 'disabled')}.get(_admin_lang.get(uid,'ru'),'Режим обслуживания обновлён.'),reply_markup=control_keyboard(value,await db.get_setting('downloads_enabled',True),_admin_lang.get(uid,'ru')))
 if action=='toggle_downloads':
  value=not await db.get_setting('downloads_enabled',True); await db.set_setting('downloads_enabled',value); await db.audit(uid,'downloads_enabled',{'enabled':value})
  return await c.message.answer({'ru':'Загрузки '+('включены' if value else 'отключены'),'uz':'Yuklashlar '+('yoqildi' if value else 'o‘chirildi'),'en':'Downloads '+('enabled' if value else 'disabled')}.get(_admin_lang.get(uid,'ru'),'Загрузки обновлены.'),reply_markup=control_keyboard(await db.get_setting('maintenance',False),value,_admin_lang.get(uid,'ru')))
 if action=='broadcast': await state.set_state(Flow.broadcast); return await c.message.answer('Отправьте текст рассылки. Я покажу предварительный просмотр и попрошу подтверждение. Отмена: /cancel')
 if action=='sendone': await state.set_state(Flow.send_one); return await c.message.answer('Отправьте ID пользователя, затем через пробел текст. Пример: 123456789 Привет!')
 if action=='search': await state.set_state(Flow.search_user); return await c.message.answer('Введите Telegram ID, username или часть имени.')
 if action=='channel': await state.set_state(Flow.broadcast); await state.update_data(target='channel'); return await c.message.answer('Отправьте текст для публикации в CHANNEL_ID. Отмена: /cancel')
@router.message(Command('cancel'))
async def cancel(m:Message,state:FSMContext):
 if not authed(m.from_user.id): return
 await state.clear(); await m.answer('Действие отменено.',reply_markup=panel(_admin_lang.get(m.from_user.id,'ru')))
@router.message(Flow.broadcast)
async def broadcast_text(m:Message,state:FSMContext):
 if not authed(m.from_user.id): await state.clear(); return
 data=await state.get_data(); target=data.get('target','all')
 if not (m.text or m.caption or m.photo or m.video or m.document or m.audio or m.voice or m.animation):
  return await m.answer('Пришлите текст или поддерживаемое медиа для рассылки.')
 draft_text=(m.text or m.caption or '').strip()[:3500]
 media_kind=None; file_id=None; file_name='broadcast.bin'
 if m.photo: media_kind='photo'; file_id=m.photo[-1].file_id; file_name='broadcast.jpg'
 elif m.video: media_kind='video'; file_id=m.video.file_id; file_name=m.video.file_name or 'broadcast.mp4'
 elif m.document: media_kind='document'; file_id=m.document.file_id; file_name=m.document.file_name or 'broadcast.bin'
 elif m.audio: media_kind='audio'; file_id=m.audio.file_id; file_name=m.audio.file_name or 'broadcast.mp3'
 elif m.voice: media_kind='voice'; file_id=m.voice.file_id; file_name='broadcast.ogg'
 elif m.animation: media_kind='animation'; file_id=m.animation.file_id; file_name='broadcast.mp4'
 await state.clear()
 await state.update_data(broadcast_text=draft_text,target=target,source_chat_id=m.chat.id,source_message_id=m.message_id,has_media=bool(media_kind),media_kind=media_kind,file_id=file_id,file_name=file_name)
 lang=_admin_lang.get(m.from_user.id,'ru'); confirm={'ru':'Подтвердить','uz':'Tasdiqlash','en':'Confirm'}[lang]; cancel={'ru':'× Отмена','uz':'× Bekor qilish','en':'× Cancel'}[lang]
 kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=confirm,callback_data='adm:confirm_broadcast'),InlineKeyboardButton(text=cancel,callback_data='adm:cancel_broadcast')]])
 destination='канал' if target=='channel' else (f'язык: {target}' if target in {'ru','uz','en'} else 'личные сообщения всем')
 preview='\n\n'+draft_text if draft_text else '\n\n[Медиа-вложение будет скопировано без изменения]'
 await m.answer(f'Предпросмотр ({destination}):{preview}',reply_markup=kb)

@router.callback_query(F.data.in_({'adm:confirm_broadcast','adm:cancel_broadcast'}))
async def broadcast_confirm(c:CallbackQuery,state:FSMContext,bot:Bot):
 if not authed(c.from_user.id): return await c.answer('Сессия истекла',show_alert=True)
 data=await state.get_data(); body=data.get('broadcast_text',''); target=data.get('target','all'); source_chat=data.get('source_chat_id'); source_message=data.get('source_message_id'); await state.clear(); await c.answer()
 if c.data.endswith('cancel_broadcast') or (not body and not source_message): return await c.message.answer('Рассылка отменена.')
 async def copy_draft(destination_bot:Bot, chat_id):
  if destination_bot is bot and source_chat is not None and source_message is not None:
   await destination_bot.copy_message(chat_id=chat_id,from_chat_id=source_chat,message_id=source_message)
   return
  if not data.get('media_kind'):
   await destination_bot.send_message(chat_id,body)
   return
  if user_bot is None:
   raise RuntimeError('User Bot не подключён')
  media_bytes=BytesIO()
  await bot.download(data['file_id'],destination=media_bytes)
  upload=BufferedInputFile(media_bytes.getvalue(),filename=data.get('file_name') or 'broadcast.bin')
  kind=data['media_kind']
  caption=body or None
  if kind=='photo': await destination_bot.send_photo(chat_id,photo=upload,caption=caption)
  elif kind=='video': await destination_bot.send_video(chat_id,video=upload,caption=caption,supports_streaming=True)
  elif kind=='document': await destination_bot.send_document(chat_id,document=upload,caption=caption)
  elif kind=='audio': await destination_bot.send_audio(chat_id,audio=upload,caption=caption)
  elif kind=='voice': await destination_bot.send_voice(chat_id,voice=upload,caption=caption)
  elif kind=='animation': await destination_bot.send_animation(chat_id,animation=upload,caption=caption)
  else: await destination_bot.send_document(chat_id,document=upload,caption=caption)
 if target=='channel':
  try: await copy_draft(bot,settings.channel_id); await db.audit(c.from_user.id,'broadcast_channel',{'has_media':bool(data.get('has_media'))}); return await c.message.answer('Сообщение опубликовано в канале.')
  except Exception as e: return await c.message.answer(f'Не удалось отправить в канал ({type(e).__name__}). Проверьте CHANNEL_ID и права бота.')
 ids=await db.all_user_ids(target if target in {'ru','uz','en'} else None); ok=failed=0
 if user_bot is None: return await c.message.answer('User Bot не подключён.')
 if data.get('media_kind') and ids:
  try:
   media_bytes=BytesIO(); await bot.download(data['file_id'],destination=media_bytes)
   cached_media=media_bytes.getvalue()
  except Exception as e:
   return await c.message.answer(f'Не удалось подготовить медиа для рассылки ({type(e).__name__}).')
 else:
  cached_media=None
 for i,uid in enumerate(ids):
  try:
   if cached_media is not None:
    upload=BufferedInputFile(cached_media,filename=data.get('file_name') or 'broadcast.bin')
    kind=data['media_kind']; caption=body or None
    if kind=='photo': await user_bot.send_photo(uid,photo=upload,caption=caption)
    elif kind=='video': await user_bot.send_video(uid,video=upload,caption=caption,supports_streaming=True)
    elif kind=='document': await user_bot.send_document(uid,document=upload,caption=caption)
    elif kind=='audio': await user_bot.send_audio(uid,audio=upload,caption=caption)
    elif kind=='voice': await user_bot.send_voice(uid,voice=upload,caption=caption)
    elif kind=='animation': await user_bot.send_animation(uid,animation=upload,caption=caption)
    else: await user_bot.send_document(uid,document=upload,caption=caption)
   else: await copy_draft(user_bot,uid)
   ok+=1
  except Exception: failed+=1
  if i%20==19: await asyncio.sleep(1)
 await db.audit(c.from_user.id,'broadcast_all',{'ok':ok,'failed':failed,'target':target,'has_media':bool(data.get('has_media'))}); await c.message.answer(f'Рассылка завершена. Доставлено: {ok}; ошибок: {failed}.')
@router.message(Flow.send_one)
async def send_one(m:Message,state:FSMContext,bot:Bot):
 if not authed(m.from_user.id): await state.clear(); return
 parts=(m.text or '').split(maxsplit=1); await state.clear()
 if len(parts)<2 or not parts[0].isdigit(): return await m.answer('Формат: <code>TelegramID текст</code>',parse_mode='HTML')
 try:
  if user_bot is None: raise RuntimeError('User Bot не подключён')
  await user_bot.send_message(int(parts[0]),parts[1]); await db.audit(m.from_user.id,'send_one',{'user_id':int(parts[0])}); await m.answer('Сообщение отправлено.')
 except Exception as e: await m.answer(f'Не удалось отправить ({type(e).__name__}). Возможно, пользователь не запускал бота.')
@router.message(Flow.ticket_reply)
async def ticket_reply_button(m:Message,state:FSMContext):
 if not authed(m.from_user.id): await state.clear(); return await m.answer('Сессия истекла. Выполните /start.')
 body=(m.text or '').strip()
 data=await state.get_data(); tid=data.get('ticket_id')
 if not body: return await m.answer('Отправьте текстовый ответ одним сообщением.')
 if not tid: await state.clear(); return await m.answer('Номер тикета не найден.')
 ticket=await db.get_ticket(int(tid))
 if not ticket or ticket['status']=='closed': await state.clear(); return await m.answer('Тикет закрыт или не найден.')
 uid=await db.add_ticket_message(int(tid),m.from_user.id,'admin',body)
 await state.clear()
 if uid is None: return await m.answer('Тикет не найден.')
 try:
  if user_bot is None: raise RuntimeError('User Bot не подключён')
  await user_bot.send_message(uid,f'Support · Ответ поддержки по тикету #{tid}:\n\n{body}')
 except Exception as exc:
  return await m.answer(f'Ответ сохранён, но отправить его не удалось ({type(exc).__name__}).')
 await db.audit(m.from_user.id,'reply_ticket',{'ticket_id':int(tid)})
 await m.answer(f'Ответ по тикету #{tid} отправлен.',reply_markup=ticket_keyboard(int(tid),'in_progress',_admin_lang.get(m.from_user.id,'ru')))

@router.message(Flow.search_user)
async def search_user(m:Message,state:FSMContext):
 if not authed(m.from_user.id): await state.clear(); return
 await state.clear(); rows=await db.find_users(m.text or '')
 if not rows:return await m.answer('Ничего не найдено.')
 for r in rows:
  p=r['profile'];
  if isinstance(p,str):
   try: p=json.loads(p)
   except Exception: p={}
  await m.answer(f'{html.escape(r["name"])}\nID: <code>{r["telegram_id"]}</code>\nUsername: @{html.escape(r["username"] or "—")}\nЯзык: {r["language"]}\nЗагрузок: {p.get("successes",0) if isinstance(p,dict) else 0}\nЗаблокирован: {r["blocked"]}\nКоманды: /block {r["telegram_id"]} или /unblock {r["telegram_id"]}',parse_mode='HTML')
@router.message(Command('tickets'))
async def tickets_cmd(m:Message):
 if not authed(m.from_user.id): return await m.answer('Выполните /start для входа.')
 rows=[r for r in await db.list_tickets('all',50) if r['status'] in {'open','in_progress'}][:25]
 if not rows: return await m.answer('Открытых тикетов нет.')
 lines=['Открытые тикеты MediaMate:']
 for r in rows:
  lines.append(f"#{r['id']} · {html.escape(r['name'][:35])} · ID {r['telegram_id']} · {html.escape(r['status'])}")
  lines.append(f"/reply {r['id']} текст · /close {r['id']}")
 await m.answer('\n'.join(lines),parse_mode='HTML')


@router.message(Command('dbinfo'))
async def dbinfo_cmd(m:Message):
 if not authed(m.from_user.id): return await m.answer('Выполните /start для входа.')
 info=await db.storage_info()
 size=info['size_bytes']
 human=f'{size/1024:.1f} KiB' if size < 1024*1024 else f'{size/1024/1024:.2f} MiB'
 rows=info['rows']
 await m.answer(
  'MediaMate · Хранилище\n\n'
  f'Файл: <code>{html.escape(info["path"])}</code>\nРазмер файла БД: <b>{human}</b>\n'
  f'Пользователи: {rows["users"]}\nТикеты: {rows["tickets"]}\nСообщения тикетов: {rows["ticket_messages"]}\n'
  f'Аудит: {rows["audit"]}\nДневные метрики: {rows["metrics"]}\n'
  'Полные ссылки загрузок не хранятся. База хранит тикеты и ответы, настройки и агрегированные счётчики.',
  parse_mode='HTML')


@router.message(Command('user'))
async def user_cmd(m:Message):
 if not authed(m.from_user.id): return await m.answer('Выполните /start.')
 q=(m.text or '').partition(' ')[2].strip()
 if not q:return await m.answer('Использование: /user ID или username')
 rows=await db.find_users(q)
 if not rows:return await m.answer('Не найдено.')
 for r in rows: await m.answer(f'{html.escape(r["name"])} · <code>{r["telegram_id"]}</code> · @{html.escape(r["username"] or "—")}\n/block {r["telegram_id"]} · /unblock {r["telegram_id"]}',parse_mode='HTML')
@router.message(Command('block'))
@router.message(Command('unblock'))
async def block_cmd(m:Message,bot:Bot):
 if not authed(m.from_user.id): return await m.answer('Выполните /start.')
 parts=(m.text or '').split(); cmd=parts[0].split('@')[0]
 if len(parts)!=2 or not parts[1].isdigit():return await m.answer('Использование: /block ID или /unblock ID')
 uid=int(parts[1]); blocked=cmd=='/block'; await db.set_blocked(uid,blocked)
 if uid in USERS: USERS[uid]['blocked']=blocked
 if blocked:
  BLOCKED.add(uid)
  if uid in ACTIVE:
   ACTIVE[uid].set()
   proc=ACTIVE_PROCESSES.get(uid)
   if proc is not None and proc.returncode is None:
    try: proc.terminate()
    except ProcessLookupError: pass
   task=ACTIVE_TASKS.get(uid)
   if task is not None and not task.done(): task.cancel()
 else: BLOCKED.discard(uid)
 await db.audit(m.from_user.id,cmd[1:],{'user_id':uid})
 await m.answer(f'Пользователь {uid}: '+('заблокирован' if blocked else 'разблокирован'))
 try: await bot.send_message(uid,'Ваш доступ к MediaMate ограничен администратором.' if blocked else 'Доступ к MediaMate восстановлен.')
 except Exception: pass
@router.message(Command('reply'))
async def reply_ticket(m:Message,bot:Bot):
 if not authed(m.from_user.id): return await m.answer('Выполните /start.')
 parts=(m.text or '').split(maxsplit=2)
 if len(parts)<3 or not parts[1].isdigit():return await m.answer('Формат: /reply НОМЕР текст')
 tid=int(parts[1]); body=parts[2]; uid=await db.add_ticket_message(tid,m.from_user.id,'admin',body)
 if uid is None:return await m.answer('Тикет не найден.')
 try:
  if user_bot is None: raise RuntimeError('User Bot не подключён')
  await user_bot.send_message(uid,f'Support · Ответ поддержки по тикету #{tid}:\n\n{body}')
 except Exception as e: await m.answer(f'Ответ сохранён, но отправить пользователю не удалось ({type(e).__name__}).'); return
 await m.answer(f'Ответ по тикету #{tid} отправлен.')
@router.message(Command('close'))
async def close(m:Message):
 if not authed(m.from_user.id): return await m.answer('Выполните /start.')
 parts=(m.text or '').split()
 if len(parts)!=2 or not parts[1].isdigit():return await m.answer('Формат: /close НОМЕР')
 row=await db.close_ticket(int(parts[1])); await m.answer('Тикет закрыт.' if row else 'Тикет не найден.')
@router.message(Command('report'))
async def report_cmd(m:Message):
 if not authed(m.from_user.id):return await m.answer('Выполните /start.')
 await send_report(m,10)

@router.message(Command('ytdlp_status'))
async def ytdlp_status_cmd(m:Message):
 if not authed(m.from_user.id): return await m.answer('Выполните /start.')
 await m.answer(f'yt-dlp: {current_version()}\nДля проверки Deno используйте кнопку «yt-dlp: статус» в панели.')

@router.message(Command('update_ytdlp'))
async def update_ytdlp_cmd(m:Message):
 if not authed(m.from_user.id): return await m.answer('Выполните /start.')
 msg=await m.answer('Обновляю yt-dlp. Активные операции завершатся перед обновлением.')
 result=await update_ytdlp(force=True)
 await db.audit(m.from_user.id,'ytdlp_update',{'ok':bool(result['ok']),'version':result['version']})
 await msg.edit_text(('' if result['ok'] else '')+str(result['message'])+f'\nВерсия: {result["version"]}')
async def send_report(m:Message,days:int):
 rows=await db.report_rows(days)
 if not rows:return await m.answer('Пока нет статистики для отчёта.')
 lines=[f'<b>MediaMate — отчёт за {days} дней</b>','']
 for r in rows: lines.append(f'{r["day"]}: +{r["new_users"]} пользователей · ✓{r["downloads_ok"]} · ✕{r["downloads_failed"]} · среднее {int(r["avg_ms"])/1000:.1f} сек')
 s=await db.stats(); lines+=['',f'Всего пользователей: {s["users"]}',f'Активны за 24 ч: {s["active_24h"]}',f'Открытых тикетов: {s["open_tickets"]}']
 await m.answer('\n'.join(lines),parse_mode='HTML')
@router.message(F.text & ~F.text.startswith('/'))
async def fallback(m:Message):
 if m.from_user.id in settings.admin_ids and not authed(m.from_user.id): await m.answer('Сессия истекла. Выполните /start.')

@router.message(Command('setlimit'))
async def set_limit(m:Message):
 if not authed(m.from_user.id): return await m.answer('Выполните /start.')
 parts=(m.text or '').split()
 if len(parts)!=3: return await m.answer('Формат: /setlimit file_mb|duration_min|cooldown число\nПример: /setlimit file_mb 40')
 key=parts[1].lower()
 try: value=int(parts[2])
 except ValueError: return await m.answer('Значение должно быть целым числом.')
 max_mb=2000 if settings.telegram_api_base_url else 45
 if key=='file_mb' and 1<=value<=max_mb: await db.set_setting('max_file_size_mb',value)
 elif key=='duration_min' and 1<=value<=240: await db.set_setting('max_video_duration_seconds',value*60)
 elif key=='cooldown' and 0<=value<=120: await db.set_setting('download_cooldown_seconds',value)
 else: return await m.answer(f'Недопустимый параметр или диапазон. file_mb: 1–{max_mb}, duration_min: 1–240, cooldown: 0–120.')
 await db.audit(m.from_user.id,'set_limit',{'key':key,'value':value})
 await m.answer(f'Настройка сохранена: {key} = {value}. Применяется без перезапуска.')

@router.message(Command('source'))
async def source_cmd(m:Message):
 if not authed(m.from_user.id): return await m.answer('Выполните /start.')
 parts=(m.text or '').split()
 valid={'youtube','instagram','pinterest','facebook','tiktok','reddit','x','vk','other'}
 if len(parts)!=3 or parts[1].lower() not in valid or parts[2].lower() not in {'on','off'}:
  return await m.answer('Формат: /source youtube|instagram|pinterest|facebook|tiktok|reddit|x|vk|other on|off')
 key=parts[1].lower(); enabled=parts[2].lower()=='on'; values=await db.get_setting('source_enabled',{})
 if not isinstance(values,dict): values={}
 values[key]=enabled; await db.set_setting('source_enabled',values); await db.audit(m.from_user.id,'source_toggle',{'source':key,'enabled':enabled})
 await m.answer(f'Источник {key}: '+('включён' if enabled else 'отключён'))

@router.message(Command('broadcast_ru'))
@router.message(Command('broadcast_uz'))
@router.message(Command('broadcast_en'))
async def broadcast_language(m:Message,state:FSMContext):
 if not authed(m.from_user.id): return await m.answer('Выполните /start.')
 target=(m.text or '').split()[0].split('@')[0].replace('/broadcast_','')
 if target not in {'ru','uz','en'}: return await m.answer('Язык должен быть ru, uz или en.')
 await state.set_state(Flow.broadcast); await state.update_data(target=target)
 await m.answer(f'Отправьте текст рассылки пользователям с языком {target}. Перед отправкой будет предпросмотр и подтверждение. /cancel — отмена.')

@router.message(Command('audit'))
async def audit_cmd(m:Message):
 if not authed(m.from_user.id): return await m.answer('Выполните /start.')
 rows=await db.recent_audit(15)
 if not rows: return await m.answer('Журнал действий пока пуст.')
 lines=['<b>Последние действия администраторов</b>']
 for r in rows: lines.append(f"{r['created_at']:%Y-%m-%d %H:%M} · ID {r['admin_id']} · {html.escape(r['action'])}")
 await m.answer('\n'.join(lines),parse_mode='HTML')

@router.message(Command('recentdownloads'))
async def recent_downloads_cmd(m:Message):
 if not authed(m.from_user.id): return await m.answer('Выполните /start.')
 rows=await db.recent_downloads(15)
 if not rows: return await m.answer('Сводных данных о загрузках пока нет.')
 lines=['<b>Сводка загрузок по источникам за 7 дней</b>']
 for r in rows: lines.append(f"{r['created_at']:%m-%d %H:%M} · {html.escape(r['name'][:30])} · {html.escape(r['source'] or 'unknown')} · {html.escape(r['status'])}")
 await m.answer('\n'.join(lines),parse_mode='HTML')
