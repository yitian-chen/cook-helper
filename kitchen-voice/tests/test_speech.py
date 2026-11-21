import json
import threading
import pytest
import soundfile as sf
from voice import speech

class FakePlayer:
    def __init__(self):
        self.cancel = threading.Event()
        self.written = []
        self.closed = False
    def begin_pcm(self, rate): return self
    def write(self, chunk): self.written.append(chunk)
    def end_pcm(self): self.closed = True
    def play(self, path): self.path = path

def setup_stream(monkeypatch, tmp_path, player, fail=False):
    monkeypatch.setattr(speech, 'RUNTIME', tmp_path)
    monkeypatch.setattr(speech, 'output_rate', lambda: 22050)
    class FakeProcess:
        def __init__(self, *args, **kw):
            self.returncode = None
            self.stdin = self
        def write(self, data): self.request = json.loads(data)
        def close(self):
            from pathlib import Path
            Path(self.request['path']).write_bytes(b'\x01\x00' * 5000)
        def poll(self):
            if player.written:
                self.returncode = 1 if fail else 0
            return self.returncode
        def kill(self): self.returncode = -1
        def wait(self, timeout): return self.returncode
    monkeypatch.setattr(speech.subprocess, 'Popen', FakeProcess)

def test_stream_plays_before_worker_finishes_and_caches(monkeypatch, tmp_path):
    player = FakePlayer()
    setup_stream(monkeypatch, tmp_path, player)
    result = speech.speak('测试', player)
    assert result['status'] == 'finished' and result['first_audio_seconds'] is not None
    assert len(b''.join(player.written)) == 10000
    assert sf.info(tmp_path / result['file']).frames == 5000
    assert player.closed and not list(tmp_path.glob('*.pcm'))
    def unexpected(*a, **kw): raise AssertionError('cache hit must not call cloud')
    monkeypatch.setattr(speech.subprocess, 'Popen', unexpected)
    assert speech.speak('测试', player)['cache_hit'] is True

def test_cancel_does_not_cache_incomplete_audio(monkeypatch, tmp_path):
    player = FakePlayer()
    setup_stream(monkeypatch, tmp_path, player)
    def cancel(chunk):
        player.written.append(chunk)
        player.cancel.set()
    player.write = cancel
    assert speech.speak('取消', player)['status'] == 'cancelled'
    assert not list(tmp_path.iterdir()) and player.closed

def test_failed_stream_is_not_cached(monkeypatch, tmp_path):
    player = FakePlayer()
    setup_stream(monkeypatch, tmp_path, player, fail=True)
    with pytest.raises(RuntimeError): speech.speak('失败', player)
    assert not list(tmp_path.iterdir())

def test_cache_changes_with_voice(monkeypatch, tmp_path):
    monkeypatch.setattr(speech, 'RUNTIME', tmp_path)
    monkeypatch.setenv('TTS_VOICE', 'voice-a')
    original = speech.cache_path('你好', 22050)
    monkeypatch.setenv('TTS_VOICE', 'voice-b')
    assert speech.cache_path('你好', 22050) != original
    assert speech.cache_path('你好', 48000) != speech.cache_path('你好', 22050)


def test_xfyun_stream_uses_16k_cloud_audio_and_adapts_to_48k_output(monkeypatch, tmp_path):
    player = FakePlayer()
    setup_stream(monkeypatch, tmp_path, player)
    monkeypatch.setenv('TTS_PROVIDER', 'xfyun')
    monkeypatch.setattr(speech, 'output_rate', lambda: 48000)
    result = speech.speak('讯飞流式测试', player)
    assert result['status'] == 'finished'
    assert len(b''.join(player.written)) == 5000 * 3 * 2
    info = sf.info(tmp_path / result['file'])
    assert info.samplerate == 16000 and info.frames == 5000
    assert not list(tmp_path.glob('*.pcm'))


def test_safe_stream_error_is_shown_and_sidecar_is_removed(monkeypatch, tmp_path):
    player = FakePlayer()
    setup_stream(monkeypatch, tmp_path, player, fail=True)
    original = speech.subprocess.Popen
    def process(*args, **kwargs):
        instance = original(*args, **kwargs)
        close = instance.close
        def write_error():
            from pathlib import Path
            close()
            Path(instance.request['error_path']).write_text(
                json.dumps({'message': '讯飞服务返回错误码 11200'}), encoding='utf-8')
        instance.close = write_error
        return instance
    monkeypatch.setattr(speech.subprocess, 'Popen', process)
    with pytest.raises(speech.CloudServiceError, match='11200'):
        speech.speak('失败测试', player)
    assert not list(tmp_path.iterdir())
