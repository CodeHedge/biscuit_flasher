# Biscuit Flash Utility

Customer-facing tool to download and flash the latest Biscuit firmware to your ESP32 devices.

## Requirements

- Python 3.7 or later
- USB cables for both ESP32 devices
- Windows 10/11

## Quick Start

1. Connect both Biscuit devices (WROOM and C5) via USB
2. Run the flash utility:
   ```
   python biscuit_flash.py
   ```
3. Follow the on-screen prompts

## What It Does

The utility automatically:
1. Downloads the latest firmware from the cloud
2. Detects your connected Biscuit devices
3. Flashes both the C5 (scanner) and WROOM (BLE gateway)

## Troubleshooting

### Device Not Detected

If a device isn't detected:
- Ensure the USB cable is connected
- Try a different USB port
- Check that drivers are installed (CH340 or CP210x)

### Flash Failed - Not in Download Mode

If flashing fails with "device not in download mode":
1. Locate the BOOT and RESET buttons on the device
2. Hold down the BOOT button
3. Press and release the RESET button
4. Release the BOOT button
5. Press Enter to retry

### Permission Denied

If you see "permission denied" errors:
- Close any serial monitors (Arduino IDE, PuTTY, etc.)
- Ensure no other program is using the COM port

## Controls

During the flash process:
- `Enter` - Retry the current operation
- `E` - Erase flash and retry (use if flash is corrupted)
- `S` - Skip this device and continue
- `R` - Rescan for devices
- `Q` - Quit the utility

## Support

If you continue to have issues, contact support with:
- The error message shown
- Your Windows version
- The COM port numbers detected
