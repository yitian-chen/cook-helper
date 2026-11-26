import json
import subprocess
import wave
from types import SimpleNamespace
import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient
from voice import cloud, agent, audio
from voice.config import artifact
from voice.service import VoiceService

def event(text='', final=False, **extra):
    return {'payload': {'output': {'sentence': {'text': text, 'sentence_end': final, **extra}}}}

def test_partial_results_are_not_duplicated():
    assert cloud.collect_asr([event('番'), event('番茄炒蛋', True), event('继续', True), event('心跳', heartbeat=True)]) == '番茄炒蛋继续'

def test_empty_or_failed_asr():
    with pytest.raises(ValueError): cloud.collect_asr([event()])
    with pytest.raises(RuntimeError): cloud.collect_asr([{'header': {'event': 'task-failed'}}])

def test_wav_resampling_and_silence(tmp_path, monkeypatch):
    monkeypatch.setattr(audio, 'RUNTIME', tmp_path)
    rate = 48000
    samples = np.sin(2 * np.pi * 440 * np.arange(rate) / rate).astype('float32') * 0.1
    path = tmp_path / 'input.wav'
    sf.write(path, np.column_stack([samples, samples]), rate)
    out = audio.normalize_wav(path)
    info = sf.info(out)
    assert (info.samplerate, info.channels, info.subtype) == (16000, 1, 'PCM_16')
    assert abs(info.duration - 1) < 0.001
    sf.write(path, np.zeros(16000), 16000)
    with pytest.raises(ValueError, match='静音'): audio.normalize_wav(path)

@pytest.mark.parametrize('name', ['../secret', 'a/b.wav', 'a\\b.wav', '..'])
def test_api_paths_are_confined(name):
    with pytest.raises(ValueError): artifact(name)

def test_cloud_timeout(monkeypatch):
    def timeout(*args, **kwargs): raise subprocess.TimeoutExpired('worker', 1)
    monkeypatch.setattr(subprocess, 'run', timeout)
    with pytest.raises(TimeoutError): cloud._request({'operation': 'asr'})

def test_agent_mock_and_http_contract(monkeypatch):
    monkeypatch.setenv('AGENT_MODE', 'mock')
    assert '模拟' in agent.ask_agent('暂停', 'id')['reply_text']
    monkeypatch.setenv('AGENT_MODE', 'http')
    monkeypatch.setenv('AGENT_URL', 'http://localhost/agent')
    monkeypatch.setattr(agent.httpx, 'post', lambda *a, **k: SimpleNamespace(
        raise_for_status=lambda: None, json=lambda: {'request_id': 'old', 'reply_text': '已暂停'}))
    with pytest.raises(ValueError, match='不匹配'): agent.ask_agent('暂停', 'new')

def test_pipeline_order_and_unlock_on_failure(monkeypatch):
    import voice.service as module
    calls = []
    monkeypatch.setattr(module, 'transcribe', lambda p: calls.append('asr') or '暂停')
    monkeypatch.setattr(module, 'ask_agent', lambda t, r: calls.append('agent') or {'reply_text': '回复'})
    monkeypatch.setattr(module, 'synthesize', lambda t: calls.append('tts') or artifact('test.wav'))
    monkeypatch.setattr(module, 'speak', lambda t, p: calls.append('speak') or {'file': 'test.wav', 'status': 'finished'})
    service = VoiceService()
    monkeypatch.setattr(service.player, 'play', lambda p: calls.append('play') or {'status': 'started'})
    assert service.process('input')['text'] == '暂停'
    assert calls == ['asr', 'agent', 'speak']
    def fail(p): raise ValueError('失败')
    monkeypatch.setattr(module, 'transcribe', fail)
    with pytest.raises(ValueError): service.process('input')
    assert not service.lock.locked()

def test_http_endpoints_and_busy_guard(monkeypatch):
    import voice.api as api
    client = TestClient(api.app, raise_server_exceptions=False)
    assert client.get('/health').json()['status'] == 'ok'
    page = client.get('/')
    assert page.status_code == 200 and '开始录音' in page.text
    assert '生成语音并播放' in page.text
    assert client.post('/asr', json={'file': '../secret'}).status_code == 400
    monkeypatch.setenv('AGENT_MODE', 'mock')
    assert client.post('/agent', json={'text': '暂停'}).json()['mode'] == 'mock'
    assert client.post('/tts', json={'text': ''}).status_code == 422
    api.service.lock.acquire()
    try:
        assert client.post('/agent', json={'text': '暂停'}).status_code == 400
    finally: api.service.lock.release()

def test_recorder_press_release_and_repeated_stop(tmp_path, monkeypatch):
    monkeypatch.setattr(audio, 'RUNTIME', tmp_path)
    class Stream:
        def __init__(self, **kw): self.callback = kw['callback']
        def start(self): self.callback(b'\x01\x00' * 1600, 1600, None, None)
        def stop(self): pass
        def close(self): pass
    monkeypatch.setattr(audio, 'sd_module', lambda: SimpleNamespace(
        check_input_settings=lambda **kw: None, RawInputStream=Stream))
    recorder = audio.Recorder()
    recorder.start()
    with pytest.raises(ValueError): recorder.start()
    path = recorder.stop()
    assert recorder.stop() == path
    assert recorder.stream is None
    with wave.open(str(path)) as wav:
        assert wav.getnchannels() == 1 and wav.getnframes() == 1600

def test_ui_device_settings_and_level(monkeypatch):
    import voice.api as api
    fake_sd = SimpleNamespace(
        default=SimpleNamespace(device=(1, 2)),
        query_hostapis=lambda: [{'name': 'TestAudio'}],
        query_devices=lambda dev=None, kind=None: {'default_samplerate': 48000, 'max_output_channels': 2, 'name': 'Test Speaker'},
        check_input_settings=lambda **kw: None,
        check_output_settings=lambda **kw: None)
    monkeypatch.setattr(audio, 'sd_module', lambda: fake_sd)
    monkeypatch.setattr(api.service.player, 'stop', lambda: None)
    # Restore process config after this test.
    for key in ['INPUT_DEVICE', 'OUTPUT_DEVICE', 'RECORD_SAMPLE_RATE']:
        monkeypatch.setenv(key, '')
    client = TestClient(api.app, raise_server_exceptions=False)
    assert client.get('/settings').json()['default_input'] == 1
    response = client.post('/settings/devices', json={'input_device': 1, 'output_device': 2})
    assert response.json()['sample_rate'] == 48000
    import os
    assert os.environ['INPUT_DEVICE'] == '1'
    assert client.get('/record/level').json()['recording'] is False

def test_stereo_only_output_resamples_without_switching_device(tmp_path, monkeypatch):
    class AudioError(Exception): pass
    captures = []
    def check(**kw):
        assert kw['device'] == 8
        if kw['channels'] != 2 or kw['samplerate'] != 48000:
            raise AudioError('unsupported format')
    sd = SimpleNamespace(PortAudioError=AudioError,
        query_devices=lambda *a: {'max_output_channels': 2, 'default_samplerate': 48000, 'name': 'Computer Speaker', 'hostapi': 0},
        query_hostapis=lambda host: {'name': 'TestAPI'},
        get_stream=lambda: SimpleNamespace(device=8),
        check_output_settings=check, stop=lambda: None,
        play=lambda samples, rate, **kw: captures.append((samples, rate, kw)))
    monkeypatch.setattr(audio, 'sd_module', lambda: sd)
    monkeypatch.setenv('OUTPUT_DEVICE', '8')
    file = tmp_path / 'mono.wav'
    sf.write(file, np.ones(22050, dtype='float32') * .01, 22050)
    result = audio.Player().play(file)
    assert result['output_device'] == 8 and result['output_name'] == 'Computer Speaker'
    samples, rate, kw = captures[0]
    assert samples.shape == (48000, 2) and rate == 48000 and kw['device'] == 8
    writer = audio.MonoPCMWriter(SimpleNamespace(write=lambda data: captures.append(data)), 2)
    writer.write(b'\x01\x00\x02\x00')
    assert captures[-1] == b'\x01\x00\x01\x00\x02\x00\x02\x00'

def test_audio_help_is_platform_specific(monkeypatch):
    monkeypatch.setattr(audio.platform, 'system', lambda: 'Linux')
    assert 'Linux' in audio.audio_device_help() and 'WASAPI' not in audio.audio_device_help()
    monkeypatch.setattr(audio.platform, 'system', lambda: 'Windows')
    assert 'WASAPI' in audio.audio_device_help()

def test_refresh_clears_stale_selections_and_rejects_busy_recording(monkeypatch):
    import voice.api as api
    from voice.audio_runtime import runtime
    monkeypatch.setenv('INPUT_DEVICE', '77')
    monkeypatch.setenv('OUTPUT_DEVICE', '88')
    monkeypatch.setattr(api.service.player, 'stop', lambda: None)
    monkeypatch.setattr(api.service.player, 'end_pcm', lambda: None)
    monkeypatch.setattr(runtime, 'refresh_devices', lambda: [{'index': 0}])
    client = TestClient(api.app, raise_server_exceptions=False)
    response = client.post('/devices/refresh')
    assert response.status_code == 200
    import os
    assert os.environ['OUTPUT_DEVICE'] == '' and os.environ['INPUT_DEVICE'] == ''
    monkeypatch.setattr(api.service.recorder, 'stream', object())
    assert client.post('/devices/refresh').status_code == 400
