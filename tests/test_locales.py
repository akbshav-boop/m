from app.locales import tr


def test_primary_copy_and_data_policy_exist_in_all_languages():
    for lang in ('ru', 'uz', 'en'):
        assert tr(lang, 'menu_download')
        assert tr(lang, 'menu_history')
        assert tr(lang, 'rules')
        assert tr(lang, 'accept')
        assert tr(lang, 'privacy_text')
        assert tr(lang, 'subscription_left_notice')
        assert tr(lang, 'menu_updated')
        assert tr(lang, 'cancel')
        assert tr(lang, 'download_cancelled')
        assert tr(lang, 'favorite_saved')  # legacy callback now says that link saving is disabled
