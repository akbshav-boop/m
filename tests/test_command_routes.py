import ast
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def _commands(path: str) -> set[str]:
    tree = ast.parse((ROOT / path).read_text(encoding='utf-8'))
    found = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            rendered = ast.unparse(dec)
            found.update(re.findall(r"Command\(['\"]([^'\"]+)['\"]\)", rendered))
    return found


def test_published_user_commands_have_handlers():
    routes = _commands('app/user_bot.py')
    expected = {
        'menu', 'download', 'cancel', 'help', 'profile', 'stats', 'history',
        'clearhistory', 'settings', 'language', 'support', 'privacy', 'id', 'about',
    }
    assert expected <= routes


def test_published_admin_commands_have_handlers():
    routes = _commands('app/admin_bot.py')
    expected = {
        'panel', 'stats', 'backup', 'user', 'block', 'unblock', 'reply', 'close',
        'report', 'ytdlp_status', 'update_ytdlp', 'recentdownloads', 'tickets', 'dbinfo',
        'audit', 'cancel', 'logout', 'setlimit', 'source', 'broadcast_ru', 'broadcast_uz', 'broadcast_en',
    }
    assert expected <= routes
