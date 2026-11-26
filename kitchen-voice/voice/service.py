import threading
import time
import uuid
from .audio import Recorder, Player
from .cloud import transcribe, synthesize
from .agent import ask_agent
from .speech import speak

class VoiceService:
    def __init__(self):
        self.recorder, self.player = Recorder(), Player()
        self.lock = threading.Lock()

    def start_recording(self):
        with self.lock:
            self.player.stop()
            return self.recorder.start()

    def finish_recording(self):
        with self.lock:
            return self.recorder.stop()

    def process(self, path, play=True):
        if not self.lock.acquire(blocking=False):
            raise ValueError('语音模块正在处理另一请求')
        try:
            if self.recorder.stream is not None:
                raise ValueError('请先结束录音')
            request_id = uuid.uuid4().hex
            timings = {}
            begin = time.monotonic()
            text = transcribe(path)
            timings['asr_seconds'] = round(time.monotonic() - begin, 3)
            begin = time.monotonic()
            reply = ask_agent(text, request_id)
            timings['agent_seconds'] = round(time.monotonic() - begin, 3)
            begin = time.monotonic()
            if play:
                playback = speak(reply['reply_text'], self.player)
                audio_file = playback['file']
            else:
                audio_file = synthesize(reply['reply_text']).name
                playback = {'status': 'not_requested'}
            timings['tts_and_playback_seconds'] = round(time.monotonic() - begin, 3)
            return {'request_id': request_id, 'text': text, 'agent': reply,
                    'audio_file': audio_file, 'timings': timings, 'playback': playback}
        finally:
            self.lock.release()
