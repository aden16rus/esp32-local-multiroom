#!/usr/bin/env bash
# Flash Script for ESP32-S3 Voice Satellite Precompiled Firmware
PORT=${1:-/dev/ttyACM0}
echo "Flashing ESP32-S3 Voice Satellite on port $PORT..."
esptool.py -p "$PORT" -b 460800 --chip esp32s3 write_flash \
    --flash_mode dio --flash_freq 80m --flash_size 16MB \
    0x0 bootloader.bin \
    0x8000 partition-table.bin \
    0x10000 wake_word_test.bin \
    0x410000 srmodels.bin

