from app.security import sanitize_url

def test_sanitize_removes_common_secret_query_parameters():
    result=sanitize_url('https://example.com/video?id=123&token=secret&foo=bar')
    assert 'token' not in result and 'secret' not in result
    assert 'id=123' in result and 'foo=bar' in result

def test_sanitize_removes_fragment():
    assert sanitize_url('https://example.com/watch#private') == 'https://example.com/watch'
