from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from app.locales import tr
from app.config import settings


def main_menu(lang):
    # Primary navigation belongs below the input field, not in the conversation.
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=tr(lang, "menu_download")), KeyboardButton(text=tr(lang, "menu_profile"))],
            [KeyboardButton(text=tr(lang, "menu_support")), KeyboardButton(text=tr(lang, "menu_settings"))],
            [KeyboardButton(text=tr(lang, "menu_history")), KeyboardButton(text=tr(lang, "menu_how"))],
            [KeyboardButton(text=tr(lang, "language"))],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder=tr(lang, "input_placeholder"),
        selective=False,
    )


def accept_rules(lang):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=tr(lang, 'accept'), callback_data='rules:accept')]])


def support_keyboard(lang):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='@' + settings.support_username, url='https://t.me/' + settings.support_username)],
        [InlineKeyboardButton(text=tr(lang, 'new_ticket'), callback_data='support:ticket'), InlineKeyboardButton(text=tr(lang, 'my_tickets'), callback_data='support:mine')],
        [InlineKeyboardButton(text=tr(lang, 'back'), callback_data='menu:back')],
    ])


def settings_keyboard(lang, current, quality='best', fmt='video'):
    labels={
      'ru': {'best':'Лучшее','q1080':'До 1080p','q720':'До 720p','q480':'До 480p','video':'Видео','audio':'Аудио','original':'Оригинал','clear':'Очистить историю сеанса'},
      'uz': {'best':'Eng yaxshi','q1080':'1080p gacha','q720':'720p gacha','q480':'480p gacha','video':'Video','audio':'Audio','original':'Asl','clear':'Joriy tarixni tozalash'},
      'en': {'best':'Best','q1080':'Up to 1080p','q720':'Up to 720p','q480':'Up to 480p','video':'Video','audio':'Audio','original':'Original','clear':'Clear session history'},
    }[lang]
    b=InlineKeyboardBuilder()
    b.button(text='Русский'+(' ✓' if current=='ru' else ''),callback_data='lang:ru')
    b.button(text='O‘zbekcha'+(' ✓' if current=='uz' else ''),callback_data='lang:uz')
    b.button(text='English'+(' ✓' if current=='en' else ''),callback_data='lang:en')
    b.adjust(1)
    b.row(InlineKeyboardButton(text=tr(lang,'quality')+': '+labels.get(quality,labels['best']),callback_data='settings:quality'))
    b.row(InlineKeyboardButton(text=tr(lang,'format')+': '+labels.get(fmt,labels['video']),callback_data='settings:format'))
    b.row(InlineKeyboardButton(text=labels['clear'],callback_data='history:clear'))
    b.row(InlineKeyboardButton(text=tr(lang,'back'),callback_data='menu:back'))
    return b.as_markup()


def preference_keyboard(lang, kind, current):
    b=InlineKeyboardBuilder()
    if kind=='quality':
        choices=[('best','best'),('q1080','q1080'),('q720','q720'),('q480','q480')]
    else:
        choices=[('video','video'),('audio','audio'),('original','original')]
    labels={
      'ru': {'best':'Лучшее качество','q1080':'До 1080p','q720':'До 720p','q480':'До 480p','video':'Видео','audio':'MP3-аудио','original':'Оригинал'},
      'uz': {'best':'Eng yaxshi sifat','q1080':'1080p gacha','q720':'720p gacha','q480':'480p gacha','video':'Video','audio':'MP3 audio','original':'Asl media'},
      'en': {'best':'Best quality','q1080':'Up to 1080p','q720':'Up to 720p','q480':'Up to 480p','video':'Video','audio':'MP3 audio','original':'Original media'},
    }[lang]
    for value,label in choices:
        b.button(text=label+(' ✓' if value==current else ''),callback_data=f'settings:set:{kind}:{value}')
    b.adjust(2)
    b.row(InlineKeyboardButton(text=tr(lang,'back'),callback_data='menu:settings'))
    return b.as_markup()


def format_choices(lang, key, summary, preferred_quality='best', preferred_format='video'):
    b=InlineKeyboardBuilder(); choices=[]
    if summary.get('has_video'):
        choices=[('best','best')]
        heights=set(summary.get('heights') or [])
        for h in (1080,720,480):
            if any(x>=h for x in heights): choices.append((f'q{h}',f'q{h}'))
    if summary.get('has_audio'): choices.append(('audio','audio'))
    if summary.get('is_image') or summary.get('is_collection') or not choices:
        choices.append(('original','original'))
    seen=set()
    for label,mode in choices:
        if mode in seen: continue
        seen.add(mode)
        marker=' ✓' if ((mode==preferred_quality and mode not in {'audio','original'}) or (mode==preferred_format) or (mode=='best' and preferred_format=='video')) else ''
        b.button(text=tr(lang,label)+marker,callback_data=f'pick:{key}:{mode}')
    b.button(text=tr(lang,'cancel'),callback_data=f'pickcancel:{key}')
    b.adjust(2,2,1)
    return b.as_markup()


def cancel_download_keyboard(lang):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=tr(lang,'cancel'),callback_data='download:cancel')]])


def subscription_keyboard(lang, channel_url):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=tr(lang,'subscribe'),url=channel_url)],[InlineKeyboardButton(text=tr(lang,'check_subscription'),callback_data='subscription:check')]])
