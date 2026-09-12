from unittest.mock import patch, Mock

from dotenv import dotenv_values
import pytest
import requests

import discord_settings
from desktop import Api
from tests.test_desktop import dashboard, add

URL = 'https://discord.com/api/webhooks/123/fake_test_token'


def test_save_preserves_other_settings_and_clears_mention(tmp_path):
    path = tmp_path / '.env'
    path.write_text('# keep comment\nSMTP_PASSWORD="unrelated"\nDISCORD_USER_ID=456\nNOTIFICATION_WEBHOOK_URL=old\n')
    discord_settings.save(path, URL, '')
    assert dotenv_values(path) == {'SMTP_PASSWORD':'unrelated', 'DISCORD_USER_ID':'', 'NOTIFICATION_WEBHOOK_URL':URL}
    assert '# keep comment' in path.read_text()
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize('url', ['http://discord.com/api/webhooks/1/x', 'https://discord.com.evil.com/api/webhooks/1/x', URL+'?extra=x', URL+'\nINJECT=value'])
def test_invalid_url_does_not_write(tmp_path, url):
    path = tmp_path / '.env'
    with pytest.raises(ValueError):
        discord_settings.save(path, url, '')
    assert not path.exists()


def test_atomic_failure_keeps_original(tmp_path):
    path = tmp_path / '.env'
    path.write_text('KEEP=yes\n')
    with patch('discord_settings.set_key', side_effect=OSError('disk failure')):
        with pytest.raises(OSError):
            discord_settings.save(path, URL, '')
    assert path.read_text() == 'KEEP=yes\n'
    assert list(tmp_path.iterdir()) == [path]


def test_test_delivery_and_redacted_errors():
    with patch('discord_settings.requests.post', return_value=Mock(status_code=200)) as post:
        discord_settings.send_test(URL, '456')
    args = post.call_args.kwargs
    assert args['allow_redirects'] is False
    assert args['json']['allowed_mentions'] == {'parse':[], 'users':['456']}
    assert 'test notification' in args['json']['content']
    with patch('discord_settings.requests.post', side_effect=requests.Timeout(URL)):
        with pytest.raises(ValueError) as error:
            discord_settings.send_test(URL, '')
    assert URL not in str(error.value)
    with patch('discord_settings.requests.post', return_value=Mock(status_code=429)):
        with pytest.raises(ValueError, match='HTTP 429'):
            discord_settings.send_test(URL, '')


def test_dashboard_save_keeps_secret_out_of_snapshot_and_restarts_only_active(dashboard, monkeypatch, tmp_path):
    monkeypatch.setattr('desktop_backend.ROOT', tmp_path)
    monkeypatch.setenv('NOTIFICATION_WEBHOOK_URL', URL)
    monkeypatch.setenv('DISCORD_USER_ID', '456')
    active = add(dashboard, 1)
    add(dashboard, 2)
    dashboard._processes[active] = Mock()
    with patch.object(dashboard, 'pause') as pause, patch.object(dashboard, 'start') as start:
        assert Api(dashboard).action('save_discord', {'webhook':'', 'user_id':''}) == {'ok':True}
    pause.assert_called_once_with(active)
    start.assert_called_once_with(active, once=False)
    assert URL not in str(dashboard.snapshot())
    assert URL not in dashboard.path.read_text()
    assert dotenv_values(tmp_path / '.env')['NOTIFICATION_WEBHOOK_URL'] == URL
    with patch('discord_settings.requests.post', return_value=Mock(status_code=200)) as post:
        assert Api(dashboard).action('test_discord') == {'ok':True}
    post.assert_called_once()
