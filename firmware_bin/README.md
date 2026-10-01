# Precompiled Firmware Binaries for ESP32-S3 Voice Satellite

This directory contains the ready-to-flash binary files for the ESP32-S3 Voice Control Satellite.

## Contents
- `wake_word_test.bin`: Main satellite application firmware.
- `bootloader.bin`: Second-stage bootloader binary.
- `partition-table.bin`: Custom partition table layout.
- `srmodels.bin`: ESP-SR WakeNet neural network models partition.
- `flash.bat`: Flashing script for Windows.
- `flash.sh`: Flashing script for Linux / macOS.

## Quick Flash Instructions

### Windows
```cmd
flash.bat COM3
```

### Linux / macOS
```bash
chmod +x flash.sh
./flash.sh /dev/ttyACM0
```

