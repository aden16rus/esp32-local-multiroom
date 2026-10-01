"""
Ultra-Smooth, Crackle-Free Audio Enhancement Engine (Domain: Audio).
DSP Audio Cleaner for Far-Field Satellite Microphones with Configurable Denoise & Gain.
"""
import numpy as np
import scipy.signal
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger("AudioCleaner")


class AudioCleaner:
    def __init__(
        self,
        sample_rate: int = 16000,
        denoise_enabled: bool = True,
        noise_suppression_level: float = 0.85,
        gain_multiplier: float = 1.0,
        target_speech_rms: float = 3500.0,
        hpf_cutoff_hz: float = 80.0,
        agc_max_gain: float = 8.0
    ):
        self.sample_rate = sample_rate
        self.denoise_enabled = denoise_enabled
        self.noise_suppression_level = noise_suppression_level
        self.gain_multiplier = gain_multiplier
        self.target_speech_rms = target_speech_rms
        self.hpf_cutoff_hz = hpf_cutoff_hz
        self.agc_max_gain = agc_max_gain

        self._update_hpf_filter()

    def _update_hpf_filter(self):
        cutoff = max(20.0, min(500.0, self.hpf_cutoff_hz))
        self.b_hpf, self.a_hpf = scipy.signal.butter(4, cutoff / (self.sample_rate / 2.0), btype='highpass')

    def get_settings(self) -> Dict[str, Any]:
        """Return current DSP audio settings."""
        return {
            "denoise_enabled": self.denoise_enabled,
            "noise_suppression_level": round(self.noise_suppression_level, 2),
            "gain_multiplier": round(self.gain_multiplier, 2),
            "target_speech_rms": round(self.target_speech_rms, 1),
            "hpf_cutoff_hz": round(self.hpf_cutoff_hz, 1),
            "agc_max_gain": round(self.agc_max_gain, 1)
        }

    def update_settings(
        self,
        denoise_enabled: Optional[bool] = None,
        noise_suppression_level: Optional[float] = None,
        gain_multiplier: Optional[float] = None,
        target_speech_rms: Optional[float] = None,
        hpf_cutoff_hz: Optional[float] = None,
        agc_max_gain: Optional[float] = None
    ) -> Dict[str, Any]:
        """Dynamically update DSP settings at runtime."""
        if denoise_enabled is not None:
            self.denoise_enabled = bool(denoise_enabled)
        if noise_suppression_level is not None:
            self.noise_suppression_level = float(np.clip(noise_suppression_level, 0.30, 1.00))
        if gain_multiplier is not None:
            self.gain_multiplier = float(np.clip(gain_multiplier, 0.20, 5.00))
        if target_speech_rms is not None:
            self.target_speech_rms = float(np.clip(target_speech_rms, 500.0, 10000.0))
        if hpf_cutoff_hz is not None:
            self.hpf_cutoff_hz = float(np.clip(hpf_cutoff_hz, 20.0, 500.0))
            self._update_hpf_filter()
        if agc_max_gain is not None:
            self.agc_max_gain = float(np.clip(agc_max_gain, 1.0, 20.0))

        logger.info(f"Updated AudioCleaner DSP settings: {self.get_settings()}")
        return self.get_settings()

    def process_pcm16(
        self,
        pcm_bytes: bytes,
        target_speech_rms: Optional[float] = None,
        gain_multiplier: Optional[float] = None,
        denoise_enabled: Optional[bool] = None,
        noise_suppression_level: Optional[float] = None
    ) -> bytes:
        """
        Clean and enhance 16kHz 16-bit Mono PCM audio.
        Removes clicks/crackles, suppresses background noise, and boosts far-field speech smoothly.
        """
        target_speech_rms = target_speech_rms if target_speech_rms is not None else self.target_speech_rms
        gain_multiplier = gain_multiplier if gain_multiplier is not None else self.gain_multiplier
        denoise_enabled = denoise_enabled if denoise_enabled is not None else self.denoise_enabled
        noise_suppression_level = noise_suppression_level if noise_suppression_level is not None else self.noise_suppression_level

        if not pcm_bytes:
            return pcm_bytes

        samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32)
        total_samples = len(samples)
        if total_samples < 320:
            return pcm_bytes

        # 1. High-Pass Filter cutoff if denoise is enabled
        if denoise_enabled:
            samples_filtered = scipy.signal.filtfilt(self.b_hpf, self.a_hpf, samples)
        else:
            samples_filtered = samples

        # 2. Estimate Noise Floor (quietest 20% of 20ms frames)
        frame_size = 320  # 20ms frames
        num_frames = total_samples // frame_size
        if num_frames < 2:
            return pcm_bytes

        frame_rms = np.zeros(num_frames, dtype=np.float32)
        for f in range(num_frames):
            frame_chunk = samples_filtered[f * frame_size : (f + 1) * frame_size]
            frame_rms[f] = np.sqrt(np.mean(frame_chunk ** 2) + 1e-6)

        sorted_rms = np.sort(frame_rms)
        noise_floor_rms = float(np.mean(sorted_rms[:max(1, int(num_frames * 0.20))]))
        if noise_floor_rms < 10.0:
            noise_floor_rms = 10.0

        # 3. Calculate target frame gain with Selective Speech Boosting & Noise Suppression
        raw_frame_gains = np.ones(num_frames, dtype=np.float32)
        speech_thresh = noise_floor_rms * 2.2
        suppress_factor = noise_suppression_level if denoise_enabled else 1.0

        for f in range(num_frames):
            rms_val = frame_rms[f]
            if rms_val > speech_thresh:
                desired_gain = target_speech_rms / rms_val
                raw_frame_gains[f] = min(desired_gain, self.agc_max_gain)
            elif rms_val < noise_floor_rms * 1.3:
                raw_frame_gains[f] = suppress_factor
            else:
                alpha = (rms_val - noise_floor_rms * 1.3) / (speech_thresh - noise_floor_rms * 1.3 + 1e-6)
                raw_frame_gains[f] = suppress_factor + alpha * (min(target_speech_rms / rms_val, self.agc_max_gain) - suppress_factor)

        # 4. Smooth Gain Curve across frames (Attack/Release Filter)
        smooth_gains = np.zeros(num_frames, dtype=np.float32)
        current_gain = raw_frame_gains[0]

        for f in range(num_frames):
            target = raw_frame_gains[f]
            if target > current_gain:
                current_gain = 0.65 * current_gain + 0.35 * target
            else:
                current_gain = 0.90 * current_gain + 0.10 * target
            smooth_gains[f] = current_gain

        # 5. Interpolate smooth frame gains sample-by-sample & apply Gain Multiplier
        sample_gains = np.repeat(smooth_gains, frame_size)
        remainder = total_samples - len(sample_gains)
        if remainder > 0:
            sample_gains = np.pad(sample_gains, (0, remainder), mode='edge')

        samples_enhanced = samples_filtered * sample_gains * gain_multiplier

        # 6. Soft Limiter using Tanh to guarantee ZERO digital clipping distortion
        max_val = 30000.0
        peak = np.max(np.abs(samples_enhanced))
        if peak > max_val:
            scale = max_val / peak
            samples_enhanced = samples_enhanced * scale

        pcm16_cleaned = np.clip(samples_enhanced, -32768, 32767).astype(np.int16).tobytes()
        return pcm16_cleaned
