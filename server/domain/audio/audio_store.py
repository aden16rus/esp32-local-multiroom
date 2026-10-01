"""
Audio Storage & Quality Analysis Engine (Domain: Audio).
Saves 16kHz 16-bit Mono PCM audio streams into .wav files and calculates audio quality metrics.
"""
import os
import json
import wave
import time
import math
import logging
import numpy as np
from typing import Dict, List, Any, Optional

logger = logging.getLogger("AudioStore")

RECORDINGS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "recordings"))
INDEX_FILE = os.path.join(RECORDINGS_DIR, "index.json")


class AudioStore:
    def __init__(self, storage_dir: str = RECORDINGS_DIR):
        self.storage_dir = storage_dir
        os.makedirs(self.storage_dir, exist_ok=True)
        self.index_file = os.path.join(self.storage_dir, "index.json")
        self.recordings: List[Dict[str, Any]] = self._load_index()

    def _load_index(self) -> List[Dict[str, Any]]:
        if os.path.exists(self.index_file):
            try:
                with open(self.index_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Error loading recordings index.json: {e}")
        return []

    def _save_index(self):
        try:
            with open(self.index_file, "w", encoding="utf-8") as f:
                json.dump(self.recordings, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Error saving recordings index.json: {e}")

    def analyze_pcm_audio(self, pcm_bytes: bytes) -> Dict[str, float]:
        """Analyze 16-bit 16kHz Mono PCM audio for quality metrics."""
        if not pcm_bytes:
            return {"rms": 0.0, "peak": 0.0, "noise_floor": 0.0, "snr_db": 0.0, "clipping_pct": 0.0, "quality_label": "Empty"}

        samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32)
        total_samples = len(samples)
        if total_samples == 0:
            return {"rms": 0.0, "peak": 0.0, "noise_floor": 0.0, "snr_db": 0.0, "clipping_pct": 0.0, "quality_label": "Empty"}

        peak = float(np.max(np.abs(samples)))
        rms = float(np.sqrt(np.mean(samples ** 2)))

        clipping_count = int(np.sum(np.abs(samples) >= 32700))
        clipping_pct = float((clipping_count / total_samples) * 100.0)

        window_size = 1600
        num_windows = total_samples // window_size
        if num_windows > 1:
            window_rms = [
                float(np.sqrt(np.mean(samples[i * window_size : (i + 1) * window_size] ** 2)))
                for i in range(num_windows)
            ]
            window_rms.sort()
            quiet_count = max(1, int(num_windows * 0.2))
            noise_floor = float(np.mean(window_rms[:quiet_count]))
        else:
            noise_floor = min(rms, 100.0)

        if noise_floor > 1.0 and rms > noise_floor:
            snr_db = float(20.0 * math.log10(rms / noise_floor))
        else:
            snr_db = 0.0

        if clipping_pct > 2.0:
            quality_label = "Клиппинг / Перегруз"
        elif snr_db >= 18.0:
            quality_label = "Отличное (Чистый звук)"
        elif snr_db >= 10.0:
            quality_label = "Хорошее (Умеренный шум)"
        elif snr_db >= 4.0:
            quality_label = "Удовлетворительное (Высокий шум)"
        else:
            quality_label = "Низкое (Сильные помехи)"

        return {
            "rms": round(rms, 1),
            "peak": round(peak, 1),
            "noise_floor": round(noise_floor, 1),
            "snr_db": round(snr_db, 1),
            "clipping_pct": round(clipping_pct, 2),
            "quality_label": quality_label
        }

    def save_recording(
        self,
        pcm_bytes: bytes,
        device_id: str,
        area_id: str,
        transcribed_text: str,
        ha_response: str
    ) -> Dict[str, Any]:
        """Save PCM audio to WAV file and register metadata."""
        timestamp_str = time.strftime("%Y%m%d_%H%M%S")
        filename = f"cmd_{timestamp_str}_{device_id}.wav"
        file_path = os.path.join(self.storage_dir, filename)

        with wave.open(file_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(pcm_bytes)

        duration_sec = round(len(pcm_bytes) / (16000 * 2), 2)
        metrics = self.analyze_pcm_audio(pcm_bytes)

        record_entry = {
            "id": f"rec_{int(time.time() * 1000)}",
            "filename": filename,
            "url": f"/recordings/{filename}",
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "device_id": device_id,
            "area_id": area_id,
            "duration_sec": duration_sec,
            "size_bytes": len(pcm_bytes),
            "transcribed_text": transcribed_text,
            "ha_response": ha_response,
            "metrics": metrics
        }

        self.recordings.insert(0, record_entry)
        if len(self.recordings) > 100:
            old = self.recordings.pop()
            old_file = os.path.join(self.storage_dir, old["filename"])
            if os.path.exists(old_file):
                try:
                    os.remove(old_file)
                except Exception:
                    pass

        self._save_index()
        logger.info(f"Saved audio recording '{filename}' (Duration: {duration_sec}s, Quality: '{metrics['quality_label']}', SNR: {metrics['snr_db']} dB)")
        return record_entry

    def get_all_recordings(self) -> List[Dict[str, Any]]:
        return self.recordings

    def get_file_path(self, filename: str) -> Optional[str]:
        path = os.path.join(self.storage_dir, filename)
        if os.path.exists(path) and os.path.isfile(path):
            return path
        return None
