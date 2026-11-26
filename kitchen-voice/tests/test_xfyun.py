import base64
import hashlib
import hmac
import json
import wave
from types import SimpleNamespace
from urllib.parse import urlparse, parse_qs
import numpy as np
import pytest
from fastapi.testclient import TestClient
from voice import xfyun, cloud, providers, speech
from voice.pcm import PCMConverter


def result(sn, text, **extra):
    return {'sn': sn, 'ws': [{'cw': [{'w': text}, {'w': '不要用第二候选'}]}], **extra}


def test_signing_uses_exact_host_date_and_request_line():
    date = 'Fri, 20 Nov 2026 01:02:03 GMT'
    parsed = urlparse(xfyun.signed_url('iat-api.xfyun.cn', '/v2/iat', 'fake-key', 'fake-secret', date))
    params = parse_qs(parsed.query)
    auth = base64.b64decode(params['authorization'][0]).decode()
    expected = base64.b64encode(hmac.new(b'fake-secret',
        f'host: iat-api.xfyun.cn\ndate: {date}\nGET /v2/iat HTTP/1.1'.encode(), hashlib.sha256).digest()).decode()
    assert expected in auth and 'api_key="fake-key"' in auth
    assert params['date'] == [date] and parsed.scheme == 'wss'


def test_dynamic_asr_corrections_replace_range_and_repeated_sn():
    text = xfyun.Transcript()
    text.add(result(1, '我想'))
    text.add(result(2, '番茄超蛋'))
    text.add(result(3, '做番茄炒蛋', pgs='rpl', rg=[2, 2]))
    text.add(result(3, '做番茄炒鸡蛋'))
    assert text.text() == '我想做番茄炒鸡蛋'


def test_asr_frames_have_one_initial_frame_and_one_end_frame():
    raw = b'\x01\x00' * 1300
    frames = list(xfyun.asr_frames(raw, 'test-app'))
    assert [f['data']['status'] for f in frames] == [0, 1, 1, 2]
    assert 'common' in frames[0] and all('common' not in f for f in frames[1:])
    assert b''.join(base64.b64decode(f['data']['audio']) for f in frames) == raw


class Socket:
    def __init__(self, events):
        self.events, self.sent, self.closed = iter(events), [], False
    def send(self, data): self.sent.append(json.loads(data))
    def recv(self): return json.dumps(next(self.events))
    def close(self): self.closed = True


def test_asr_receives_dynamic_corrections_and_closes_socket(tmp_path, monkeypatch):
    path = tmp_path / 'input.wav'
    with wave.open(str(path), 'wb') as f:
        f.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        f.writeframes(b'\x01\x00' * 640)
    socket = Socket([{'code': 0, 'data': {'status': 1, 'result': result(1, '番茄超蛋')}},
                     {'code': 0, 'data': {'status': 2, 'result': result(2, '番茄炒蛋', pgs='rpl', rg=[1, 1])}}])
    monkeypatch.setattr(xfyun, 'connect', lambda kind: (socket, 'test-app'))
    assert xfyun.transcribe(path) == '番茄炒蛋'
    assert socket.closed and socket.sent[0]['common']['app_id'] == 'test-app'


def test_tts_handles_heartbeats_last_audio_and_utf8(monkeypatch):
    monkeypatch.setenv('TTS_PROVIDER', 'xfyun')
    socket = Socket([{'code': 0, 'data': None},
        {'code': 0, 'data': {'status': 1, 'audio': base64.b64encode(b'\x01\x00').decode()}},
        {'code': 0, 'data': {'status': 2, 'audio': base64.b64encode(b'\x02\x00').decode()}}])
    monkeypatch.setattr(xfyun, 'connect', lambda kind: (socket, 'test-app'))
    assert b''.join(xfyun.tts_chunks('番茄炒蛋')) == b'\x01\x00\x02\x00'
    request = socket.sent[0]
    assert base64.b64decode(request['data']['text']).decode('utf-8') == '番茄炒蛋'
    assert request['business']['auf'] == 'audio/L16;rate=16000'
    assert request['business']['aue'] == 'raw' and socket.closed


@pytest.mark.parametrize('events', [
    [{'code': 0, 'data': {'status': 1, 'audio': 'AQACAA=='}}],  # no completion
    [{'code': 0, 'data': {'status': 2, 'audio': 'AQ=='}}],       # odd PCM
    [{'code': 0, 'data': {'status': 2, 'audio': ''}}],           # empty
    [{'code': 11200, 'message': 'SECRET-MUST-NOT-LEAK'}],
])
def test_bad_tts_results_fail_without_leaking_vendor_diagnostics(events, monkeypatch):
    socket = Socket(events)
    monkeypatch.setattr(xfyun, 'connect', lambda kind: (socket, 'app'))
    with pytest.raises(providers.CloudServiceError) as error:
        list(xfyun.tts_chunks('测试'))
    assert 'SECRET' not in str(error.value) and socket.closed


def test_handshake_errors_do_not_expose_signed_urls(monkeypatch):
    import websocket
    def fail(*a, **kw): raise RuntimeError('wss://host?authorization=SECRET')
    monkeypatch.setattr(websocket, 'create_connection', fail)
    with pytest.raises(providers.CloudServiceError) as exc:
        xfyun.connect('asr')
    assert 'SECRET' not in str(exc.value)


def test_service_credentials_can_be_separate_and_status_never_returns_secrets(monkeypatch):
    monkeypatch.setenv('ASR_PROVIDER', 'xfyun')
    monkeypatch.setenv('XFYUN_ASR_API_KEY', 'different-private-key')
    assert providers.xfyun_credentials('asr')[0][1] == 'different-private-key'
    assert providers.status('asr')['configured']
    assert 'different-private-key' not in json.dumps(providers.status('asr'))
    monkeypatch.delenv('XFYUN_ASR_API_KEY')
    monkeypatch.delenv('XFYUN_API_KEY')
    assert not providers.status('asr')['configured']
    assert providers.status('tts')['provider'] == 'aliyun'


def test_xfyun_text_and_sample_rate_limits_fail_before_connect(monkeypatch):
    monkeypatch.setenv('TTS_PROVIDER', 'xfyun')
    with pytest.raises(ValueError, match='8000字节'):
        providers.validate_text('😀' * 2000)
    with pytest.raises(ValueError, match='16000'):
        list(xfyun.tts_chunks('你好', 48000))


def test_dispatch_uses_xfyun_without_constructing_aliyun_client(monkeypatch, tmp_path):
    def forbidden(): raise AssertionError('must not instantiate Aliyun')
    monkeypatch.setattr(cloud, '_client', forbidden)
    monkeypatch.setattr(xfyun, 'transcribe', lambda path: '我想做番茄炒鸡蛋')
    monkeypatch.setattr(xfyun, 'tts_chunks', lambda text, rate: iter([b'\x01\x00' * 160]))
    assert cloud.dispatch({'operation': 'asr', 'provider': 'xfyun', 'path': 'ignored'})['text'] == '我想做番茄炒鸡蛋'
    audio = cloud.dispatch({'operation': 'tts', 'provider': 'xfyun', 'text': '回复'})
    import io
    with wave.open(io.BytesIO(base64.b64decode(audio['audio']))) as f:
        assert (f.getframerate(), f.getnframes()) == (16000, 160)
    path = tmp_path / 'stream.pcm'
    cloud.dispatch({'operation': 'tts-stream', 'provider': 'xfyun', 'text': '回复', 'path': str(path), 'sample_rate': 16000})
    assert path.read_bytes() == b'\x01\x00' * 160


@pytest.mark.parametrize('target_rate', [16000, 22050, 44100, 48000])
def test_stream_resampling_preserves_duration_and_chunk_boundaries(target_rate):
    source = (np.sin(np.arange(1600) * .2) * 12000).astype('<i2').tobytes()
    def run(chunks):
        converter = PCMConverter(16000, target_rate)
        return b''.join([*(converter.convert(c) for c in chunks), converter.convert(b'', final=True)])
    whole = run([source])
    split = run([source[i:i+74] for i in range(0, len(source), 74)])
    assert whole == split
    assert len(whole) == round(1600 * target_rate / 16000) * 2


def test_xfyun_cache_does_not_reuse_aliyun_voice_and_varies_with_controls(monkeypatch):
    old = speech.cache_path('你好', 16000)
    monkeypatch.setenv('TTS_PROVIDER', 'xfyun')
    new = speech.cache_path('你好', 16000)
    assert old != new
    monkeypatch.setenv('XFYUN_TTS_SPEED', '65')
    assert new != speech.cache_path('你好', 16000)


def test_provider_api_switches_independently_and_guards_active_requests(monkeypatch):
    import voice.api as api
    monkeypatch.setattr(api.service.player, 'stop', lambda: None)
    client = TestClient(api.app, raise_server_exceptions=False)
    before = client.get('/providers').json()
    assert before['asr']['provider'] == 'aliyun'
    response = client.post('/settings/providers', json={'asr_provider': 'xfyun', 'tts_provider': 'aliyun'})
    assert response.status_code == 200
    assert response.json()['asr']['provider'] == 'xfyun' and response.json()['tts']['provider'] == 'aliyun'
    assert 'test-api_key' not in response.text
    assert client.post('/settings/providers', json={'asr_provider': 'invalid', 'tts_provider': 'aliyun'}).status_code == 422
    api.service.lock.acquire()
    try:
        assert client.post('/settings/providers', json={'asr_provider': 'aliyun', 'tts_provider': 'xfyun'}).status_code == 400
    finally:
        api.service.lock.release()
    assert providers.provider('tts') == 'aliyun'


def test_structured_worker_errors_are_safe_and_visible(monkeypatch):
    monkeypatch.setattr(cloud.subprocess, 'run', lambda *a, **kw: SimpleNamespace(
        returncode=1, stdout='ERROR:' + json.dumps({'message': '讯飞服务返回错误码 11200'})))
    with pytest.raises(providers.CloudServiceError, match='11200'):
        cloud._request({'operation': 'tts'})
