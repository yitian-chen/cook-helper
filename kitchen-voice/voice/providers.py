"""Provider selection and public status. Never return credential values."""
import os
from .config import number

PROVIDERS = ('aliyun', 'xfyun')


class CloudServiceError(RuntimeError):
    """Only locally constructed, credential-free messages belong here."""


def provider(kind):
    if kind not in ('asr', 'tts'):
        raise ValueError('服务类型必须为 asr 或 tts')
    value = os.getenv(f'{kind.upper()}_PROVIDER', 'aliyun').strip().lower()
    if value not in PROVIDERS:
        raise ValueError(f'{kind.upper()}_PROVIDER 只能为 aliyun 或 xfyun')
    return value


def xfyun_credentials(kind):
    values, missing = [], []
    for field in ('APP_ID', 'API_KEY', 'API_SECRET'):
        specific = f'XFYUN_{kind.upper()}_{field}'
        shared = f'XFYUN_{field}'
        value = os.getenv(specific, '').strip() or os.getenv(shared, '').strip()
        values.append(value)
        if not value:
            missing.append(f'{shared}（或 {specific}）')
    return tuple(values), missing


def tts_options():
    return {'vcn': os.getenv('XFYUN_TTS_VOICE', 'xiaoyan').strip(),
            **{name: int(number('XFYUN_TTS_' + name.upper(), 50, 0, 100))
               for name in ('speed', 'volume', 'pitch')}}


def status(kind):
    selected = provider(kind)
    if selected == 'xfyun':
        _, missing = xfyun_credentials(kind)
        model = 'iat-v2' if kind == 'asr' else 'tts-v2'
        voice = tts_options()['vcn'] if kind == 'tts' else None
    else:
        missing = [key for key in ('DASHSCOPE_API_KEY', 'DASHSCOPE_WORKSPACE')
                   if not os.getenv(key, '').strip()]
        model = os.getenv('ASR_MODEL', 'paraformer-realtime-v2') if kind == 'asr' else os.getenv('TTS_MODEL', 'cosyvoice-v3-plus')
        voice = os.getenv('TTS_VOICE', 'longanyang') if kind == 'tts' else None
    return {'provider': selected, 'configured': not missing, 'missing': missing,
            'model': model, 'voice': voice}


def require_config(kind):
    current = status(kind)
    if not current['configured']:
        raise ValueError('请在 .env 填写：' + '、'.join(current['missing']))
    return current['provider']


def validate_text(text):
    if not text.strip() or len(text) > 2000:
        raise ValueError('播报文字须为1～2000字符')
    if provider('tts') == 'xfyun' and len(text.encode('utf-8')) >= 8000:
        raise ValueError('讯飞单次播报文字的 UTF-8 编码必须小于8000字节')
