#!/usr/bin/env python3
"""
STT Model Downloader for Voice Control Central Server.
Downloads high-accuracy CPU-optimized Sherpa-ONNX Russian models:
- giga-am: Sber GigaAM v2 Transducer INT8 (~225 MB) - SOTA Accuracy
- zipformer-ru: Zipformer RU INT8 (~67 MB) - High Accuracy & Speed
- zipformer-small-ru: Zipformer Small INT8 (~26 MB) - Lightweight

Usage:
    python scripts/download_stt_models.py --model giga-am
    python scripts/download_stt_models.py --model zipformer-ru
    python scripts/download_stt_models.py --model all
"""
import os
import sys
import argparse
import urllib.request

MODELS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "models"))

MODELS_CONFIG = {
    "giga-am": {
        "name": "sherpa-onnx-nemo-transducer-giga-am-v2-russian-2025-04-19",
        "description": "Sber GigaAM v2 Transducer INT8 (SOTA Russian speech recognition, ~225MB)",
        "base_url": "https://huggingface.co/csukuangfj/sherpa-onnx-nemo-transducer-giga-am-v2-russian-2025-04-19/resolve/main/",
        "files": ["encoder.int8.onnx", "decoder.onnx", "joiner.onnx", "tokens.txt"]
    },
    "zipformer-ru": {
        "name": "sherpa-onnx-zipformer-ru-int8-2025-04-20",
        "description": "Zipformer RU INT8 (~67MB) - High accuracy & fast CPU execution",
        "base_url": "https://huggingface.co/csukuangfj/sherpa-onnx-zipformer-ru-int8-2025-04-20/resolve/main/",
        "files": ["encoder.int8.onnx", "decoder.onnx", "joiner.int8.onnx", "tokens.txt", "bpe.model"]
    },
    "zipformer-small-ru": {
        "name": "sherpa-onnx-streaming-zipformer-small-ru-vosk-int8-2025-08-16",
        "description": "Zipformer Small RU INT8 (~26MB) - Lightweight model",
        "base_url": "https://huggingface.co/csukuangfj/sherpa-onnx-streaming-zipformer-small-ru-vosk-int8-2025-08-16/resolve/main/",
        "files": ["encoder.int8.onnx", "decoder.onnx", "joiner.int8.onnx", "tokens.txt", "bpe.model"]
    }
}


def download_file(url: str, target_path: str):
    def progress_bar(blocks, block_size, total_size):
        if total_size <= 0:
            return
        downloaded = blocks * block_size
        percent = min(100.0, downloaded / total_size * 100.0)
        sys.stdout.write(f"\r   [{percent:5.1f}%] {os.path.basename(target_path)}")
        sys.stdout.flush()

    urllib.request.urlretrieve(url, target_path, reporthook=progress_bar)
    print()


def download_model(key: str):
    if key not in MODELS_CONFIG:
        print(f"Unknown model key: {key}")
        return

    cfg = MODELS_CONFIG[key]
    target_dir = os.path.join(MODELS_DIR, cfg["name"])
    os.makedirs(target_dir, exist_ok=True)

    print(f"\n=======================================================")
    print(f" Downloading Model: {key}")
    print(f" Description : {cfg['description']}")
    print(f" Destination : {target_dir}")
    print(f"=======================================================")

    for filename in cfg["files"]:
        filepath = os.path.join(target_dir, filename)
        if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
            print(f" [EXISTS] {filename}")
            continue

        url = cfg["base_url"] + filename
        print(f" [DOWNLOADING] {filename}...")
        try:
            download_file(url, filepath)
        except Exception as e:
            print(f"   ERROR downloading {filename}: {e}")
            return False

    print(f" Successfully ready: {cfg['name']}\n")
    return True


def main():
    parser = argparse.ArgumentParser(description="Download Russian STT models for Sherpa-ONNX")
    parser.add_argument(
        "--model",
        type=str,
        default="giga-am",
        choices=["giga-am", "zipformer-ru", "zipformer-small-ru", "all"],
        help="Model to download (default: giga-am)"
    )
    args = parser.parse_args()

    if args.model == "all":
        for m in MODELS_CONFIG.keys():
            download_model(m)
    else:
        download_model(args.model)


if __name__ == "__main__":
    main()
