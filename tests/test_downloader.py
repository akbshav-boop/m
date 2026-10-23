import importlib
from pathlib import Path


def _downloader(monkeypatch):
    monkeypatch.setenv('USER_BOT_TOKEN', 'test-user-token')
    monkeypatch.setenv('ADMIN_BOT_TOKEN', 'test-admin-token')
    monkeypatch.setenv('ADMIN_IDS', '12345')
    monkeypatch.setenv('ADMIN_ACCESS_CODE', 'long-test-access-code')
    import app.downloader as downloader
    return importlib.reload(downloader)


def test_image_media_is_classified_as_image(monkeypatch):
    downloader = _downloader(monkeypatch)
    summary = downloader.media_summary({'id': 'photo-1', 'title': 'Photo', 'ext': 'jpg'})
    assert summary['is_image'] is True
    assert summary['has_video'] is False


def test_gallery_summary_counts_entries_and_video(monkeypatch):
    downloader = _downloader(monkeypatch)
    summary = downloader.media_summary({
        'id': 'gallery', 'title': 'Gallery', '_type': 'playlist',
        'entries': [
            {'id': 'image-1', 'ext': 'jpg'},
            {'id': 'video-1', 'ext': 'mp4', 'vcodec': 'h264', 'formats': [{'height': 720, 'vcodec': 'h264'}]},
        ],
    })
    assert summary['is_collection'] is True
    assert summary['entries_count'] == 2
    assert summary['has_video'] is True
    assert 720 in summary['heights']


def test_gallery_download_does_not_force_no_playlist(monkeypatch, tmp_path):
    downloader = _downloader(monkeypatch)
    gallery = downloader.make_download_command('https://www.instagram.com/p/example/', 'original', tmp_path)
    youtube = downloader.make_download_command('https://www.youtube.com/watch?v=example', 'best', tmp_path)
    assert '--no-playlist' not in gallery
    assert '--playlist-end' in gallery
    assert '--no-playlist' in youtube


def test_youtube_commands_enable_js_remote_components(monkeypatch, tmp_path):
    downloader = _downloader(monkeypatch)
    youtube = downloader.make_download_command('https://www.youtube.com/watch?v=example', 'best', tmp_path)
    instagram = downloader.make_download_command('https://www.instagram.com/p/example/', 'original', tmp_path)
    assert '--remote-components' in youtube
    assert 'ejs:github' in youtube
    assert '--remote-components' not in instagram


def test_youtube_bot_check_error_is_detected_without_attempting_bypass(monkeypatch):
    downloader = _downloader(monkeypatch)
    assert downloader.is_youtube_bot_check_error("Sign in to confirm you're not a bot")
    assert downloader.is_youtube_bot_check_error('HTTP Error 429: Too Many Requests')
    assert not downloader.is_youtube_bot_check_error('Video unavailable')


def test_youtube_host_match_is_not_suffix_spoofable(monkeypatch):
    downloader = _downloader(monkeypatch)
    assert downloader.is_youtube_host('youtu.be')
    assert downloader.is_youtube_host('music.youtube.com')
    assert not downloader.is_youtube_host('notyoutube.com')


def test_download_command_prefers_progressive_video_and_uses_speed_options(monkeypatch, tmp_path):
    downloader = _downloader(monkeypatch)
    command = downloader.make_download_command('https://www.youtube.com/watch?v=example', 'best', tmp_path)
    assert '--concurrent-fragments' in command
    assert command[command.index('--concurrent-fragments') + 1] == '8'
    assert '--buffer-size' in command and '1M' in command
    assert '--http-chunk-size' not in command
    selector = command[command.index('--format') + 1]
    assert selector.startswith('best[height<=720][ext=mp4]/best[height<=720]/')
