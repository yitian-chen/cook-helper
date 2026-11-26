"""Bridge browser microphone recordings to the shared kitchen-voice ASR."""
import io
import sys
import wave
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi import APIRouter, HTTPException, UploadFile

from .domain import ROOT

# The standalone voice project also runs directly from its own directory.
sys.path.insert(0, str(ROOT / "kitchen-voice"))
from voice.providers import CloudServiceError, status as voice_status

router = APIRouter(prefix="/api/voice")
MAX_BYTES = 2 * 1024 * 1024
MAX_SECONDS = 20


def transcribe(path: Path) -> str:
    from voice.cloud import transcribe as cloud_transcribe

    return cloud_transcribe(path)


@router.get("/status")
def status():
    try:
        current = voice_status("asr")
    except ValueError:
        return {"configured": False}
    return {"configured": current["configured"], "provider": current["provider"]}


def validate_wav(content: bytes):
    try:
        with wave.open(io.BytesIO(content)) as file:
            frames, rate = file.getnframes(), file.getframerate()
            if not 8000 <= rate <= 48000 or file.getnchannels() != 1 or file.getsampwidth() != 2:
                raise ValueError
            if frames > rate * MAX_SECONDS:
                raise HTTPException(413, "语音最多录制 20 秒，请分段输入")
            pcm = file.readframes(frames)
            if len(pcm) != frames * 2 or not any(pcm):
                raise ValueError
    except (wave.Error, EOFError, ValueError):
        raise HTTPException(422, "录音为空或格式无效，请重新录音") from None


@router.post("/transcribe")
def transcribe_upload(file: UploadFile):
    if not status()["configured"]:
        raise HTTPException(503, "云端语音识别尚未配置")

    content = file.file.read(MAX_BYTES + 1)
    if len(content) > MAX_BYTES:
        raise HTTPException(413, "录音文件过大，请分段输入")
    validate_wav(content)
    try:
        with TemporaryDirectory(prefix="cook-asr-") as directory:
            path = Path(directory) / "recording.wav"
            path.write_bytes(content)
            text = transcribe(path).strip()
    except ImportError:
        raise HTTPException(503, "语音模块依赖未安装，请按项目说明安装") from None
    except TimeoutError:
        raise HTTPException(504, "语音识别超时，请重试") from None
    except ValueError:
        raise HTTPException(422, "没有识别到清晰语音，请检查麦克风并重试") from None
    except (CloudServiceError, RuntimeError, OSError):
        raise HTTPException(502, "云端语音识别失败，请检查服务配置和网络后重试") from None
    if not text:
        raise HTTPException(422, "没有识别到语音，请重试")
    return {"text": text}
