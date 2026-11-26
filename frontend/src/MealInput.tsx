import {useEffect, useRef, useState} from 'react';
import type {ChangeEvent} from 'react';
import './meal-input.css';

type MealInputProps = {
  draft: string;
  onDraft: (value: string) => void;
  file: File | null;
  onFile: (file: File | null) => void;
  onSend: () => void;
  busy: boolean;
  disabled: boolean;
  placeholder: string;
  sessionId?: string;
};

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

const supportedImages = 'image/jpeg,image/png,image/webp';
const maxTextLength = 12000;

export default function MealInput({
  draft, onDraft, file, onFile, onSend, busy, disabled, placeholder, sessionId,
}: MealInputProps) {
  const [listening, setListening] = useState(false);
  const [notice, setNotice] = useState('');
  const recognitionRef = useRef<Recognition | null>(null);
  const latestDraft = useRef(draft);
  const camera = useRef<HTMLInputElement>(null);
  const picker = useRef<HTMLInputElement>(null);
  const blocked = busy || disabled;
  const canSend = !blocked && !listening && !!(draft.trim() || file);

  useEffect(() => {
    latestDraft.current = draft;
  }, [draft]);

  useEffect(() => {
    setNotice('');
  }, [sessionId]);

  useEffect(() => {
    setListening(false);
    return () => {
      const recognition = recognitionRef.current;
      recognitionRef.current = null;
      recognition?.abort();
    };
  }, [sessionId, blocked]);

  function choosePhoto(event: ChangeEvent<HTMLInputElement>) {
    const selected = event.target.files?.[0];
    event.target.value = '';
    if (!selected || blocked) return;
    if (selected.size > 8 * 1024 * 1024) {
      setNotice('图片不得超过 8 MB，请换一张照片。');
      return;
    }
    setNotice('');
    onFile(selected);
  }

  function toggleVoice() {
    if (recognitionRef.current) {
      recognitionRef.current.stop();
      return;
    }
    const browser = window as SpeechWindow;
    const SpeechRecognition = browser.SpeechRecognition ?? browser.webkitSpeechRecognition;
    if (!SpeechRecognition) {
      setNotice('当前浏览器不支持语音输入，可以直接输入文字。');
      return;
    }

    const recognition = new SpeechRecognition();
    recognitionRef.current = recognition;
    recognition.lang = 'zh-CN';
    recognition.onresult = event => {
      if (recognitionRef.current !== recognition) return;

      const phrases = [];
      for (let index = event.resultIndex; index < event.results.length; index++) {
        const result = event.results[index];
        if (result.isFinal) phrases.push(result[0].transcript.trim());
      }
      const transcript = phrases.join(' ').trim();
      if (transcript) {
        const text = [latestDraft.current.trimEnd(), transcript].filter(Boolean).join(' ');
        latestDraft.current = text.slice(0, maxTextLength);
        onDraft(latestDraft.current);
      }
    };
    recognition.onerror = ({error}) => {
      if (recognitionRef.current !== recognition) return;
      setNotice(error === 'aborted' ? '' : speechErrors[error] ?? '语音输入暂时不可用，可以直接输入文字。');
    };
    recognition.onend = () => {
      if (recognitionRef.current !== recognition) return;
      recognitionRef.current = null;
      setListening(false);
    };
    try {
      recognition.start();
      setNotice('');
      setListening(true);
    } catch {
      recognitionRef.current = null;
      setNotice('无法启动语音输入，请检查麦克风权限。');
    }
  }

  return <form className="meal-input" aria-label="补充信息" onSubmit={event => {
    event.preventDefault();
    if (canSend) {
      setNotice('');
      onSend();
    }
  }}>
    {file && <div className="meal-input-attachment">
      <span title={file.name}>{file.name}</span>
      <button type="button" aria-label="移除照片" disabled={blocked} onClick={() => onFile(null)}>×</button>
    </div>}
    <textarea
      aria-label="补充食材或要求"
      maxLength={maxTextLength}
      placeholder={placeholder}
      value={draft}
      disabled={disabled}
      rows={2}
      onChange={event => {
        latestDraft.current = event.target.value;
        onDraft(event.target.value);
      }}
      onKeyDown={event => {
        if (event.key === 'Enter' && !event.shiftKey
            && !event.nativeEvent.isComposing && event.keyCode !== 229) {
          event.preventDefault();
          if (canSend) event.currentTarget.form?.requestSubmit();
        }
      }}
    />
    <input ref={camera} hidden type="file" accept="image/*" capture="environment" onChange={choosePhoto}/>
    <input ref={picker} hidden type="file" accept={supportedImages} onChange={choosePhoto}/>
    <div className="meal-input-controls">
      <button className="meal-input-tool" type="button" disabled={blocked || listening} onClick={() => camera.current?.click()}>
        <span className="meal-input-camera" aria-hidden="true"/>拍照
      </button>
      <button className="meal-input-tool" type="button" disabled={blocked || listening} onClick={() => picker.current?.click()}>
        <span className="meal-input-image" aria-hidden="true"/>选照片
      </button>
      <button className="meal-input-tool" type="button" aria-label="语音输入" aria-pressed={listening}
        disabled={blocked} onClick={toggleVoice}>
        <span className="meal-input-mic" aria-hidden="true"/>{listening ? '停止' : '语音'}
      </button>
      <button className="meal-input-send" type="submit" disabled={!canSend}>
        {busy ? '处理中' : <>发送 <span aria-hidden="true">↑</span></>}
      </button>
    </div>
    {(listening || notice) && <p className="meal-input-status" role="status">
      {listening && !notice ? '正在听，识别结果会填入输入框。' : notice}
    </p>}
  </form>;
}
