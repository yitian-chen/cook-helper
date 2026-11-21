"""Local, single-process test server. Run with ONE uvicorn worker."""
import uuid
import os
import platform
import time
from pathlib import Path
from typing import Literal
from contextlib import asynccontextmanager, contextmanager
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, FileResponse
from pydantic import BaseModel, Field
from .config import artifact, device, number, RUNTIME
from .service import VoiceService
from . import cloud, agent
from .providers import status as provider_status, CloudServiceError

service = VoiceService()

@asynccontextmanager
async def lifespan(app):
    yield
    if service.recorder.stream is not None:
        service.recorder.stop()
    service.player.stop()

app = FastAPI(title='厨房助手语音模块测试接口', lifespan=lifespan)

class FileInput(BaseModel):
    file: str

class TextInput(BaseModel):
    text: str = Field(min_length=1, max_length=2000)

class PipelineInput(FileInput):
    play: bool = True

class DeviceSettings(BaseModel):
    input_device: int | None = None
    output_device: int | None = None

class ProviderSettings(BaseModel):
    asr_provider: Literal['aliyun', 'xfyun']
    tts_provider: Literal['aliyun', 'xfyun']

@app.get('/', include_in_schema=False)
def home():
    return FileResponse(Path(__file__).with_name('web') / 'index.html')

@app.get('/settings')
def settings():
    from .audio import sd_module
    sd = sd_module()
    return {'input_device': device('INPUT_DEVICE'), 'output_device': device('OUTPUT_DEVICE'),
            'default_input': sd.default.device[0], 'default_output': sd.default.device[1],
            'max_seconds': number('MAX_RECORD_SECONDS', 20, 1, 60),
            'platform': platform.system().lower(),
            **providers(),
            'agent_mode': os.getenv('AGENT_MODE', 'mock'),
            'hostapis': [dict(item) for item in sd.query_hostapis()]}

@app.get('/providers')
def providers():
    asr, tts = provider_status('asr'), provider_status('tts')
    return {'asr': asr, 'tts': tts,
            'cloud_configured': asr['configured'] and tts['configured']}

@app.post('/settings/providers')
def set_providers(body: ProviderSettings):
    with exclusive():
        service.player.stop()
        os.environ['ASR_PROVIDER'] = body.asr_provider
        os.environ['TTS_PROVIDER'] = body.tts_provider
        return {**providers(), 'message': '服务商已应用到本次运行；长期默认请修改 .env 后重启。'}

@app.post('/settings/devices')
def set_devices(body: DeviceSettings):
    from .audio import sd_module, output_profile
    with exclusive():
        sd = sd_module()
        selected = sd.query_devices(body.input_device, 'input')
        sd.query_devices(body.output_device, 'output')
        # Native recording rate avoids unsupported 16kHz devices; ASR later resamples.
        rate = int(selected['default_samplerate'])
        sd.check_input_settings(device=body.input_device, samplerate=rate, channels=1, dtype='int16')
        output_rate, output_channels = output_profile(22050, 'int16', body.output_device)
        service.player.stop()
        os.environ['INPUT_DEVICE'] = '' if body.input_device is None else str(body.input_device)
        os.environ['OUTPUT_DEVICE'] = '' if body.output_device is None else str(body.output_device)
        os.environ['RECORD_SAMPLE_RATE'] = str(rate)
        output = sd.query_devices(body.output_device, 'output')
        return {'message': '已应用到本次运行；重启后恢复 .env 配置', 'sample_rate': rate,
                'output_name': output['name'], 'output_rate': output_rate,
                'output_channels': output_channels}

@app.get('/record/level')
def level():
    return {'recording': service.recorder.stream is not None, 'level': service.recorder.level}

@app.post('/test-tone')
def test_tone():
    import numpy as np
    import soundfile as sf
    from .audio import sd_module
    with exclusive():
        rate = int(sd_module().query_devices(device('OUTPUT_DEVICE'), 'output')['default_samplerate'])
        t = np.arange(rate) / rate
        tone = (np.sin(2 * np.pi * 440 * t) * 0.08).astype('float32')
        # Fade the tone to avoid clicks.
        fade = min(int(rate * .02), len(tone) // 2)
        tone[:fade] *= np.linspace(0, 1, fade)
        tone[-fade:] *= np.linspace(1, 0, fade)
        RUNTIME.mkdir(exist_ok=True)
        path = RUNTIME / 'speaker-test.wav'
        sf.write(path, tone, rate)
        return service.player.play(path)

@app.exception_handler(Exception)
async def error(request: Request, exc: Exception):
    # SDK and HTTP exceptions may contain URL or credentials: do not expose them.
    safe = str(exc) if isinstance(exc, (ValueError, FileNotFoundError, TimeoutError, CloudServiceError)) else '操作失败，请检查配置、设备、网络及服务权限'
    from .audio import sd_module, audio_device_help
    if isinstance(exc, getattr(sd_module(), 'PortAudioError', ())):
        safe = ('本地音频设备无法打开：' + str(exc) + '。' + audio_device_help()
                + '此错误与云端密钥和网络无关。')
    return JSONResponse(status_code=400, content={'error': type(exc).__name__, 'message': safe})

@contextmanager
def exclusive(allow_recording=False):
    if not service.lock.acquire(blocking=False):
        raise ValueError('模块忙，请等当前请求完成')
    try:
        if not allow_recording and service.recorder.stream is not None:
            raise ValueError('正在录音，请先停止')
        yield
    finally:
        service.lock.release()

@app.get('/health')
def health():
    import os
    return {'status': 'ok', 'recording': service.recorder.stream is not None,
            'busy': service.lock.locked(), 'agent_mode': os.getenv('AGENT_MODE', 'mock')}

@app.get('/devices')
def devices():
    from .audio import sd_module
    return [dict(item) for item in sd_module().query_devices()]

@app.post('/devices/refresh')
def refresh_devices():
    from .audio_runtime import runtime
    with exclusive():
        service.player.stop()
        service.player.end_pcm()
        # Old indices must not be reused after a hardware rescan.
        os.environ['INPUT_DEVICE'] = ''
        os.environ['OUTPUT_DEVICE'] = ''
        items = runtime.refresh_devices()
        return {'status': 'refreshed', 'device_count': len(items),
                'message': '已重新扫描音频设备，旧设备选择已清除，请重新选择并应用'}

@app.post('/record/start')
def record_start():
    with exclusive():
        service.player.stop()
        return {'file': service.recorder.start(), 'status': 'recording'}

@app.post('/record/stop')
def record_stop():
    with exclusive(allow_recording=True):
        return {'file': service.recorder.stop().name, 'status': 'recorded'}

@app.post('/asr')
def asr(body: FileInput):
    with exclusive():
        return {'text': cloud.transcribe(artifact(body.file))}

@app.post('/agent')
def agent_test(body: TextInput):
    with exclusive():
        return agent.ask_agent(body.text, uuid.uuid4().hex)

@app.post('/tts')
def tts(body: TextInput):
    with exclusive():
        begin = time.monotonic()
        path = cloud.synthesize(body.text)
        return {'file': path.name, 'synthesis_seconds': round(time.monotonic() - begin, 3)}

@app.post('/tts/speak')
def tts_speak(body: TextInput):
    from .speech import speak
    with exclusive():
        return speak(body.text, service.player)

@app.post('/play')
def play(body: FileInput):
    with exclusive():
        return service.player.play(artifact(body.file))

@app.post('/play/stop')
def stop():
    service.player.stop()
    return {'status': 'stopped'}

@app.get('/audio/{filename}')
def audio(filename: str):
    path = artifact(filename)
    if path.suffix.lower() != '.wav' or not path.is_file():
        raise ValueError('未找到 WAV 音频')
    return FileResponse(path, media_type='audio/wav')

@app.post('/pipeline')
def pipeline(body: PipelineInput):
    return service.process(artifact(body.file), body.play)
