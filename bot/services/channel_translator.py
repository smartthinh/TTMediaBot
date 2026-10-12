import json
import logging
from typing import Tuple
from urllib import parse, request

API_KEY = "AIzaSyDLEeFI5OtFBwYBIoK_jj5m32rZK5CkCXA"
CLIENT_NAME = "gtx"
TRANSLATE_URL = "https://translate-pa.googleapis.com/v1/translate"
DATA_TYPES = ("TRANSLATION", "SENTENCE_SPLITS")
HEADERS = {
    "User-Agent": "Mozilla/5.0",
}


def is_same_or_parent_language(detected: str, target: str) -> bool:
    """Checks if the detected language is the same as or shares the base language with target."""
    if not detected or not target:
        return False
    d = detected.strip().lower().replace("_", "-")
    t = target.strip().lower().replace("_", "-")
    if d == t:
        return True
    if d.split("-")[0] == t.split("-")[0]:
        return True
    return False


def translate_channel_text(
    text: str, lang_from: str = "auto", lang_to: str = "en"
) -> Tuple[str, str]:
    """Translates text using Google Translate internal API from Instant Translate.

    Returns:
        Tuple[str, str]: (translated_text, detected_language)
    """
    clean_text = (text or "").strip()
    if not clean_text:
        return "", ""

    params = [
        ("params.client", CLIENT_NAME),
        ("query.source_language", lang_from),
        ("query.target_language", lang_to),
        ("query.display_language", lang_to),
        ("query.text", clean_text),
        ("key", API_KEY),
        ("data_types", "TRANSLATION"),
        ("data_types", "SENTENCE_SPLITS"),
    ]

    url = f"{TRANSLATE_URL}?{parse.urlencode(params)}"
    req = request.Request(url, headers=HEADERS)

    try:
        with request.urlopen(req, timeout=8) as response:
            raw_data = response.read().decode("utf-8", errors="replace")
            data = json.loads(raw_data)
    except Exception as e:
        logging.error(f"[channel_translator] Error contacting translation API: {e}")
        return "", ""

    translated = ""
    detected = ""

    if isinstance(data, dict):
        if "translation" in data and isinstance(data["translation"], str):
            translated = data["translation"]
        elif "sentences" in data and isinstance(data["sentences"], list):
            translated = "".join(
                s.get("trans", "") for s in data["sentences"] if isinstance(s, dict)
            )

        if "sourceLanguage" in data and isinstance(data["sourceLanguage"], str):
            detected = data["sourceLanguage"]
        elif "detectedLanguages" in data and isinstance(data["detectedLanguages"], dict):
            srclangs = data["detectedLanguages"].get("srclangs")
            if srclangs and isinstance(srclangs, list) and len(srclangs) > 0:
                detected = str(srclangs[0])

    elif isinstance(data, list):
        if len(data) > 0 and isinstance(data[0], str):
            translated = data[0]
        elif len(data) > 1 and isinstance(data[1], list):
            translated = "".join(
                s[0]
                for s in data[1]
                if isinstance(s, (list, tuple)) and len(s) > 0 and isinstance(s[0], str)
            )

        if len(data) > 5 and isinstance(data[5], str):
            detected = data[5]

    if not detected:
        detected = lang_from if lang_from != "auto" else ""

    return translated, detected
