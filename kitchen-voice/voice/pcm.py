"""Stateful PCM16 voice upsampling, independent of physical audio devices."""
import numpy as np


class PCMConverter:
    """Linear interpolation across chunk boundaries, with bounded buffering.

    Used only when XFYun's 16 kHz PCM must play on a higher-rate endpoint.
    No downsampling: that needs an anti-alias filter (file playback uses scipy).
    """
    def __init__(self, source_rate, target_rate):
        if target_rate < source_rate:
            raise ValueError('流式播放设备采样率低于语音采样率，请改用整段合成播放')
        self.source_rate, self.target_rate = source_rate, target_rate
        self.received = self.emitted = 0
        self.last = None

    def convert(self, raw, final=False):
        samples = np.frombuffer(raw, dtype='<i2').astype(np.float64)
        if self.source_rate == self.target_rate:
            return raw
        if samples.size:
            offset = self.received
            points = samples
            if self.last is not None:
                offset -= 1
                points = np.concatenate(([self.last], samples))
            self.received += samples.size
            self.last = samples[-1]
        elif self.last is not None:
            offset, points = self.received - 1, np.array([self.last])
        else:
            return b''
        # Wait for the next source sample before interpolating a boundary.
        limit = (round(self.received * self.target_rate / self.source_rate) if final
                 else ((self.received - 1) * self.target_rate) // self.source_rate + 1)
        indices = np.arange(self.emitted, limit, dtype=np.float64)
        positions = indices * self.source_rate / self.target_rate - offset
        result = np.interp(positions, np.arange(len(points)), points)
        self.emitted = limit
        return np.rint(result).clip(-32768, 32767).astype('<i2').tobytes()
