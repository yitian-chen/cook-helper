import os
import httpx
from .config import number

def ask_agent(text, request_id):
    if not text.strip():
        raise ValueError('不能发送空文字给 Agent')
    mode = os.getenv('AGENT_MODE', 'mock')
    if mode == 'mock':
        return {'request_id': request_id, 'mode': 'mock',
                'reply_text': f'这是模拟接口，收到：{text}。尚未连接真实 Agent，也没有执行视频操作。'}
    if mode != 'http':
        raise ValueError('AGENT_MODE 只能是 mock 或 http')
    headers = {}
    if os.getenv('AGENT_TOKEN'):
        headers['Authorization'] = 'Bearer ' + os.environ['AGENT_TOKEN']
    response = httpx.post(os.environ['AGENT_URL'],
                          json={'text': text, 'request_id': request_id}, headers=headers,
                          timeout=number('AGENT_TIMEOUT_SECONDS', 30, 1, 180))
    response.raise_for_status()
    data = response.json()
    if data.get('request_id') != request_id:
        raise ValueError('Agent 返回的 request_id 不匹配')
    if not isinstance(data.get('reply_text'), str) or not data['reply_text'].strip():
        raise ValueError('Agent 必须返回非空 reply_text')
    return {**data, 'mode': 'http'}
