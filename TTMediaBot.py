import socket
import sys

# Prefer IPv4 resolution for fast and reliable network calls
_orig_getaddrinfo = socket.getaddrinfo
def _ipv4_first_getaddrinfo(*args, **kwargs):
    results = _orig_getaddrinfo(*args, **kwargs)
    ipv4 = [r for r in results if r[0] == socket.AF_INET]
    return ipv4 if ipv4 else results
socket.getaddrinfo = _ipv4_first_getaddrinfo

# Support UTF-8 encoding in Windows console for music titles
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    try:
        from bot.utils import optimize_process_priority
        optimize_process_priority()
    except Exception:
        pass

from typing import Optional

from os import path

from argparse import ArgumentParser

from bot import Bot, app_vars
from bot.config import save_default_file
from bot.sound_devices import SoundDeviceManager

parser = ArgumentParser()
parser.add_argument(
    "-c",
    "--config",
    help="Path to the configuration file",
    default=None,
)
parser.add_argument("-C", "--cache", help="Path to the cache file", default=None)
parser.add_argument("-l", "--log", help="Path to the log file", default=None)
parser.add_argument(
    "--devices", help="Show available devices and exit", action="store_true"
)
parser.add_argument(
    "--default-config",
    help='Save default config to "config_default.json" and exit',
    action="store_true",
)
parser.add_argument(
    "--cli",
    help="Run bot in command-line interface mode without GUI",
    action="store_true",
)
args = parser.parse_args()


def main(
    config: Optional[str] = None,
    cache: Optional[str] = args.cache,
    log: Optional[str] = args.log,
    devices: bool = args.devices,
    default_config: bool = args.default_config,
    cli: bool = args.cli,
) -> None:
    if config is None:
        config = args.config if args.config else app_vars.get_config_path()
    if devices:
        bot = Bot(None, None, None)
        echo_sound_devices(bot.sound_device_manager)
        bot.close()
        sys.exit(0)
    elif default_config:
        save_default_file()
        print("Successfully dumped to config_default.json")
    elif cli:
        bot = Bot(config, cache, log)
        bot.initialize()
        try:
            bot.run()
        except KeyboardInterrupt:
            bot.close()
    else:
        from bot.gui import run_gui
        run_gui(config_path=config, cache_path=cache, log_path=log)


def echo_sound_devices(sound_device_manager: SoundDeviceManager):
    print("Output devices:")
    for i, device in enumerate(sound_device_manager.output_devices):
        print("\t{index}: {name}".format(index=i, name=device.name))
    print()
    print("Input devices:")
    for i, device in enumerate(sound_device_manager.input_devices):
        print("\t{index}: {name}".format(index=i, name=device.name))


def _show_crash_dialog(title: str, message: str) -> None:
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, message, title, 0x10)  # 0x10 = MB_ICONERROR
            return
        except Exception:
            pass
    print(f"[{title}] {message}", file=sys.stderr)


def _handle_fatal_exception(exc_type, exc_value, exc_traceback) -> None:
    import traceback
    tb = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))

    # Ghi log chi tiet loi vao %APPDATA%\TTMediaBot\logs\startup_crash.log
    crash_file = ""
    try:
        import os
        log_dir = app_vars.logs_dir if 'app_vars' in globals() else os.path.join(os.getenv("APPDATA", "."), "TTMediaBot", "logs")
        os.makedirs(log_dir, exist_ok=True)
        crash_file = os.path.join(log_dir, "startup_crash.log")
        import time
        with open(crash_file, "a", encoding="utf-8") as f:
            f.write(f"\n{'='*60}\nThời điểm: {time.ctime()}\nPython: {sys.version}\nLỗi:\n{tb}\n")
    except Exception:
        pass

    # Thong bao loi than thien va huong dan khac phuc
    msg = f"Đã xảy ra lỗi khi khởi động TTMediaBot:\n\n{exc_value}\n"
    if "MSVCP140" in tb or "VCRUNTIME140" in tb:
        if sys.platform == "win32":
            try:
                import ctypes
                import subprocess
                # 0x24 = MB_YESNO | MB_ICONQUESTION
                res = ctypes.windll.user32.MessageBoxW(
                    0,
                    "Máy tính của bạn đang thiếu thư viện Microsoft Visual C++ Redistributable (x64) khiến TTMediaBot không thể mở được.\n\n"
                    "Bạn có muốn chương trình tự động tải và cài đặt Visual C++ chính thức từ Microsoft ngay bây giờ không?",
                    "TTMediaBot - Thiếu Thư Viện Hệ Thống",
                    0x24,
                )
                if res == 6:  # IDYES
                    cmd = (
                        "& {[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; "
                        "$p = Join-Path $env:TEMP 'vc_redist.x64.exe'; "
                        "(New-Object Net.WebClient).DownloadFile('https://aka.ms/vs/17/release/vc_redist.x64.exe', $p); "
                        "Start-Process $p -ArgumentList '/install /quiet /norestart' -Wait}"
                    )
                    subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", cmd])
                    ctypes.windll.user32.MessageBoxW(
                        0,
                        "Đã hoàn tất cài đặt Microsoft Visual C++ Redistributable!\nVui lòng khởi động lại TTMediaBot.",
                        "Cài Đặt Thành Công",
                        0x40,  # MB_ICONINFORMATION
                    )
                    return
            except Exception:
                pass
        msg += "\n[Cách khắc phục]: Máy tính thiếu thư viện Visual C++ Redistributable (x64).\nHãy cài đặt Microsoft Visual C++ 2015-2022 Redistributable (x64)."
    elif "MFPlat.dll" in tb or "Media Foundation" in tb:
        if sys.platform == "win32":
            try:
                import ctypes
                res = ctypes.windll.user32.MessageBoxW(
                    0,
                    "Máy tính của bạn đang thiếu Windows Media Feature Pack (thường gặp ở bản Windows N hoặc Ghost/Lite).\n\n"
                    "Bạn có muốn mở Cài đặt Windows để thêm tính năng 'Media Feature Pack' ngay bây giờ không?",
                    "TTMediaBot - Thiếu Media Feature Pack",
                    0x24,
                )
                if res == 6:
                    import os
                    os.system("start ms-settings:optionalfeatures")
                    return
            except Exception:
                pass
        msg += "\n[Cách khắc phục]: Máy tính thiếu Media Feature Pack (thường gặp ở bản Windows N/Lite).\nVào Windows Settings -> Apps -> Optional features -> Cài đặt 'Media Feature Pack'."

    if crash_file:
        msg += f"\n\nChi tiết lỗi đã được lưu tại:\n{crash_file}"

    _show_crash_dialog("TTMediaBot - Lỗi Khởi Động", msg)


if __name__ == "__main__":
    sys.excepthook = _handle_fatal_exception
    try:
        main()
    except Exception:
        _handle_fatal_exception(*sys.exc_info())
        sys.exit(1)
