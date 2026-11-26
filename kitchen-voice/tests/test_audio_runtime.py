import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from voice.audio_runtime import AudioRuntime

def test_initialization_enumeration_and_stream_calls_share_one_thread():
    thread_ids = []
    selected = []
    def record(): thread_ids.append(threading.get_ident())
    class Stream:
        device = 8
        def start(self): record()
        def write(self, data): record()
        def stop(self): record()
        def close(self): record()
    def factory():
        record()
        def query(*args):
            record()
            return [{'name': 'speaker', 'index': 8}]
        def create(**kwargs):
            record()
            selected.append(kwargs['device'])
            return Stream()
        return SimpleNamespace(query_devices=query, RawOutputStream=create,
                               default=SimpleNamespace(device=(1, 8)),
                               PortAudioError=ValueError)
    runtime = AudioRuntime(factory)
    try:
        with ThreadPoolExecutor(max_workers=3) as callers:
            assert callers.submit(runtime.query_devices).result()[0]['index'] == 8
            stream = callers.submit(runtime.RawOutputStream, device=8).result()
            callers.submit(stream.start).result()
            callers.submit(stream.write, b'\x00\x00').result()
            callers.submit(stream.stop).result()
            callers.submit(stream.close).result()
        assert runtime.default.device == (1, 8)
        assert runtime.PortAudioError is ValueError
        assert stream.device == 8 and selected == [8]
        assert len(set(thread_ids)) == 1
        assert thread_ids[0] != threading.get_ident()
    finally:
        runtime.executor.shutdown()

def test_refresh_reinitializes_on_owner_thread_and_closes_stream_first():
    calls = []
    ids = []
    def track(name):
        calls.append(name)
        ids.append(threading.get_ident())
    module = SimpleNamespace(_initialized=1,
        stop=lambda: track('stop'),
        get_stream=lambda: SimpleNamespace(close=lambda: track('close')),
        default=SimpleNamespace(reset=lambda: track('reset')),
        _terminate=lambda: track('terminate'),
        _initialize=lambda: track('initialize'),
        query_devices=lambda: [{'index': 4, 'name': 'new bluetooth'}])
    runtime = AudioRuntime(lambda: module)
    try:
        result = runtime.refresh_devices()
        assert result[0]['name'] == 'new bluetooth'
        assert calls == ['stop', 'close', 'reset', 'terminate', 'initialize']
        assert len(set(ids)) == 1 and ids[0] != threading.get_ident()
    finally:
        runtime.executor.shutdown()
