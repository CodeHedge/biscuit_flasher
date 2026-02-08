#!/usr/bin/env python3
"""
Biscuit Flash Utility - Customer-facing firmware flasher

Downloads latest firmware from cloud and flashes both ESP32-C5 and ESP32-WROOM devices.
Automatically detects devices and handles retry logic for common flash failures.

Usage:
    python biscuit_flash.py
"""

import os
import sys
import json
import time
import tempfile
import subprocess
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

USER_AGENT = "BiscuitFlashUtility/1.0"

# Configuration
MANIFEST_URL = "https://firmware.biscuitshop.us/Biscuit_V1/Prod/manifest.json"
FIRMWARE_BASE_URL = "https://firmware.biscuitshop.us/Biscuit_V1/Prod/"
CACHE_DIR = Path(tempfile.gettempdir()) / "biscuit_firmware"

# Flash settings per device type
FLASH_CONFIG = {
    "c5": {
        "chip": "esp32c5",
        "baud": "460800",
        "flash_freq": "80m",
        "name": "C5 Scanner"
    },
    "wroom": {
        "chip": "esp32",
        "baud": "921600",
        "flash_freq": "40m",
        "name": "WROOM BLE Gateway"
    }
}


def print_banner():
    """Display welcome banner."""
    print("=" * 60)
    print("    Biscuit Flash Utility")
    print("    Firmware updater for ESP32-WROOM and ESP32-C5")
    print("=" * 60)
    print()


def check_esptool():
    """Verify esptool is installed, install if needed."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "esptool", "version"],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            # Extract version from output
            version_line = result.stdout.strip().split('\n')[0]
            print(f"      esptool {version_line.split()[-1]} installed")
            return True
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    print("      Installing esptool...")
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "esptool", "pyserial"],
            capture_output=True, check=True, timeout=120
        )
        print("      esptool installed successfully")
        return True
    except subprocess.CalledProcessError as e:
        print(f"      ERROR: Failed to install esptool: {e}")
        return False


def download_manifest(retries=3):
    """Download firmware manifest from cloud."""
    for attempt in range(retries):
        try:
            req = Request(MANIFEST_URL, headers={"User-Agent": USER_AGENT})
            with urlopen(req, timeout=30) as response:
                return json.loads(response.read().decode())
        except (URLError, HTTPError) as e:
            if attempt < retries - 1:
                wait_time = 2 ** attempt  # Exponential backoff
                print(f"      Retry in {wait_time}s... ({e})")
                time.sleep(wait_time)
            else:
                raise
    return None


def download_firmware(filename, force=False):
    """Download firmware file to cache, return local path."""
    cache_path = CACHE_DIR / filename

    if cache_path.exists() and not force:
        print(f"      {filename} (cached)")
        return cache_path

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    url = FIRMWARE_BASE_URL + filename

    print(f"      Downloading {filename}...", end=" ", flush=True)
    try:
        req = Request(url, headers={"User-Agent": USER_AGENT})
        with urlopen(req, timeout=60) as response:
            with open(cache_path, "wb") as f:
                f.write(response.read())
        print("done")
        return cache_path
    except (URLError, HTTPError) as e:
        print(f"FAILED ({e})")
        return None


def list_com_ports():
    """List available COM ports using pyserial."""
    try:
        import serial.tools.list_ports
        return list(serial.tools.list_ports.comports())
    except ImportError:
        # Fallback to PowerShell if pyserial not available
        try:
            result = subprocess.run(
                ["powershell", "-ExecutionPolicy", "Bypass", "-Command",
                 "Get-CimInstance Win32_SerialPort | Select-Object DeviceID, Description | ConvertTo-Json"],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0 and result.stdout.strip():
                data = json.loads(result.stdout)
                if isinstance(data, dict):
                    data = [data]

                class PortInfo:
                    def __init__(self, device, description):
                        self.device = device
                        self.description = description or ""

                return [PortInfo(p.get("DeviceID", ""), p.get("Description", "")) for p in data]
        except Exception:
            pass
        return []


def port_exists(port):
    """Check if a COM port still exists."""
    ports = list_com_ports()
    return any(p.device == port for p in ports)


def detect_chip_type(port, timeout=15):
    """Use esptool to detect chip type on given port."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "esptool",
             "--chip", "auto", "--port", port, "chip_id"],
            capture_output=True, text=True, timeout=timeout
        )
        output = result.stdout + result.stderr

        # Parse chip type from output
        output_lower = output.lower()
        if "esp32-c5" in output_lower or "esp32c5" in output_lower:
            return "c5"
        elif "esp32-c" in output_lower or "esp32c" in output_lower:
            # Other C-series chips (C3, C6, etc.) - not our target
            return None
        elif "esp32-d" in output_lower or "esp32" in output_lower:
            # ESP32-D0WD-V3 or other ESP32 variants = WROOM
            return "wroom"
        return None
    except subprocess.TimeoutExpired:
        return None
    except Exception:
        return None


def scan_for_devices():
    """Scan all COM ports and identify Biscuit devices."""
    devices = {"c5": None, "wroom": None}
    ports = list_com_ports()

    if not ports:
        return devices

    # Sort by COM port number descending (highest first)
    def get_port_num(p):
        try:
            return int(''.join(filter(str.isdigit, p.device)))
        except ValueError:
            return 0
    ports = sorted(ports, key=get_port_num, reverse=True)

    for i, port_info in enumerate(ports):
        port = port_info.device

        print(f"      Checking {port}...", end=" ", flush=True)

        chip_type = detect_chip_type(port)

        if chip_type == "c5":
            print("ESP32-C5 detected (C5)")
            devices["c5"] = port
        elif chip_type == "wroom":
            print("ESP32 detected (WROOM)")
            devices["wroom"] = port
        else:
            print("not an ESP32 or not responding")

        # Stop scanning once we've found both devices
        if devices["c5"] and devices["wroom"]:
            break

        # Small delay between checks to let devices recover from hard reset
        if i < len(ports) - 1:
            time.sleep(0.5)

    return devices


def flash_device(device_type, port, firmware_path, erase_first=False):
    """Flash a device with the given firmware. Returns (success, error_message)."""
    config = FLASH_CONFIG[device_type]

    # Verify port still exists
    if not port_exists(port):
        return False, "Device disconnected"

    cmd = [
        sys.executable, "-m", "esptool",
        "--chip", config["chip"],
        "--port", port,
        "--baud", config["baud"],
        "--before", "default_reset",
        "--after", "hard_reset"
    ]

    if erase_first:
        # Run erase first
        erase_cmd = cmd + ["erase_flash"]
        print("      Erasing flash...", end=" ", flush=True)
        try:
            result = subprocess.run(erase_cmd, capture_output=True, text=True, timeout=120)
            if result.returncode != 0:
                return False, "Erase failed"
            print("done")
        except subprocess.TimeoutExpired:
            return False, "Erase timed out"

    # Flash command
    flash_cmd = cmd + [
        "write_flash",
        "--flash_mode", "dio",
        "--flash_freq", config["flash_freq"],
        "--flash_size", "detect",
        "0x0", str(firmware_path)
    ]

    try:
        # Run flash with live output
        process = subprocess.Popen(
            flash_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )

        output_lines = []
        for line in process.stdout:
            line = line.rstrip()
            output_lines.append(line)
            # Show progress
            if "Writing" in line or "Connecting" in line:
                print(f"      {line}")

        process.wait(timeout=300)

        if process.returncode == 0:
            return True, None
        else:
            # Parse error from output
            output = "\n".join(output_lines)
            if "Failed to connect" in output:
                return False, "Failed to connect (device not in download mode?)"
            elif "Timed out" in output:
                return False, "Connection timed out"
            elif "Permission" in output.lower():
                return False, "Permission denied (port in use by another program?)"
            else:
                return False, f"Flash failed (exit code {process.returncode})"

    except subprocess.TimeoutExpired:
        process.kill()
        return False, "Flash operation timed out"
    except Exception as e:
        return False, str(e)


def prompt_retry(device_name, port, error_msg):
    """Prompt user to retry, skip, or quit. Returns action: 'retry', 'erase', 'skip', 'rescan', 'quit'."""
    print()
    print(f"      {error_msg}")
    print()
    print("      To enter download mode: Hold BOOT button, press RESET, release BOOT")
    print()
    print("      [Enter] Retry | [E] Erase & Retry | [S] Skip | [R] Rescan | [Q] Quit")

    while True:
        try:
            choice = input("      > ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            return "quit"

        if choice == "" or choice == "r" and len(choice) == 0:
            return "retry"
        elif choice == "e":
            return "erase"
        elif choice == "s":
            return "skip"
        elif choice == "r":
            return "rescan"
        elif choice == "q":
            return "quit"
        else:
            print("      Invalid choice. Press Enter to retry, E to erase, S to skip, R to rescan, Q to quit")


def prompt_no_devices():
    """Prompt when no devices found. Returns 'rescan' or 'quit'."""
    print()
    print("      Please ensure your Biscuit devices are connected via USB.")
    print("      [Enter] Rescan | [Q] Quit")

    while True:
        try:
            choice = input("      > ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            return "quit"

        if choice == "" or choice == "r":
            return "rescan"
        elif choice == "q":
            return "quit"
        else:
            print("      Press Enter to rescan or Q to quit")


def prompt_disconnect(port):
    """Prompt when device disconnected. Returns 'rescan' or 'quit'."""
    print()
    print(f"      Device on {port} disconnected!")
    print("      Please reconnect and press Enter to rescan, or Q to quit")

    while True:
        try:
            choice = input("      > ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            return "quit"

        if choice == "" or choice == "r":
            return "rescan"
        elif choice == "q":
            return "quit"


def main():
    """Main entry point."""
    # Check for --fresh flag to force re-download
    force_download = "--fresh" in sys.argv

    if force_download and CACHE_DIR.exists():
        import shutil
        shutil.rmtree(CACHE_DIR)
        print("Cleared firmware cache.\n")

    print_banner()

    # Step 1: Check esptool
    print("[1/5] Checking esptool installation...")
    if not check_esptool():
        print("\nERROR: Could not install esptool. Please install manually:")
        print("       pip install esptool")
        return 1
    print()

    # Step 2: Download manifest
    print("[2/5] Fetching latest firmware info...")
    try:
        manifest = download_manifest()
    except (URLError, HTTPError) as e:
        print(f"\nERROR: Could not connect to firmware server")
        print(f"       {e}")
        print("\n       Please check your internet connection and try again.")
        return 1

    c5_version = manifest["c5"]["version"]
    wroom_version = manifest["wroom"]["version"]
    c5_merged = manifest["c5"].get("mergedFilename")
    wroom_merged = manifest["wroom"].get("mergedFilename")

    print(f"      C5 firmware: {c5_version}")
    print(f"      WROOM firmware: {wroom_version}")
    print()

    # Step 3: Download firmware files
    print("[3/5] Downloading firmware files...")

    if not c5_merged:
        print("\nERROR: C5 merged firmware not available in manifest")
        return 1
    if not wroom_merged:
        print("\nERROR: WROOM merged firmware not available in manifest")
        return 1

    c5_firmware = download_firmware(c5_merged, force=force_download)
    wroom_firmware = download_firmware(wroom_merged, force=force_download)

    if not c5_firmware or not wroom_firmware:
        print("\nERROR: Could not download firmware files")
        return 1

    print(f"      Cached in: {CACHE_DIR}")
    print()

    # Main flash loop
    flash_results = {"c5": None, "wroom": None}  # None=pending, True=success, False=skipped

    while True:
        # Step 4: Scan for devices
        print("[4/5] Scanning for Biscuit devices...")
        devices = scan_for_devices()

        found_any = devices["c5"] or devices["wroom"]

        if not found_any:
            print("\n      No Biscuit devices detected!")
            action = prompt_no_devices()
            if action == "quit":
                print("\nExiting.")
                return 1
            continue  # Rescan

        print()
        if devices["c5"]:
            print(f"      Found C5 on {devices['c5']}")
        if devices["wroom"]:
            print(f"      Found WROOM on {devices['wroom']}")
        print()

        # Step 5: Flash devices
        print("[5/5] Flashing firmware...")
        print()

        # Flash C5
        if devices["c5"] and flash_results["c5"] is None:
            print(f"[C5] Flashing {FLASH_CONFIG['c5']['name']} on {devices['c5']}...")

            retry_count = 0
            erase_flash = False

            while True:
                success, error = flash_device("c5", devices["c5"], c5_firmware, erase_first=erase_flash)

                if success:
                    print("      C5 flash complete!")
                    flash_results["c5"] = True
                    break
                else:
                    print(f"\n      [C5] Flash failed on {devices['c5']}")

                    # Check if device disconnected
                    if not port_exists(devices["c5"]):
                        action = prompt_disconnect(devices["c5"])
                        if action == "quit":
                            print("\nExiting.")
                            return 1
                        break  # Will rescan

                    action = prompt_retry("C5", devices["c5"], error)

                    if action == "retry":
                        erase_flash = False
                        print("\n      Retrying...")
                        continue
                    elif action == "erase":
                        erase_flash = True
                        print("\n      Erasing and retrying...")
                        continue
                    elif action == "skip":
                        flash_results["c5"] = False  # Skipped
                        print("      Skipping C5")
                        break
                    elif action == "rescan":
                        break  # Will rescan
                    elif action == "quit":
                        print("\nExiting.")
                        return 1

            print()

        # Flash WROOM
        if devices["wroom"] and flash_results["wroom"] is None:
            print(f"[WROOM] Flashing {FLASH_CONFIG['wroom']['name']} on {devices['wroom']}...")

            erase_flash = False

            while True:
                success, error = flash_device("wroom", devices["wroom"], wroom_firmware, erase_first=erase_flash)

                if success:
                    print("      WROOM flash complete!")
                    flash_results["wroom"] = True
                    break
                else:
                    print(f"\n      [WROOM] Flash failed on {devices['wroom']}")

                    # Check if device disconnected
                    if not port_exists(devices["wroom"]):
                        action = prompt_disconnect(devices["wroom"])
                        if action == "quit":
                            print("\nExiting.")
                            return 1
                        break  # Will rescan

                    action = prompt_retry("WROOM", devices["wroom"], error)

                    if action == "retry":
                        erase_flash = False
                        print("\n      Retrying...")
                        continue
                    elif action == "erase":
                        erase_flash = True
                        print("\n      Erasing and retrying...")
                        continue
                    elif action == "skip":
                        flash_results["wroom"] = False  # Skipped
                        print("      Skipping WROOM")
                        break
                    elif action == "rescan":
                        break  # Will rescan
                    elif action == "quit":
                        print("\nExiting.")
                        return 1

            print()

        # Check if we're done
        # Count what's been handled (flashed or skipped) vs still pending
        handled = [k for k, v in flash_results.items() if v is not None]
        pending = [k for k, v in flash_results.items() if v is None]

        if not pending:
            # All devices handled
            break

        # We have pending devices that weren't found
        # If we flashed at least one device, ask user if they want to continue
        if handled:
            missing = ", ".join(d.upper() for d in pending)
            print(f"\n      {missing} not found. Connect it and press Enter to scan, or Q to finish.")
            try:
                choice = input("      > ").strip().lower()
            except (KeyboardInterrupt, EOFError):
                break
            if choice == "q":
                break
            # Otherwise continue to rescan

    # Summary
    print()
    print("=" * 60)

    c5_status = flash_results["c5"]
    wroom_status = flash_results["wroom"]

    if c5_status is True and wroom_status is True:
        print("    Flash complete! Your Biscuit is ready.")
    elif c5_status is True or wroom_status is True:
        print("    Partial flash complete.")
        if c5_status is True:
            print("    - C5: SUCCESS")
        elif c5_status is False:
            print("    - C5: skipped")
        else:
            print("    - C5: not found")

        if wroom_status is True:
            print("    - WROOM: SUCCESS")
        elif wroom_status is False:
            print("    - WROOM: skipped")
        else:
            print("    - WROOM: not found")
    else:
        print("    No devices were flashed.")

    print("=" * 60)

    # Return success if at least one device was flashed
    return 0 if (c5_status is True or wroom_status is True) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n\nInterrupted by user.")
        sys.exit(1)
