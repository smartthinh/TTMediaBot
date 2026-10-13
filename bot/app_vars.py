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



_cached_ffmpeg_path: str | None = None
_cached_ffprobe_path: str | None = None


def find_binary(binary_name: str) -> str | None:
    """
    Finds the absolute path to a binary executable (e.g. 'ffmpeg' or 'ffprobe').
    Searches system PATH, frozen app folders (_internal, _internal/win32, etc.),
    sys._MEIPASS, directory, and static_ffmpeg.
    """
    import shutil

    ext = ".exe" if sys.platform == "win32" else ""
    target_file = f"{binary_name}{ext}"

    # 1. First check if it is already in PATH
    which_path = shutil.which(binary_name) or shutil.which(target_file)
    if which_path and os.path.isfile(which_path):
        return os.path.abspath(which_path)

    # 2. Gather candidate directories
    candidate_dirs: list[str] = []

    # Frozen executable directory and internal subfolders (PyInstaller / Inno Setup)
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        candidate_dirs.extend([
            exe_dir,
            os.path.join(exe_dir, "_internal"),
            os.path.join(exe_dir, "_internal", "win32"),
            os.path.join(exe_dir, "win32"),
            os.path.join(exe_dir, "_internal", "bin"),
            os.path.join(exe_dir, "bin"),
            os.path.join(exe_dir, "_internal", "ffmpeg"),
            os.path.join(exe_dir, "ffmpeg"),
            os.path.join(exe_dir, "_internal", "static_ffmpeg", "bin", "win32"),
            os.path.join(exe_dir, "static_ffmpeg", "bin", "win32"),
        ])

    # PyInstaller _MEIPASS (onefile mode)
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidate_dirs.extend([
            meipass,
            os.path.join(meipass, "win32"),
            os.path.join(meipass, "_internal", "win32"),
            os.path.join(meipass, "bin"),
            os.path.join(meipass, "static_ffmpeg", "bin", "win32"),
        ])

    # Project directory
    candidate_dirs.extend([
        directory,
        os.path.join(directory, "_internal", "win32"),
        os.path.join(directory, "_internal"),
        os.path.join(directory, "win32"),
        os.path.join(directory, "_internal", "bin"),
        os.path.join(directory, "bin"),
        os.path.join(directory, "ffmpeg"),
        os.path.join(directory, "_internal", "static_ffmpeg", "bin", "win32"),
    ])

    # static_ffmpeg module dir if available
    try:
        import static_ffmpeg.run
        candidate_dirs.append(static_ffmpeg.run.get_platform_dir())
    except Exception:
        pass

    # User data directory
    candidate_dirs.extend([
        os.path.join(data_dir, "bin"),
        os.path.join(data_dir, "ffmpeg"),
    ])

    for d in candidate_dirs:
        if d and os.path.isdir(d):
            candidate = os.path.join(d, target_file)
            if os.path.isfile(candidate):
                return os.path.abspath(candidate)

    # 3. Recursive search (up to 2 levels) in root folders as fallback
    roots_to_search: list[str] = []
    if getattr(sys, "frozen", False):
        roots_to_search.append(os.path.dirname(os.path.abspath(sys.executable)))
    if meipass:
        roots_to_search.append(meipass)
    roots_to_search.append(directory)

    for root in roots_to_search:
        if root and os.path.isdir(root):
            try:
                for current_dir, dirs, files in os.walk(root):
                    rel = os.path.relpath(current_dir, root)
                    if rel != "." and rel.count(os.sep) >= 2:
                        dirs.clear()
                    if target_file in files:
                        p = os.path.join(current_dir, target_file)
                        if os.path.isfile(p):
                            return os.path.abspath(p)
            except Exception:
                pass

    return None


def _add_to_path(folder: str) -> None:
    if folder and os.path.isdir(folder):
        current_path = os.environ.get("PATH", "")
        paths = current_path.split(os.pathsep)
        if folder not in paths:
            os.environ["PATH"] = folder + os.pathsep + current_path


def get_ffmpeg_path() -> str:
    """Returns the full path to ffmpeg executable, or 'ffmpeg' as fallback."""
    global _cached_ffmpeg_path
    if _cached_ffmpeg_path and os.path.isfile(_cached_ffmpeg_path):
        return _cached_ffmpeg_path

    found = find_binary("ffmpeg")
    if found:
        _cached_ffmpeg_path = found
        _add_to_path(os.path.dirname(found))
        return found
    return "ffmpeg"


def get_ffprobe_path() -> str:
    """Returns the full path to ffprobe executable, or 'ffprobe' as fallback."""
    global _cached_ffprobe_path
    if _cached_ffprobe_path and os.path.isfile(_cached_ffprobe_path):
        return _cached_ffprobe_path

    found = find_binary("ffprobe")
    if found:
        _cached_ffprobe_path = found
        _add_to_path(os.path.dirname(found))
        return found
    return "ffprobe"


def get_ffmpeg_dir() -> str | None:
    """Returns the directory containing ffmpeg executable, or None if not found."""
    path = get_ffmpeg_path()
    if path and path != "ffmpeg" and os.path.isfile(path):
        return os.path.dirname(path)
    return None


def ensure_ffmpeg_in_path() -> None:
    """Ensures ffmpeg and ffprobe are available in PATH and resolved."""
    get_ffmpeg_path()
    get_ffprobe_path()


ensure_ffmpeg_in_path()



