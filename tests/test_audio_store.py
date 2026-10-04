"""
Unit test for AudioStore recording limits and cleanup.
"""
import os
import shutil
import tempfile
import pytest
from server.domain.audio.storage import AudioStore


@pytest.fixture
def temp_recordings_dir():
    temp_dir = tempfile.mkdtemp()
    yield temp_dir
    shutil.rmtree(temp_dir, ignore_errors=True)


def test_audio_store_max_recordings(temp_recordings_dir):
    store = AudioStore(storage_dir=temp_recordings_dir, max_recordings=50)
    dummy_pcm = b"\x00\x00" * 16000  # 1 second of silence PCM16

    # Save 60 recordings
    for i in range(60):
        store.save_recording(
            pcm_bytes=dummy_pcm,
            device_id=f"device_{i}",
            area_id="test_area",
            transcribed_text=f"Command {i}",
            ha_response="OK"
        )

    # Check that only 50 recordings remain in store
    recordings = store.get_all_recordings(limit=100)
    assert len(recordings) == 50
    # Latest recording should be device_59
    assert recordings[0]["device_id"] == "device_59"
    # Oldest kept recording should be device_10
    assert recordings[-1]["device_id"] == "device_10"


def test_cleanup_orphan_files(temp_recordings_dir):
    store = AudioStore(storage_dir=temp_recordings_dir, max_recordings=50)
    dummy_pcm = b"\x00\x00" * 16000

    # Save 5 recordings
    for i in range(5):
        store.save_recording(
            pcm_bytes=dummy_pcm,
            device_id=f"device_{i}",
            area_id="test_area",
            transcribed_text=f"Command {i}",
            ha_response="OK"
        )

    # Create an orphan .wav file in the directory
    orphan_file = os.path.join(temp_recordings_dir, "orphan_test.wav")
    with open(orphan_file, "wb") as f:
        f.write(dummy_pcm)

    assert os.path.exists(orphan_file)

    # Trigger cleanup
    removed_count = store.cleanup_old_recordings(max_keep=50)
    assert removed_count == 1
    assert not os.path.exists(orphan_file)
