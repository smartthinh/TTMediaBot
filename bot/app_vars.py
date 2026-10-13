from __future__ import annotations
import os
import sys
from typing import Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from bot.translator import Translator

app_name = "TTMediaBot"
app_version = "3.5.0"
client_name = app_name + "-V" + app_version
about_text: Callable[[Translator], str] = lambda translator: translator.translate(
    """\
Hello, I'm Smart Thinh.
This is a TTMediaBot edition for TeamTalk 5, based on João Almeida's fork.
Ported to run seamlessly on Windows with high stability and ultra-fast response.
Repository: https://github.com/smartthinh/TTMediaBot
\fOriginal Authors: Amir Gumerov, Vladislav Kopylov, Beqa Gozalishvili, Kirill Belousov.
"""
)
fallback_service = "yt"
loop_timeout = 0.01
max_message_length = 256
recents_max_lenth = 32
tt_event_timeout = 2

# Application binary / installation directory
if getattr(sys, "frozen", False):
    directory = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(sys.executable)))
else:
    directory = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# User data directory (%APPDATA%\TTMediaBot on Windows)
if sys.platform == "win32":
    data_dir = os.path.join(os.getenv("APPDATA") or os.path.expanduser("~"), "TTMediaBot")
else:
    data_dir = os.path.join(os.path.expanduser("~"), ".ttmediabot")

logs_dir = os.path.join(data_dir, "logs")
cache_dir = os.path.join(data_dir, "cache")
temp_dir = os.path.join(data_dir, "temp")
downloads_dir = os.path.join(data_dir, "downloads")
music_downloads_dir = os.path.join(downloads_dir, "music")
zip_downloads_dir = os.path.join(downloads_dir, "zips")
default_config_path = os.path.join(data_dir, "config.json")

os.makedirs(data_dir, exist_ok=True)
os.makedirs(logs_dir, exist_ok=True)
os.makedirs(cache_dir, exist_ok=True)
os.makedirs(temp_dir, exist_ok=True)
os.makedirs(downloads_dir, exist_ok=True)
os.makedirs(music_downloads_dir, exist_ok=True)
os.makedirs(zip_downloads_dir, exist_ok=True)


def get_config_path() -> str:
    """
    Returns the path to config.json.
    If the app is frozen (packaged/installed), always uses data_dir (%APPDATA%\\TTMediaBot)
    because the installation directory (e.g. Program Files) is read-only.
    In development mode, uses the project root config.json if present, otherwise data_dir.
    Ensures that an initial config file exists in data_dir by copying from template if necessary.
    """
    user_config = os.path.join(data_dir, "config.json")
    if getattr(sys, "frozen", False):
        if not os.path.exists(user_config):
            for candidate in [
                os.path.join(directory, "config.json"),
                os.path.join(directory, "config_default.json"),
            ]:
                if os.path.exists(candidate):
                    try:
                        import shutil
                        shutil.copy2(candidate, user_config)
                        break
                    except Exception:
                        pass
        return user_config
    else:
        dev_config = os.path.join(directory, "config.json")
        if os.path.exists(dev_config):
            return dev_config
        return user_config



def ensure_ffmpeg_in_path() -> None:
    """Ensures ffmpeg and ffprobe are available in PATH without creating lock files."""
    import shutil
    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        return

    candidate_dirs = []
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        candidate_dirs.append(exe_dir)
        candidate_dirs.append(os.path.join(exe_dir, "_internal"))
        candidate_dirs.append(os.path.join(exe_dir, "_internal", "static_ffmpeg", "bin", "win32"))
    candidate_dirs.append(directory)
    candidate_dirs.append(os.path.join(directory, "_internal", "static_ffmpeg", "bin", "win32"))

    try:
        import static_ffmpeg.run
        candidate_dirs.append(static_ffmpeg.run.get_platform_dir())
    except Exception:
        pass

    for d in candidate_dirs:
        if os.path.exists(d):
            ffmpeg_path = os.path.join(d, "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")
            if os.path.isfile(ffmpeg_path):
                if d not in os.environ["PATH"]:
                    os.environ["PATH"] = d + os.pathsep + os.environ["PATH"]
                break


ensure_ffmpeg_in_path()


