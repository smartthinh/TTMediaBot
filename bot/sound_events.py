import logging
import os
import sys
from typing import Optional

try:
    import winsound
except ImportError:
    winsound = None

from bot import app_vars


# Standard TeamTalk 5 sound event identifiers
SOUND_CHANNEL_MSG = "channel_msg"
SOUND_CHANNEL_MSG_SENT = "channel_msg_sent"
SOUND_USER_MSG = "user_msg"
SOUND_USER_MSG_SENT = "user_msg_sent"
SOUND_NEW_USER = "newuser"
SOUND_REMOVE_USER = "removeuser"
SOUND_SERVER_LOST = "serverlost"
SOUND_VOICEACT_ON = "voiceact_on"
SOUND_VOICEACT_OFF = "voiceact_off"
SOUND_HOTKEY = "hotkey"

# Fallback mappings if specific sounds are missing
SOUND_FALLBACKS = {
    SOUND_CHANNEL_MSG_SENT: SOUND_CHANNEL_MSG,
    SOUND_USER_MSG_SENT: SOUND_USER_MSG,
    SOUND_VOICEACT_ON: SOUND_HOTKEY,
    SOUND_VOICEACT_OFF: SOUND_HOTKEY,
}

_sound_cache: dict[str, Optional[str]] = {}


def get_sound_path(sound_name: str) -> Optional[str]:
    """Finds the absolute path to a sound file with multi-directory fallback."""
    if sound_name in _sound_cache:
        cached = _sound_cache[sound_name]
        if cached and os.path.isfile(cached):
            return cached

    candidate_dirs = [
        os.path.join(app_vars.directory, "sounds"),
    ]

    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", "")
        if meipass:
            candidate_dirs.append(os.path.join(meipass, "sounds"))
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        candidate_dirs.append(os.path.join(exe_dir, "sounds"))

    # Also search standard TeamTalk 5 installation folders if available
    if sys.platform == "win32":
        for env_var in ("ProgramFiles", "ProgramFiles(x86)"):
            pf = os.environ.get(env_var)
            if pf:
                candidate_dirs.append(os.path.join(pf, "TeamTalk5", "Sounds"))

    # Try exact sound name
    target_filename = f"{sound_name}.wav"
    for d in candidate_dirs:
        full_path = os.path.join(d, target_filename)
        if os.path.isfile(full_path):
            _sound_cache[sound_name] = full_path
            return full_path

    # Try fallback sound name if defined
    fallback_name = SOUND_FALLBACKS.get(sound_name)
    if fallback_name:
        fallback_filename = f"{fallback_name}.wav"
        for d in candidate_dirs:
            full_path = os.path.join(d, fallback_filename)
            if os.path.isfile(full_path):
                _sound_cache[sound_name] = full_path
                return full_path

    _sound_cache[sound_name] = None
    return None


def play_sound(sound_name: str, enabled: bool = True) -> None:
    """Plays a sound event asynchronously without blocking the UI thread."""
    if not enabled:
        return

    path = get_sound_path(sound_name)
    if not path or not os.path.isfile(path):
        return

    if sys.platform == "win32" and winsound:
        try:
            winsound.PlaySound(
                path,
                winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT,
            )
            return
        except Exception as e:
            logging.debug(f"Error playing sound '{sound_name}' with winsound: {e}")

    # Fallback to wx.adv.Sound if available
    try:
        import wx.adv
        snd = wx.adv.Sound(path)
        if snd.IsOk():
            snd.Play(wx.adv.SOUND_ASYNC)
    except Exception as e:
        logging.debug(f"Error playing sound '{sound_name}' with wx.adv.Sound: {e}")
