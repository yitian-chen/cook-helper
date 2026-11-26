import io
from pathlib import Path
import struct
import wave

import pytest
from fastapi.testclient import TestClient

from app import store, voice_input
from app.main import app


def pcm_wav(seconds=1, *, sample_width=2):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(sample_width)
        audio.setframerate(16000)
        frame_count = seconds * audio.getframerate()
        if sample_width == 2:
            pulse = struct.pack("<hhhh", 1200, 200, -1200, -200)
            audio.writeframes(pulse * (frame_count // 4))
        else:
            audio.writeframes(b"\x00" * sample_width * frame_count)
    return buffer.getvalue()


def upload(client, content):
    return client.post("/api/voice/transcribe", files={
        "file": ("microphone.wav", content, "audio/wav"),
    })


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB", tmp_path / "voice-tests.sqlite")
    monkeypatch.setattr(voice_input, "voice_status", lambda kind: {
        "configured": True, "provider": "xfyun", "api_key": "private-status-test-key",
    })
    with TestClient(app) as client:
        yield client


def test_status_does_not_expose_provider_configuration(client, monkeypatch):
    response = client.get("/api/voice/status")
    assert response.status_code == 200
    assert response.json() == {"configured": True, "provider": "xfyun"}

    def invalid_provider(kind):
        raise ValueError("private-provider-value")

    monkeypatch.setattr(voice_input, "voice_status", invalid_provider)
    response = client.get("/api/voice/status")
    assert response.status_code == 200
    assert response.json()["configured"] is False
    assert "private-provider-value" not in response.text


def test_transcribes_valid_wav_and_removes_temp_file(client, monkeypatch):
    wav = pcm_wav()
    submitted = []

    def transcribe(path):
        path = Path(path)
        assert path.read_bytes() == wav
        submitted.append(path)
        return "加两个鸡蛋"

    monkeypatch.setattr(voice_input, "transcribe", transcribe)
    response = upload(client, wav)
    assert response.status_code == 200
    assert response.json() == {"text": "加两个鸡蛋"}
    assert len(submitted) == 1
    assert not submitted[0].exists()


@pytest.mark.parametrize(("case", "status"), [
    ("invalid", 422),
    ("empty", 422),
    ("truncated", 422),
    ("eight-bit", 422),
    ("long", 413),
    ("large", 413),
])
def test_invalid_recordings_never_reach_provider(client, monkeypatch, case, status):
    content = {
        "invalid": lambda: b"this is not a WAV file",
        "empty": lambda: pcm_wav(seconds=0),
        "truncated": lambda: pcm_wav()[:-12],
        "eight-bit": lambda: pcm_wav(sample_width=1),
        "long": lambda: pcm_wav(seconds=21),
        "large": lambda: b"\x00" * (2 * 1024 * 1024 + 1),
    }[case]()

    def transcribe(path):
        pytest.fail("Invalid audio was sent to the provider")

    monkeypatch.setattr(voice_input, "transcribe", transcribe)
    assert upload(client, content).status_code == status


def test_missing_configuration_never_calls_provider(client, monkeypatch):
    monkeypatch.setattr(voice_input, "voice_status", lambda kind: {
        "configured": False, "provider": "xfyun",
    })

    def transcribe(path):
        pytest.fail("Unconfigured provider received audio")

    monkeypatch.setattr(voice_input, "transcribe", transcribe)
    assert client.get("/api/voice/status").json()["configured"] is False
    assert upload(client, pcm_wav()).status_code == 503


@pytest.mark.parametrize(("error_type", "status"), [
    (RuntimeError, 502),
    (voice_input.CloudServiceError, 502),
    (TimeoutError, 504),
    (ModuleNotFoundError, 503),
    (ValueError, 422),
])
def test_upstream_failure_does_not_expose_error_or_keep_audio(client, monkeypatch, error_type, status):
    submitted = []

    def transcribe(path):
        submitted.append(Path(path))
        raise error_type("private-upstream-test-key")

    monkeypatch.setattr(voice_input, "transcribe", transcribe)
    response = upload(client, pcm_wav())
    assert response.status_code == status
    assert "private-upstream-test-key" not in response.text
    assert len(submitted) == 1
    assert not submitted[0].exists()
