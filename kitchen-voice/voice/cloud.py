"""Switchable cloud adapters. Child processes provide a hard timeout."""
import base64
import json
import os
import subprocess
import sys
import uuid
import io
import wave
from pathlib import Path
from .audio import normalize_wav
from .config import ROOT, RUNTIME, number
from .providers import provider, require_config, validate_text, CloudServiceError

def collect_asr(events):
    completed, pending = [], ''
    for event in events:
        header = event.get('header') or {}
        if header.get('event') == 'task-failed':
            raise RuntimeError('语音识别服务返回 task-failed，请检查模型、权限及参数')
        sentence = ((event.get('payload') or {}).get('output') or {}).get('sentence') or {}
        if sentence.get('heartbeat') or not sentence.get('text'):
            continue
        pending = sentence['text']
        if sentence.get('sentence_end'):
            completed.append(pending)
            pending = ''
    text = ''.join(completed) + pending
    if not text.strip():
        raise ValueError('没有识别到文字，请重新录音')
    return text.strip()

def _client():
    import dashscope
    key, workspace = os.getenv('DASHSCOPE_API_KEY'), os.getenv('DASHSCOPE_WORKSPACE')
    if not key or not workspace:
        raise ValueError('请在 .env 设置 DASHSCOPE_API_KEY 和 DASHSCOPE_WORKSPACE')
    if not hasattr(dashscope, 'Client'):
        raise RuntimeError('需要 DashScope 2.x，请按 requirements.txt 安装')
    return dashscope.Client(api_key=key, workspace=workspace,
                           region=os.getenv('DASHSCOPE_REGION', 'cn-beijing'))

def _request(payload):
    try:
        result = subprocess.run([sys.executable, '-m', 'voice.cloud'],
                                input=json.dumps(payload), capture_output=True, text=True,
                                encoding='utf-8', cwd=ROOT,
                                timeout=number('CLOUD_TIMEOUT_SECONDS', 45, 1, 180))
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError('云端语音调用超时，已终止本次调用，可重试') from exc
    lines = [line[7:] for line in result.stdout.splitlines() if line.startswith('RESULT:')]
    errors = [line[6:] for line in result.stdout.splitlines() if line.startswith('ERROR:')]
    if result.returncode and errors:
        raise CloudServiceError(json.loads(errors[-1])['message'])
    if result.returncode or not lines:
        # Do not echo SDK debug output that might include credentials.
        raise RuntimeError('云端语音调用失败；检查密钥、地域、网络、额度、模型/音色及 SDK 版本')
    return json.loads(lines[-1])

def transcribe(path):
    selected = require_config('asr')
    normalized = normalize_wav(path)
    try:
        return _request({'operation': 'asr', 'provider': selected, 'path': str(normalized)})['text']
    finally:
        normalized.unlink(missing_ok=True)

def synthesize(text):
    validate_text(text)
    selected = require_config('tts')
    result = _request({'operation': 'tts', 'provider': selected, 'text': text})
    audio = base64.b64decode(result['audio'])
    RUNTIME.mkdir(exist_ok=True)
    path = RUNTIME / f'tts-{uuid.uuid4().hex}.wav'
    path.write_bytes(audio)
    # Reject mislabeled or incomplete data before playing.
    import soundfile as sf
    if sf.info(path).frames == 0:
        raise ValueError('合成音频为空')
    return path

def dispatch(request):
    operation = request['operation']
    if operation not in ('asr', 'tts', 'tts-stream'):
        raise ValueError('未知语音操作')
    kind = 'asr' if operation == 'asr' else 'tts'
    selected = request.get('provider', provider(kind))
    if selected not in ('aliyun', 'xfyun'):
        raise ValueError('未知语音服务商')
    if selected == 'xfyun':
        from .xfyun import transcribe as xfyun_asr, tts_chunks
        if operation == 'asr':
            return {'text': xfyun_asr(request['path'])}
        rate = request.get('sample_rate', 16000)
        if operation == 'tts-stream':
            with open(request['path'], 'wb') as target:
                for chunk in tts_chunks(request['text'], rate):
                    target.write(chunk)
                    target.flush()
            return None
        raw = b''.join(tts_chunks(request['text'], rate))
        buffer = io.BytesIO()
        with wave.open(buffer, 'wb') as target:
            target.setnchannels(1)
            target.setsampwidth(2)
            target.setframerate(rate)
            target.writeframes(raw)
        return {'audio': base64.b64encode(buffer.getvalue()).decode('ascii')}
    client = _client()
    if operation == 'asr':
        events = client.audio.asr.recognize_file(
            request['path'], model=os.getenv('ASR_MODEL', 'paraformer-realtime-v2'),
            format='wav', sample_rate=16000, parameters={'language_hints': ['zh']})
        result = {'text': collect_asr(events)}
    elif operation == 'tts-stream':
        with open(request['path'], 'wb') as target:
            for chunk in client.audio.tts.stream(
                request['text'], model=os.getenv('TTS_MODEL', 'cosyvoice-v3-plus'),
                voice=os.getenv('TTS_VOICE', 'longanyang'), format='pcm',
                sample_rate=request['sample_rate']):
                target.write(chunk)
                target.flush()
        return None
    else:
        audio = client.audio.tts.synthesize(
            request['text'], model=os.getenv('TTS_MODEL', 'cosyvoice-v3-plus'),
            voice=os.getenv('TTS_VOICE', 'longanyang'), format='wav', sample_rate=22050)
        result = {'audio': base64.b64encode(audio).decode('ascii')}
    return result


def main():
    request = json.loads(sys.stdin.read())
    try:
        result = dispatch(request)
        if result is not None:
            print('RESULT:' + json.dumps(result, ensure_ascii=True))
    except (CloudServiceError, ValueError) as exc:
        # Only our validated configuration/protocol messages, never a raw SDK URL.
        # A stream sidecar lets the parent display a safe error without pipe deadlocks.
        message = str(exc) if isinstance(exc, CloudServiceError) else '云端参数或配置无效，请核对 .env 和音频/文字格式'
        safe = {'message': message}
        if request.get('error_path'):
            Path(request['error_path']).write_text(json.dumps(safe), encoding='utf-8')
        print('ERROR:' + json.dumps(safe, ensure_ascii=True))
        raise SystemExit(1)

if __name__ == '__main__':
    main()
