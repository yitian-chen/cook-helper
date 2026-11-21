"""Stream generated PCM to speakers; cache completed speech locally.

The subprocess writes a temporary PCM file. Only complete sample frames are read.
No text, API credentials, or SDK diagnostic messages are returned to the browser.
"""
import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
import wave
from .config import ROOT, RUNTIME, number, device
from .audio import sd_module, output_profile
from .providers import provider, require_config, validate_text, tts_options, xfyun_credentials, CloudServiceError
from .pcm import PCMConverter

def cache_path(text, rate):
    if provider('tts') == 'xfyun':
        (app_id, _, _), _missing = xfyun_credentials('tts')
        identity = ['xfyun-tts-v2', app_id, tts_options(), text, rate, 'pcm16-v1']
    else:
        identity = ['aliyun', text, os.getenv('TTS_MODEL', 'cosyvoice-v3-plus'),
                os.getenv('TTS_VOICE', 'longanyang'), os.getenv('DASHSCOPE_REGION', 'cn-beijing'),
                os.getenv('DASHSCOPE_WORKSPACE', ''), rate, 'pcm16-v1']
    digest = hashlib.sha256(json.dumps(identity, ensure_ascii=True).encode()).hexdigest()
    return RUNTIME / f'tts-cache-{digest}.wav'

def output_rate():
    source_rate = 16000 if provider('tts') == 'xfyun' else 22050
    rate, _ = output_profile(source_rate, 'int16', device('OUTPUT_DEVICE'))
    return rate

def speak(text, player):
    validate_text(text)
    selected = require_config('tts')
    start = time.monotonic()
    rate = output_rate()
    source_rate = (8000 if rate == 8000 else 16000) if selected == 'xfyun' else rate
    converter = PCMConverter(source_rate, rate)
    cached = cache_path(text, source_rate)
    if cached.exists():
        import soundfile as sf
        try:
            valid = sf.info(cached).frames > 0
        except Exception:
            valid = False
        if valid:
            player.play(cached)
            return {'file': cached.name, 'status': 'started', 'cache_hit': True,
                    'playback_submit_seconds': round(time.monotonic() - start, 3),
                    'first_audio_seconds': None}
    RUNTIME.mkdir(exist_ok=True)
    temporary = RUNTIME / f'stream-{uuid.uuid4().hex}.pcm'
    error_path = temporary.with_suffix('.error.json')
    process = None
    first = None
    stream = None
    pcm = bytearray()
    try:
        # Open the device before calling a paid API; unsupported audio fails locally.
        stream = player.begin_pcm(rate)
        process = subprocess.Popen([sys.executable, '-m', 'voice.cloud'], stdin=subprocess.PIPE,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   cwd=ROOT)
        payload = {'operation': 'tts-stream', 'provider': selected, 'text': text,
                   'path': str(temporary), 'error_path': str(error_path), 'sample_rate': source_rate}
        process.stdin.write(json.dumps(payload).encode('utf-8'))
        process.stdin.close()
        deadline = start + number('CLOUD_TIMEOUT_SECONDS', 45, 1, 180)
        position = 0
        while True:
            if player.cancel.is_set():
                return {'file': None, 'status': 'cancelled', 'cache_hit': False,
                        'first_audio_seconds': first}
            if time.monotonic() > deadline:
                raise TimeoutError('流式合成超时，已终止本次播报；可缩短文字后重试')
            ended = process.poll() is not None
            if temporary.exists():
                with temporary.open('rb') as source:
                    source.seek(position)
                    # Consume up to 100ms per write so stop remains responsive.
                    available = (temporary.stat().st_size - position) // 2 * 2
                    size = min(available, (source_rate // 10) * 2)
                    chunk = source.read(size)
                if chunk:
                    position += len(chunk)
                    pcm.extend(chunk)
                    if first is None:
                        first = round(time.monotonic() - start, 3)
                    try:
                        converted = converter.convert(chunk)
                        if converted:
                            stream.write(converted)
                    except Exception:
                        if player.cancel.is_set():
                            return {'file': None, 'status': 'cancelled', 'cache_hit': False,
                                    'first_audio_seconds': first}
                        raise
                    continue
            if ended:
                if process.returncode != 0 or not pcm:
                    if error_path.exists():
                        raise CloudServiceError(json.loads(error_path.read_text(encoding='utf-8'))['message'])
                    raise RuntimeError('流式合成失败，请检查网络、密钥和模型/音色权限')
                break
            time.sleep(.03)
        if temporary.stat().st_size % 2:
            raise ValueError('云端音频数据不完整，未缓存')
        tail = converter.convert(b'', final=True)
        if tail and not player.cancel.is_set():
            stream.write(tail)
        with wave.open(str(cached), 'wb') as target:
            target.setnchannels(1)
            target.setsampwidth(2)
            target.setframerate(source_rate)
            target.writeframes(pcm)
        return {'file': cached.name, 'status': 'finished', 'cache_hit': False,
                'first_audio_seconds': first,
                'total_seconds': round(time.monotonic() - start, 3)}
    finally:
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
        player.end_pcm()
        temporary.unlink(missing_ok=True)
        error_path.unlink(missing_ok=True)
