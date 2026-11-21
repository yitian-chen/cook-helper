"""XFYun IAT v2 / online TTS v2, using authenticated TLS WebSockets.

No Spark SDK and no disabled TLS checks. Signed URLs must never be logged.
"""
import base64
import hashlib
import hmac
import json
import threading
import wave
from email.utils import formatdate
from urllib.parse import urlencode
from .config import number
from .providers import CloudServiceError, xfyun_credentials, tts_options, validate_text


def signed_url(host, path, key, secret, date=None):
    date = date or formatdate(usegmt=True)
    origin = f'host: {host}\ndate: {date}\nGET {path} HTTP/1.1'
    signature = base64.b64encode(hmac.new(secret.encode(), origin.encode(), hashlib.sha256).digest()).decode()
    authorization = (f'api_key="{key}", algorithm="hmac-sha256", '
                     f'headers="host date request-line", signature="{signature}"')
    query = urlencode({'authorization': base64.b64encode(authorization.encode()).decode(),
                       'date': date, 'host': host})
    return f'wss://{host}{path}?{query}'


def connect(kind):
    import websocket
    (app_id, key, secret), missing = xfyun_credentials(kind)
    if missing:
        raise ValueError('请在 .env 填写：' + '、'.join(missing))
    host, path = ('iat-api.xfyun.cn', '/v2/iat') if kind == 'asr' else ('tts-api.xfyun.cn', '/v2/tts')
    try:
        connection = websocket.create_connection(
            signed_url(host, path, key, secret),
            timeout=number('CLOUD_TIMEOUT_SECONDS', 45, 1, 180), enable_multithread=True)
    except Exception:
        raise CloudServiceError('讯飞连接失败：检查网络、APPID/APIKey/APISecret、服务开通状态和电脑系统时间；签名链接不会显示。') from None
    return connection, app_id


def receive(connection):
    try:
        raw = connection.recv()
        if not raw:
            raise ValueError('closed')
        event = json.loads(raw)
        code = event.get('code')
        if not isinstance(code, int):
            raise ValueError('bad code')
        if code:
            raise CloudServiceError(f'讯飞服务返回错误码 {code}；请按官方错误码表核对权限、额度、音色和参数。')
        return event.get('data') or {}
    except CloudServiceError:
        raise
    except Exception:
        raise CloudServiceError('讯飞结果接收失败或连接提前结束，请检查网络后重试；本次结果未作为完整结果保存。') from None


class Transcript:
    """Apply dynamic corrections instead of appending every partial result."""
    def __init__(self):
        self.parts = {}

    def add(self, result):
        if not result:
            return
        if result.get('pgs') == 'rpl':
            start, end = result['rg']
            for sn in list(self.parts):
                if start <= sn <= end:
                    del self.parts[sn]
        text = ''.join((word.get('cw') or [{}])[0].get('w', '') for word in result.get('ws', []))
        self.parts[int(result['sn'])] = text

    def text(self):
        return ''.join(self.parts[sn] for sn in sorted(self.parts)).strip()


def asr_frames(raw, app_id):
    if not raw or len(raw) % 2:
        raise ValueError('识别音频为空或 PCM16 数据不完整')
    for offset in range(0, len(raw), 1280):  # 40 ms, PCM16 mono 16 kHz
        first = offset == 0
        frame = {'data': {'status': 0 if first else 1, 'format': 'audio/L16;rate=16000',
                          'encoding': 'raw', 'audio': base64.b64encode(raw[offset:offset+1280]).decode()}}
        if first:
            frame.update(common={'app_id': app_id}, business={'language': 'zh_cn', 'domain': 'iat',
                         'accent': 'mandarin', 'dwa': 'wpgs', 'vad_eos': 10000})
        yield frame
    yield {'data': {'status': 2, 'format': 'audio/L16;rate=16000', 'encoding': 'raw', 'audio': ''}}


def transcribe(path):
    with wave.open(str(path), 'rb') as source:
        if (source.getframerate(), source.getnchannels(), source.getsampwidth()) != (16000, 1, 2):
            raise ValueError('讯飞识别需要16kHz单声道PCM16 WAV，请先规范化录音')
        raw = source.readframes(source.getnframes())
    if not raw or len(raw) > 16000 * 2 * 60:
        raise ValueError('讯飞识别音频必须非空且不超过60秒')
    connection, app_id = connect('asr')
    stopped, sender_failed = threading.Event(), threading.Event()

    def send():
        try:
            for frame in asr_frames(raw, app_id):
                if stopped.is_set():
                    break
                connection.send(json.dumps(frame))
                if frame['data']['status'] != 2:
                    # Match real-time input pacing, receiving on the main thread.
                    duration = len(base64.b64decode(frame['data']['audio'])) / 32000
                    if stopped.wait(duration):
                        break
        except Exception:
            sender_failed.set()
            connection.close()

    sender = threading.Thread(target=send, daemon=True)
    transcript = Transcript()
    sender.start()
    try:
        while True:
            data = receive(connection)
            transcript.add(data.get('result'))
            if data.get('status') == 2:
                break
        if sender_failed.is_set():
            raise CloudServiceError('讯飞音频上传失败，请检查网络后重试')
        text = transcript.text()
        if not text:
            raise ValueError('没有识别到文字，请重新录音')
        return text
    finally:
        stopped.set()
        connection.close()
        sender.join(timeout=2)


def tts_chunks(text, sample_rate=16000):
    validate_text(text)
    if len(text.encode('utf-8')) >= 8000:
        raise ValueError('讯飞单次播报文字的 UTF-8 编码必须小于8000字节')
    if sample_rate not in (8000, 16000):
        raise ValueError('讯飞合成仅支持8000或16000Hz；设备采样率由本地播放适配')
    connection, app_id = connect('tts')
    request = {'common': {'app_id': app_id},
               'business': {'aue': 'raw', 'auf': f'audio/L16;rate={sample_rate}',
                            'tte': 'UTF8', **tts_options()},
               'data': {'status': 2, 'text': base64.b64encode(text.encode('utf-8')).decode()}}
    size = 0
    try:
        connection.send(json.dumps(request))
        while True:
            data = receive(connection)
            try:
                chunk = base64.b64decode(data.get('audio', ''), validate=True)
            except Exception:
                raise CloudServiceError('讯飞返回的音频编码无效，本次音频未缓存') from None
            if chunk:
                size += len(chunk)
                yield chunk
            if data.get('status') == 2:
                break
        if size == 0 or size % 2:
            raise CloudServiceError('讯飞返回空音频或不完整的PCM16音频，本次音频未缓存')
    finally:
        connection.close()
