"""Keep PortAudio initialization and calls on one owning thread.

FastAPI's sync endpoints may run on different thread-pool workers. Windows
audio backends (notably WASAPI/COM) cannot assume those workers share the
initializing thread's state. Callbacks still run on PortAudio's own threads.
"""
import threading
from concurrent.futures import ThreadPoolExecutor

class AudioRuntime:
    STREAM_FACTORIES = {'RawInputStream', 'RawOutputStream', 'InputStream',
                        'OutputStream', 'Stream', 'RawStream', 'get_stream'}

    def __init__(self, factory=None):
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='voice-audio-owner')
        self.owner = None
        self.module = None
        self.factory = factory

    def _execute(self, function, *args, **kwargs):
        self.owner = threading.get_ident()
        if self.module is None:
            if self.factory is None:
                import sounddevice
                self.module = sounddevice
            else:
                self.module = self.factory()
        return function(*args, **kwargs)

    def run(self, function, *args, **kwargs):
        if threading.get_ident() == self.owner:
            return function(*args, **kwargs)
        return self.executor.submit(self._execute, function, *args, **kwargs).result()

    def refresh_devices(self):
        """Expert-mode reinitialization, only called while the service is idle.

        This uses the pinned sounddevice 0.5.6 expert API. Open streams must be
        stopped/closed before terminating PortAudio, on its initializing thread.
        """
        def refresh():
            module = self.module
            module.stop()
            try:
                stream = module.get_stream()
            except RuntimeError:
                stream = None
            if stream is not None:
                stream.close()
            module.default.reset()
            if module._initialized:
                module._terminate()
            try:
                module._initialize()
            except Exception as exc:
                raise ValueError('音频设备重新扫描失败，请停止并重新启动语音服务') from exc
            return [dict(item) for item in module.query_devices()]
        return self.run(refresh)

    def __getattr__(self, name):
        if name.startswith('_'):
            raise AttributeError(name)
        if name == 'default':
            return ObjectProxy(self, lambda: self.module.default)
        value = self.run(lambda: getattr(self.module, name))
        if isinstance(value, type) and issubclass(value, BaseException):
            return value  # Preserve exception types for isinstance/except.
        if callable(value):
            def invoke(*args, **kwargs):
                result = self.run(lambda: getattr(self.module, name)(*args, **kwargs))
                if name in self.STREAM_FACTORIES and result is not None:
                    return ObjectProxy(self, lambda: result)
                return result
            return invoke
        return value

class ObjectProxy:
    def __init__(self, runtime, get_object):
        self.runtime = runtime
        self.get_object = get_object

    def __getattr__(self, name):
        value = self.runtime.run(lambda: getattr(self.get_object(), name))
        if callable(value):
            return lambda *args, **kwargs: self.runtime.run(
                lambda: getattr(self.get_object(), name)(*args, **kwargs))
        return value

runtime = AudioRuntime()
