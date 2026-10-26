import gettext
import os
from typing import List

from bot import app_vars, errors


class Translator:
    def __init__(self, language: str) -> None:
        self._locale = "en"
        self.set_locale(language)

    def get_locale(self) -> str:
        return self._locale

    def _get_locales_dir(self) -> str:
        import sys
        candidates = [
            os.path.join(app_vars.directory, "locale"),
        ]
        if getattr(sys, "frozen", False):
            exe_dir = os.path.dirname(os.path.abspath(sys.executable))
            candidates.extend([
                os.path.join(exe_dir, "locale"),
                os.path.join(exe_dir, "_internal", "locale"),
            ])
        for c in candidates:
            if c and os.path.exists(c) and os.path.isdir(c):
                return c
        return os.path.join(app_vars.directory, "locale")

    def get_locales(self) -> List[str]:
        locales_path = self._get_locales_dir()
        if not os.path.exists(locales_path) or not os.path.isdir(locales_path):
            return ["en"]
        try:
            return ["en"] + [
                d
                for d in os.listdir(locales_path)
                if os.path.isdir(os.path.join(locales_path, d))
            ]
        except Exception:
            return ["en"]

    def set_locale(self, locale: str) -> None:
        if locale in self.get_locales() or locale == "en":
            self._locale = locale
            self.translation = gettext.translation(
                "TTMediaBot",
                self._get_locales_dir(),
                languages=[locale],
                fallback=True,
            )
        else:
            raise errors.LocaleNotFoundError()

    def translate(self, message: str) -> str:
        return self.translation.gettext(message)
