import ctypes
import logging
import os
import sys
from typing import Optional

from bot import app_vars

_us_dll: Optional[ctypes.CDLL] = None
_sapi_voice = None
_initialized = False


def _init_accessibility() -> None:
    global _us_dll, _sapi_voice, _initialized
    if _initialized:
        return
    _initialized = True

    # Attempt to load UniversalSpeech DLL
    candidate_dirs = [
        os.path.join(app_vars.directory, "UniversalSpeech"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "UniversalSpeech"),
    ]

    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", "")
        if meipass:
            candidate_dirs.append(os.path.join(meipass, "UniversalSpeech"))
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        candidate_dirs.append(os.path.join(exe_dir, "UniversalSpeech"))

    dll_loaded = False
    for d in candidate_dirs:
        dll_path = os.path.abspath(os.path.join(d, "UniversalSpeech.dll"))
        if os.path.isfile(dll_path):
            try:
                if hasattr(os, "add_dll_directory"):
                    try:
                        os.add_dll_directory(os.path.dirname(dll_path))
                    except Exception:
                        pass
                dll = ctypes.cdll.LoadLibrary(dll_path)
                dll.speechSay.argtypes = [ctypes.c_wchar_p, ctypes.c_int]
                dll.speechSay.restype = ctypes.c_int
                dll.brailleDisplay.argtypes = [ctypes.c_wchar_p]
                dll.brailleDisplay.restype = ctypes.c_int
                if hasattr(dll, "speechStop"):
                    dll.speechStop.restype = ctypes.c_int
                _us_dll = dll
                dll_loaded = True
                logging.info(f"Accessibility: Loaded UniversalSpeech DLL from {dll_path}")
                break
            except Exception as e:
                logging.debug(f"Failed to load UniversalSpeech from {dll_path}: {e}")

    # Fallback to SAPI SpVoice if Windows
    if sys.platform == "win32":
        try:
            import win32com.client
            _sapi_voice = win32com.client.Dispatch("SAPI.SpVoice")
        except Exception:
            _sapi_voice = None


def speech(text: str, interrupt: bool = False) -> bool:
    """Speaks text using active screen reader (NVDA, JAWS, SAPI, etc.)."""
    if not text:
        return False
    _init_accessibility()

    spoken = False
    if _us_dll is not None:
        try:
            ret = _us_dll.speechSay(text, 1 if interrupt else 0)
            if ret != 0:
                spoken = True
        except Exception as e:
            logging.debug(f"UniversalSpeech speechSay error: {e}")

    if not spoken and _sapi_voice is not None:
        try:
            # 1 = SVSFlagsAsync, 2 = SVSFPurgeBeforeSpeak
            flags = 1 | (2 if interrupt else 0)
            _sapi_voice.Speak(text, flags)
            spoken = True
        except Exception as e:
            logging.debug(f"SAPI Speak error: {e}")

    return spoken


def braille(text: str) -> bool:
    """Displays text on connected braille display."""
    if not text:
        return False
    _init_accessibility()

    if _us_dll is not None:
        try:
            ret = _us_dll.brailleDisplay(text)
            return ret != 0
        except Exception as e:
            logging.debug(f"UniversalSpeech brailleDisplay error: {e}")
    return False


def stop_speech() -> None:
    """Stops current speech output."""
    _init_accessibility()
    if _us_dll is not None and hasattr(_us_dll, "speechStop"):
        try:
            _us_dll.speechStop()
        except Exception:
            pass


def announce(text: str, interrupt: bool = False) -> None:
    """
    Announces a message to the user via screen reader speech and braille.
    This provides screen reader accessibility matching TeamTalk 5 client.
    """
    if not text:
        return
    try:
        speech(text, interrupt=interrupt)
        braille(text)
    except Exception as e:
        logging.debug(f"Accessibility announcement error: {e}")
