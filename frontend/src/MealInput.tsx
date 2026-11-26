import {useEffect, useRef, useState} from 'react';
import type {ChangeEvent} from 'react';
import {recognizeVoice, type VoicePhase} from './voice-input';
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

type VoiceAttempt = {controller: AbortController; stop?: () => void};

const voiceStatus: Record<VoicePhase, string> = {
  idle: '',
  starting: '正在启动麦克风…',
  listening: '正在听，点停止后会把识别结果填入输入框。',
  transcribing: '正在转写录音…',
};

const supportedImages = 'image/jpeg,image/png,image/webp';
const maxTextLength = 12000;

export default function MealInput({
  draft, onDraft, file, onFile, onSend, busy, disabled, placeholder, sessionId,
}: MealInputProps) {
  const [voicePhase, setVoicePhase] = useState<VoicePhase>('idle');
  const [notice, setNotice] = useState('');
  const voiceAttempt = useRef<VoiceAttempt | null>(null);
  const latestDraft = useRef(draft);
  const camera = useRef<HTMLInputElement>(null);
  const picker = useRef<HTMLInputElement>(null);
  const blocked = busy || disabled;
  const canSend = !blocked && voicePhase === 'idle' && !!(draft.trim() || file);

  useEffect(() => {
    latestDraft.current = draft;
  }, [draft]);

  useEffect(() => {
    setNotice('');
  }, [sessionId]);

  useEffect(() => {
    setVoicePhase('idle');
    return () => {
      const attempt = voiceAttempt.current;
      voiceAttempt.current = null;
      attempt?.controller.abort();
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

  async function toggleVoice() {
    const active = voiceAttempt.current;
    if (active) {
      if (active.stop) {
        const stop = active.stop;
        active.stop = undefined;
        setVoicePhase('transcribing');
        try {
          stop();
        } catch {
          voiceAttempt.current = null;
          active.controller.abort();
          setVoicePhase('idle');
          setNotice('无法结束录音，请重试。');
        }
      } else {
        voiceAttempt.current = null;
        active.controller.abort();
        setVoicePhase('idle');
      }
      return;
    }

    const attempt: VoiceAttempt = {controller:new AbortController()};
    voiceAttempt.current = attempt;
    setVoicePhase('starting');
    setNotice('');

    try {
      const text = (await recognizeVoice(
        attempt.controller.signal,
        stop => {
          if (voiceAttempt.current !== attempt) return;
          attempt.stop = stop;
          setVoicePhase('listening');
        },
        () => {
          if (voiceAttempt.current !== attempt) return;
          attempt.stop = undefined;
          setVoicePhase('transcribing');
        },
      )).trim();
      if (voiceAttempt.current !== attempt) return;
      if (!text) {
        setNotice('没有听清，可以再试一次。');
        return;
      }
      const updated = [latestDraft.current.trimEnd(), text].filter(Boolean).join(' ');
      latestDraft.current = updated.slice(0, maxTextLength);
      onDraft(latestDraft.current);
    } catch (error) {
      if (voiceAttempt.current !== attempt) return;
      setNotice(error instanceof Error ? error.message : '语音输入暂时不可用，可以直接输入文字。');
    } finally {
      if (voiceAttempt.current === attempt) {
        voiceAttempt.current = null;
        setVoicePhase('idle');
      }
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
      <button className="meal-input-tool" type="button" disabled={blocked || voicePhase !== 'idle'} onClick={() => camera.current?.click()}>
        <span className="meal-input-camera" aria-hidden="true"/>拍照
      </button>
      <button className="meal-input-tool" type="button" disabled={blocked || voicePhase !== 'idle'} onClick={() => picker.current?.click()}>
        <span className="meal-input-image" aria-hidden="true"/>选照片
      </button>
      <button className="meal-input-tool" type="button" aria-label="语音输入" aria-pressed={voicePhase==='listening'}
        disabled={blocked} onClick={toggleVoice}>
        <span className="meal-input-mic" aria-hidden="true"/>{voicePhase==='idle' ? '语音' : voicePhase==='listening' ? '停止' : '取消'}
      </button>
      <button className="meal-input-send" type="submit" disabled={!canSend}>
        {busy ? '处理中' : <>发送 <span aria-hidden="true">↑</span></>}
      </button>
    </div>
    {(voicePhase !== 'idle' || notice) && <p className="meal-input-status" role="status">
      {notice || voiceStatus[voicePhase]}
    </p>}
  </form>;
}
