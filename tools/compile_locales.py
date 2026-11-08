#!/usr/bin/env python3
"""Extract, update and compile the TTMediaBot translations.

Existing translations are kept: `update` only adds new strings and never
guesses (no fuzzy matching). A string whose source text changed becomes an
empty entry; the old one is kept as an obsolete (#~) entry so nothing is lost.

Two Babel problems this script works around:

1. Fuzzy matching (`babel update` default) copies the translation of a
   *different* similar string into a changed one and marks it fuzzy. The copy
   is wrong, and `compile` skips fuzzy entries, so the string disappears.
   -> `--no-fuzzy-matching`.

2. The form feed (\\f) in the About text. `extract` writes it as a raw
   character, Poedit writes it as the escape \\f, and Babel reads these as two
   different strings, so the old translation turned into an obsolete (#~)
   entry and the About text was never translated. Babel also rewrites \\f as
   \\\\f and compiles it as "backslash + f".
   -> every file is normalised to the plain \\f escape, and compiling is done
      with the Babel API, which converts \\f back to a real form feed.

3. Strings that Babel's own Python extractor cannot see. On Python < 3.12 an
   f-string is a single token, so `f"{translate('Channel:')} {name}"` is never
   extracted, and the helper `_t(...)` was not a keyword at all. Those strings
   were missing from the .pot and ended up as obsolete (#~) entries.
   -> extraction uses `extract_python_ast` (below), which reads the code with
      `ast` and finds every call, also inside f-strings, on any Python
      version; `_t` is added to the keywords.

4. `babel update` never brings an obsolete (#~) entry back, even when the
   same string is found in the source again. The old translation stayed
   hidden while an empty entry was created.
   -> `restore_obsolete()` copies the old translation back (never over an
      entry that already has a translation).
"""

import ast
import difflib
import os
import subprocess
import sys
import tempfile

from babel.messages.mofile import write_mo
from babel.messages.pofile import read_po, write_po

# Avoid UnicodeEncodeError on the Windows console when printing Vietnamese.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

cd = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
locale_path = os.path.join(cd, "locale")
pot_file_path = os.path.join(locale_path, "TTMediaBot.pot")
source_paths = [os.path.join(cd, "bot"), os.path.join(cd, "TTMediaBot.py")]
locale_domain = "TTMediaBot"
babel_cmd = [sys.executable, "-m", "babel.messages.frontend"]

# Functions whose first argument is a translatable string.
KEYWORDS = "translate translate_func translate_noop _t"

FORM_FEED = "\x0c"  # the real character
FF_ESCAPE = "\\f"  # backslash + f, as written in .po files
FF_DOUBLE = "\\\\f"  # what Babel wrongly writes


def run(args):
    # No shell: paths containing spaces are passed safely.
    env = dict(os.environ)
    tools_dir = os.path.dirname(os.path.abspath(__file__))
    env["PYTHONPATH"] = tools_dir + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.call(babel_cmd + args, env=env)


def _po_files():
    for lang in sorted(os.listdir(locale_path)):
        po_path = os.path.join(
            locale_path, lang, "LC_MESSAGES", locale_domain + ".po"
        )
        if os.path.isfile(po_path):
            yield lang, po_path


def _fix_formfeed(path):
    """Normalise the form feed to the plain \\f escape in a .po/.pot file."""
    with open(path, "r", encoding="utf-8", newline="") as f:
        text = f.read()
    fixed = text.replace(FORM_FEED, FF_ESCAPE).replace(FF_DOUBLE, FF_ESCAPE)
    if fixed != text:
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(fixed)


def extract_python_ast(fileobj, keywords, comment_tags, options):
    """Babel extractor: find translatable strings with `ast`.

    Unlike Babel's built-in Python extractor, this also finds calls placed
    inside f-strings on Python < 3.12. Used through the mapping file written
    by extract().
    """
    source = fileobj.read()
    if isinstance(source, bytes):
        source = source.decode(options.get("encoding", "utf-8"), "replace")
    source = source.replace("\r\n", "\n").replace("\r", "\n")
    lines = source.split("\n")
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return

    def literal(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        # Like Babel's own extractor, also take the text of "text".format(...)
        # and "text" % value, e.g. translate("{service} is disabled".format(...)).
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "format"
        ):
            return literal(node.func.value)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
            return literal(node.left)
        return None

    def comments_for(lineno):
        found = []
        i = lineno - 2  # the line above the call (0-based)
        while i >= 0 and lines[i].lstrip().startswith("#"):
            text = lines[i].lstrip()[1:].strip()
            found.insert(0, text)
            i -= 1
        if any(text.startswith(tag.strip()) for text in found for tag in comment_tags):
            return found
        return []

    results = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else (
            func.attr if isinstance(func, ast.Attribute) else None
        )
        if name is None or name not in keywords:
            continue
        # Leading string-literal arguments; Babel itself picks the ones it
        # needs (message, plural) according to the keyword specification.
        texts = []
        for arg in node.args:
            value = literal(arg)
            if value is None:
                break
            texts.append(value)
        if not texts:
            continue
        message = texts[0] if len(texts) == 1 else tuple(texts)
        results.append((node.lineno, node.col_offset, name, message, comments_for(node.lineno)))
    for lineno, _col, name, message, comments in sorted(results, key=lambda r: (r[0], r[1])):
        yield lineno, name, message, comments


def extract():
    module = os.path.splitext(os.path.basename(__file__))[0]
    mapping = (
        "[extractors]\n"
        f"ast = {module}:extract_python_ast\n\n"
        "[ast: **.py]\n"
    )
    fd, mapping_path = tempfile.mkstemp(suffix=".cfg", text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(mapping)
        code = run(
            [
                "extract",
                *source_paths,
                "-o",
                pot_file_path,
                "-F",
                mapping_path,
                f"--keywords={KEYWORDS}",
                "-c",
                "translators:",
                "--copyright-holder=TTMediaBot-team",
                "--project=TTMediaBot",
            ]
        )
    finally:
        os.remove(mapping_path)
    if code:
        sys.exit(
            "Extracting strings failed. Make sure Babel is installed "
            "(pip install babel) and that the source paths are correct."
        )
    _fix_formfeed(pot_file_path)


def update():
    for _lang, po_path in _po_files():
        _fix_formfeed(po_path)
    code = run(
        [
            "update",
            "-i",
            pot_file_path,
            "-d",
            locale_path,
            "-D",
            locale_domain,
            "--update-header-comment",
            "--no-fuzzy-matching",  # never guess translations for changed strings
        ]
    )
    if code:
        sys.exit(code)
    for lang, po_path in _po_files():
        _fix_formfeed(po_path)
        restore_obsolete(po_path, lang)
        _fix_formfeed(po_path)


def restore_obsolete(po_path, lang):
    """Bring back obsolete (#~) translations whose string is in the source again."""
    with open(po_path, "rb") as f:
        catalog = read_po(f, locale=lang)
    restored = []
    for message in list(catalog):
        if not message.id:
            continue
        has_translation = message.string and (
            not isinstance(message.string, (tuple, list)) or any(message.string)
        )
        if has_translation:
            continue
        key = message.id if isinstance(message.id, str) else message.id[0]
        old = catalog.obsolete.get(message.id) or catalog.obsolete.get(key)
        if old is None or not old.string:
            continue
        message.string = old.string
        message.flags = set(old.flags)
        del catalog.obsolete[message.id if message.id in catalog.obsolete else key]
        restored.append(key)
    if restored:
        with open(po_path, "wb") as f:
            write_po(f, catalog, width=76, include_previous=False)
        for text in restored:
            print(f"[{lang}] restored obsolete translation: {text!r}")


def _text(message):
    return message.id if isinstance(message.id, str) else message.id[0]


def check():
    """Report entries that will NOT be translated in the compiled file."""
    problems = False
    for lang, po_path in _po_files():
        with open(po_path, "rb") as f:
            catalog = read_po(f, locale=lang)
        for message in catalog:
            if not message.id:  # header
                continue
            text = _text(message)
            if message.fuzzy:
                problems = True
                print(f"[{lang}] FUZZY (skipped by compile): {text!r}")
            elif not message.string or (
                isinstance(message.string, (tuple, list))
                and not any(message.string)
            ):
                problems = True
                print(f"[{lang}] UNTRANSLATED: {text!r}")
        # Obsolete (#~) entries are strings that extract did NOT find in the
        # source code. They are never compiled, so they look "hidden".
        new_ids = [_text(m) for m in catalog if m.id and not m.string]
        for message in catalog.obsolete.values():
            problems = True
            text = _text(message)
            print(f"[{lang}] OBSOLETE (not found in source): {text!r}")
            print(f"        old translation: {message.string!r}")
            for close in difflib.get_close_matches(text, new_ids, n=2, cutoff=0.6):
                print(f"        similar new string: {close!r}")
    if problems:
        print(
            "\nFix the entries above in Poedit (review fuzzy ones and clear the "
            "fuzzy flag) and run this script again."
        )


def _unescape(value):
    if isinstance(value, str):
        return value.replace(FF_ESCAPE, FORM_FEED)
    return tuple(v.replace(FF_ESCAPE, FORM_FEED) for v in value)


def compile():
    """Compile every .po to .mo (fuzzy and obsolete entries are not compiled)."""
    n_errors = 0
    for lang, po_path in _po_files():
        with open(po_path, "rb") as f:
            catalog = read_po(f, locale=lang)
        for message in catalog:
            if message.id:
                message.id = _unescape(message.id)
                message.string = _unescape(message.string)
        messages = [m for m in catalog if m.id]
        translated = sum(1 for m in messages if m.string and not m.fuzzy)
        print(f"[{lang}] {translated} of {len(messages)} messages translated")
        for message, errors in catalog.check():
            for error in errors:
                n_errors += 1
                print(f"[{lang}] error: line {message.lineno}: {error}")
        mo_path = os.path.splitext(po_path)[0] + ".mo"
        print(f"[{lang}] compiling to {mo_path}")
        with open(mo_path, "wb") as f:
            write_mo(f, catalog, use_fuzzy=False)
        print(f"[{lang}] written: {os.path.getsize(mo_path)} bytes")
    if n_errors:
        sys.exit(f"Finished with {n_errors} error(s). Read the lines above.")


def main():
    extract()
    update()
    check()
    compile()
    print("Done.")


if __name__ == "__main__":
    main()
