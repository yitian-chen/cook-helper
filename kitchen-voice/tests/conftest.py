"""Tests must not depend on, expose, or use the developer's real credentials."""
import pytest


@pytest.fixture(autouse=True)
def isolated_cloud_config(monkeypatch):
    monkeypatch.setenv('ASR_PROVIDER', 'aliyun')
    monkeypatch.setenv('TTS_PROVIDER', 'aliyun')
    monkeypatch.setenv('DASHSCOPE_API_KEY', 'test-not-a-real-key')
    monkeypatch.setenv('DASHSCOPE_WORKSPACE', 'test-workspace')
    for field in ('APP_ID', 'API_KEY', 'API_SECRET'):
        monkeypatch.setenv('XFYUN_' + field, 'test-' + field.lower())
        for kind in ('ASR', 'TTS'):
            monkeypatch.delenv(f'XFYUN_{kind}_{field}', raising=False)
    for name, value in [('VOICE', 'xiaoyan'), ('SPEED', '50'), ('VOLUME', '50'), ('PITCH', '50')]:
        monkeypatch.setenv('XFYUN_TTS_' + name, value)
