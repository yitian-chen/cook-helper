import math
import platform
import threading
import uuid
from pathlib import Path
import numpy as np
import soundfile as sf
from .config import RUNTIME, device, number

def sd_module():
    from .audio_runtime import runtime
    return runtime

def audio_device_help():
    if platform.system() == 'Windows':
        return ('请确认设备已连接，优先选择扬声器或耳机的 MME/WASAPI 入口，'
                '暂避 WDM-KS、Hands-Free 通话入口。')
    return ('请确认设备已连接；在 Linux 系统声音设置中先选定输入/输出，'
            '再重新扫描并选择 default、pulse、pipewire 或实际可用的 ALSA 入口。')

def output_profile(source_rate, dtype='float32', output_device=None):
    """Adapt format on the selected device; never silently route elsewhere."""
    sd = sd_module()
    info = sd.query_devices(output_device, 'output')
    max_channels = int(info['max_output_channels'])
    candidates = (2, 1) if max_channels >= 2 else (1,)
    rates = list(dict.fromkeys([int(source_rate), int(info['default_samplerate']), 48000, 44100, 16000, 8000]))
    for rate in rates:
        for channels in candidates:
            try:
                sd.check_output_settings(device=output_device, channels=channels,
                                         samplerate=rate, dtype=dtype)
                return rate, channels
            except sd.PortAudioError:
                pass
    raise ValueError('所选音箱/耳机没有可用的播放参数。' + audio_device_help())

class MonoPCMWriter:
    def __init__(self, stream, channels):
        self.stream, self.channels = stream, channels

    def write(self, chunk):
        if self.channels == 2:
            samples = np.frombuffer(chunk, dtype=np.int16)
            chunk = np.repeat(samples[:, None], 2, axis=1).tobytes()
        return self.stream.write(chunk)

def normalize_wav(source):
    """Produce real mono 16 kHz PCM16 audio, including anti-alias resampling."""
    from scipy.signal import resample_poly
    audio, rate = sf.read(source, dtype='float32', always_2d=True)
    if len(audio) == 0 or len(audio) / rate > 60:
        raise ValueError('音频必须非空且不超过60秒')
    mono = audio.mean(axis=1)
    if not np.isfinite(mono).all():
        raise ValueError('音频含无效数值')
    if rate != 16000:
        divisor = math.gcd(rate, 16000)
        mono = resample_poly(mono, 16000 // divisor, rate // divisor)
    if np.max(np.abs(mono)) < 0.0001:
        raise ValueError('音频接近静音，请检查输入设备')
    RUNTIME.mkdir(exist_ok=True)
    path = RUNTIME / f'asr-{uuid.uuid4().hex}.wav'
    sf.write(path, mono, 16000, subtype='PCM_16')
    return path

class Recorder:
    """Press calls start, release calls stop. Auto-limit also releases the device."""
    def __init__(self):
        self.lock = threading.RLock()
        self.stream = None
        self.timer = None
        self.frames = []
        self.problem = None
        self.path = None
        self.level = 0.0

    def start(self):
        with self.lock:
            if self.stream is not None:
                raise ValueError('正在录音，请先结束')
            sd = sd_module()
            rate = int(number('RECORD_SAMPLE_RATE', 16000, 8000, 192000))
            sd.check_input_settings(device=device('INPUT_DEVICE'), samplerate=rate,
                                    channels=1, dtype='int16')
            RUNTIME.mkdir(exist_ok=True)
            self.path = RUNTIME / f'record-{uuid.uuid4().hex}.wav'
            self.frames, self.problem = [], None
            self.level = 0.0
            self.rate = rate
            def receive(indata, frames, times, status):
                if status:
                    self.problem = str(status)
                self.frames.append(bytes(indata))
                samples = np.frombuffer(indata, dtype=np.int16).astype(np.float32)
                self.level = float(np.sqrt(np.mean(samples * samples)) / 32768) if samples.size else 0.0
            self.stream = sd.RawInputStream(samplerate=rate, channels=1, dtype='int16',
                                           device=device('INPUT_DEVICE'), callback=receive)
            try:
                self.stream.start()
            except Exception:
                self.stream.close()
                self.stream = None
                raise
            self.timer = threading.Timer(number('MAX_RECORD_SECONDS', 20, 1, 60), self._auto_stop)
            self.timer.daemon = True
            self.timer.start()
            return self.path.name

    def _auto_stop(self):
        try:
            self.stop()
        except Exception as exc:
            self.problem = str(exc)

    def stop(self):
        with self.lock:
            if self.stream is None:
                if self.problem:
                    raise ValueError(f'录音失败：{self.problem}')
                if self.path and self.path.exists():
                    return self.path  # release after auto-stop is idempotent
                raise ValueError('尚未开始录音')
            if self.timer:
                self.timer.cancel()
            stream, self.stream = self.stream, None
            stream.stop()
            stream.close()
            raw = b''.join(self.frames)
            self.frames = []
            if self.problem or not raw:
                raise ValueError(f'录音失败：{self.problem or "没有采集到声音"}')
            import wave
            with wave.open(str(self.path), 'wb') as file:
                file.setnchannels(1)
                file.setsampwidth(2)
                file.setframerate(self.rate)
                file.writeframes(raw)
            return self.path

class Player:
    def __init__(self):
        self.lock = threading.RLock()
        self.cancel = threading.Event()
        self.pcm_stream = None

    def begin_pcm(self, rate):
        with self.lock:
            self.stop()
            self.cancel.clear()
            sd = sd_module()
            chosen_rate, channels = output_profile(rate, 'int16', device('OUTPUT_DEVICE'))
            if chosen_rate != rate:
                raise ValueError('输出设备发生变化，请刷新设备并重新应用')
            self.pcm_stream = sd.RawOutputStream(samplerate=rate, channels=channels,
                                                dtype='int16', device=device('OUTPUT_DEVICE'))
            self.pcm_stream.start()
            return MonoPCMWriter(self.pcm_stream, channels)

    def end_pcm(self):
        with self.lock:
            if self.pcm_stream is not None:
                stream, self.pcm_stream = self.pcm_stream, None
                stream.close()

    def play(self, path):
        audio, rate = sf.read(Path(path), dtype='float32', always_2d=True)
        with self.lock:
            sd = sd_module()
            sd.stop()
            target_rate, channels = output_profile(rate, 'float32', device('OUTPUT_DEVICE'))
            if target_rate != rate:
                from scipy.signal import resample_poly
                divisor = math.gcd(rate, target_rate)
                audio = resample_poly(audio, target_rate // divisor, rate // divisor, axis=0)
                rate = target_rate
            mono = audio.mean(axis=1, keepdims=True)
            audio = np.repeat(mono, channels, axis=1)
            sd.play(audio, rate, device=device('OUTPUT_DEVICE'))
            actual_device = sd.get_stream().device
            actual_info = sd.query_devices(actual_device, 'output')
            actual_host = sd.query_hostapis(actual_info['hostapi'])['name']
            requested = device('OUTPUT_DEVICE')
            if isinstance(requested, int) and actual_device != requested:
                sd.stop()
                raise ValueError('实际打开的设备与所选设备编号不一致，已停止；请刷新列表重新应用')
        # Local speech playback is asynchronous so stop/new recording can interrupt.
        return {'status': 'started', 'file': Path(path).name,
                'output_device': actual_device, 'output_name': actual_info['name'],
                'output_interface': actual_host,
                'duration_seconds': round(len(audio) / rate, 3)}

    def stop(self):
        with self.lock:
            self.cancel.set()
            if self.pcm_stream is not None:
                self.pcm_stream.abort()
            sd_module().stop()
