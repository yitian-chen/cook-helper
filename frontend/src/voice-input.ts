export type VoicePhase = 'idle' | 'starting' | 'listening' | 'transcribing';

type Recognition = {
  lang: string;
  onresult: ((event: {resultIndex: number; results: SpeechRecognitionResultList}) => void) | null;
  onerror: ((event: {error: string}) => void) | null;
  onend: (() => void) | null;
  start: () => void;
  stop: () => void;
  abort: () => void;
};

type SpeechWindow = Window & {
  SpeechRecognition?: new () => Recognition;
  webkitSpeechRecognition?: new () => Recognition;
};

const speechErrors: Record<string, string> = {
  'not-allowed': '请在浏览器中允许麦克风访问，或直接输入文字。',
  'service-not-allowed': '浏览器限制了语音输入，可以直接输入文字。',
  'audio-capture': '未检测到可用的麦克风。',
  'no-speech': '没有听清，可以再试一次。',
  'network': '语音识别服务暂时无法连接，请重试或直接输入文字。',
  'language-not-supported': '当前语音服务不支持中文识别，可以直接输入文字。',
};

const audioErrors: Record<string, string> = {
  NotAllowedError: speechErrors['not-allowed'],
  NotFoundError: speechErrors['audio-capture'],
  NotReadableError: '无法打开麦克风，请检查设备是否被其他程序占用。',
};

const sampleRate = 16000;
const maxSeconds = 20;

function checkCancelled(signal: AbortSignal) {
  if (signal.aborted) throw new DOMException('Cancelled', 'AbortError');
}

async function configuredForCloud(signal: AbortSignal) {
  try {
    const response = await fetch('/api/voice/status', {signal});
    if (response.status === 404) return false;
    if (response.ok) {
      const {configured} = await response.json();
      if (typeof configured === 'boolean') return configured;
    }
  } catch {
    checkCancelled(signal);
  }
  throw new Error('暂时无法检查语音服务，请重试或直接输入文字。');
}

function recognizeInBrowser(signal: AbortSignal, onListening: (stop: () => void) => void) {
  const browser = window as SpeechWindow;
  const SpeechRecognition = browser.SpeechRecognition ?? browser.webkitSpeechRecognition;
  if (!SpeechRecognition) {
    throw new Error('当前浏览器不支持语音输入，可以直接输入文字。');
  }

  return new Promise<string>((resolve, reject) => {
    const recognition = new SpeechRecognition();
    const phrases: string[] = [];
    const abort = () => {
      cleanup();
      recognition.abort();
      reject(new DOMException('Cancelled', 'AbortError'));
    };
    const cleanup = () => {
      signal.removeEventListener('abort', abort);
      recognition.onresult = recognition.onerror = recognition.onend = null;
    };
    recognition.lang = 'zh-CN';
    recognition.onresult = event => {
      for (let index = event.resultIndex; index < event.results.length; index++) {
        const result = event.results[index];
        if (result.isFinal) phrases.push(result[0].transcript.trim());
      }
    };
    recognition.onerror = ({error}) => {
      cleanup();
      reject(new Error(speechErrors[error] ?? '语音输入暂时不可用，可以直接输入文字。'));
    };
    recognition.onend = () => {
      cleanup();
      resolve(phrases.join(' '));
    };
    try {
      recognition.start();
      signal.addEventListener('abort', abort, {once:true});
      onListening(() => recognition.stop());
    } catch {
      cleanup();
      reject(new Error('无法启动语音输入，请检查麦克风权限。'));
    }
  });
}

async function startRecording(signal: AbortSignal) {
  if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder || !window.OfflineAudioContext) {
    throw new Error('当前页面无法录音。请使用支持录音的浏览器，通过 HTTPS 或本机地址打开。');
  }

  let stream: MediaStream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({audio:{channelCount:1}});
  } catch (error) {
    const name = error instanceof Error ? error.name : '';
    throw new Error(audioErrors[name] ?? '无法打开麦克风，请检查浏览器权限。');
  }

  const release = () => stream.getTracks().forEach(track => track.stop());
  if (signal.aborted) {
    release();
    checkCancelled(signal);
  }
  let recorder: MediaRecorder;
  try {
    const mimeType = ['audio/webm;codecs=opus', 'audio/ogg;codecs=opus', 'audio/mp4']
      .find(type => MediaRecorder.isTypeSupported(type));
    recorder = new MediaRecorder(stream, mimeType ? {mimeType} : undefined);
  } catch {
    release();
    throw new Error('当前浏览器无法录音，可以直接输入文字。');
  }

  const stop = () => {
    if (recorder.state !== 'inactive') recorder.stop();
  };
  const audio = new Promise<Blob>((resolve, reject) => {
    const parts: Blob[] = [];
    let timer: ReturnType<typeof setTimeout>;
    const cleanup = () => {
      clearTimeout(timer);
      signal.removeEventListener('abort', abort);
      recorder.ondataavailable = recorder.onstop = recorder.onerror = null;
      release();
    };
    const abort = () => {
      stop();
      cleanup();
      reject(new DOMException('Cancelled', 'AbortError'));
    };
    const fail = () => {
      cleanup();
      reject(new Error('录音中断，请重试。'));
    };
    recorder.ondataavailable = event => {
      if (event.data.size) parts.push(event.data);
    };
    recorder.onstop = () => {
      cleanup();
      resolve(new Blob(parts, {type:recorder.mimeType}));
    };
    recorder.onerror = fail;
    try {
      recorder.start();
      signal.addEventListener('abort', abort, {once:true});
      timer = setTimeout(stop, maxSeconds * 1000);
    } catch {
      fail();
    }
  });
  return {stop,audio};
}

async function pcmWav(blob: Blob) {
  if (!blob.size) throw new Error('没有录到声音，请重试。');

  let audio: AudioBuffer;
  try {
    const context = new OfflineAudioContext(1, 1, sampleRate);
    audio = await context.decodeAudioData(await blob.arrayBuffer());
  } catch {
    throw new Error('浏览器无法读取录音，请重试或直接输入文字。');
  }
  const samples = Math.min(audio.length, maxSeconds * audio.sampleRate);
  const channels = Array.from({length:audio.numberOfChannels}, (_, index) => audio.getChannelData(index));
  const wav = new ArrayBuffer(44 + samples * 2);
  const view = new DataView(wav);
  function tag(offset: number, text: string) {
    for (let index = 0; index < text.length; index++) view.setUint8(offset + index, text.charCodeAt(index));
  }
  tag(0, 'RIFF');
  view.setUint32(4, wav.byteLength - 8, true);
  tag(8, 'WAVE');
  tag(12, 'fmt ');
  view.setUint32(16, 16, true); // PCM format chunk length
  view.setUint16(20, 1, true); // PCM encoding
  view.setUint16(22, 1, true); // mono
  view.setUint32(24, audio.sampleRate, true);
  view.setUint32(28, audio.sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  tag(36, 'data');
  view.setUint32(40, samples * 2, true);

  for (let index = 0; index < samples; index++) {
    let sum = 0;
    for (const channel of channels) sum += channel[index];
    const amplitude = Math.max(-1, Math.min(1, sum / channels.length));
    view.setInt16(44 + index * 2, Math.round(amplitude * (amplitude < 0 ? 32768 : 32767)), true);
  }
  return new Blob([wav], {type:'audio/wav'});
}

export async function recognizeVoice(
  signal: AbortSignal, onListening: (stop: () => void) => void, onTranscribing: () => void,
) {
  const cloud = await configuredForCloud(signal);
  checkCancelled(signal);
  if (!cloud) return recognizeInBrowser(signal, onListening);

  const {stop,audio} = await startRecording(signal);
  onListening(stop);
  const recording = await audio;
  checkCancelled(signal);
  onTranscribing();

  const file = await pcmWav(recording);
  checkCancelled(signal);
  const body = new FormData();
  body.append('file', file, 'speech.wav');
  let response: Response;
  try {
    response = await fetch('/api/voice/transcribe', {method:'POST', body, signal});
  } catch {
    checkCancelled(signal);
    throw new Error('语音服务暂时无法连接，请重试或直接输入文字。');
  }
  const data = await response.json().catch(() => null);
  const text: unknown = data?.text;
  if (!response.ok || typeof text !== 'string') {
    throw new Error(typeof data?.detail === 'string' ? data.detail : '语音转写失败，请重试或直接输入文字。');
  }
  return text;
}
