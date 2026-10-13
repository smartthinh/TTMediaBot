import os

from bot import app_vars


def clean_file_name(file_name: str) -> str:
    for char in ["\\", "/", "%", "*", "?", ":", '"', "|"] + [
        chr(i) for i in range(1, 32)
    ]:
        file_name = file_name.replace(char, "_")
    file_name = file_name.strip()
    return file_name


def get_abs_path(file_name: str) -> str:
    if os.access(app_vars.directory, os.W_OK):
        return os.path.join(app_vars.directory, file_name)
    return os.path.join(app_vars.data_dir, file_name)


def convert_audio(input_path: str, target_format: str) -> str:
    """
    Converts input audio file to target_format (e.g. 'mp3', 'wav', 'flac', 'ogg', 'opus', 'aac').
    If target_format is 'default' or matches input extension, returns input_path untouched.
    On successful conversion, removes the original input file and returns the new file path.
    """
    import logging
    import subprocess

    if not target_format or target_format.lower() in ("default", ""):
        return input_path

    target_ext = target_format.lower().lstrip(".")
    current_ext = os.path.splitext(input_path)[1].lower().lstrip(".")
    if current_ext == target_ext:
        return input_path

    app_vars.ensure_ffmpeg_in_path()

    base_path = os.path.splitext(input_path)[0]
    output_path = f"{base_path}.{target_ext}"

    # Ensure output filename does not collide with input
    if output_path == input_path:
        output_path = f"{base_path}_converted.{target_ext}"

    cmd = [
        "ffmpeg",
        "-y",
        "-i", input_path,
        "-vn",
    ]

    # Optimized codec flags for popular formats
    if target_ext == "mp3":
        cmd.extend(["-c:a", "libmp3lame", "-q:a", "2"])
    elif target_ext == "ogg":
        cmd.extend(["-c:a", "libvorbis", "-q:a", "4"])
    elif target_ext == "opus":
        cmd.extend(["-c:a", "libopus", "-b:a", "128k"])
    elif target_ext == "flac":
        cmd.extend(["-c:a", "flac"])
    elif target_ext == "wav":
        cmd.extend(["-c:a", "pcm_s16le"])
    elif target_ext == "aac":
        cmd.extend(["-c:a", "aac", "-b:a", "192k"])
    elif target_ext == "wma":
        cmd.extend(["-c:a", "wmav2", "-b:a", "192k"])

    cmd.append(output_path)

    logging.info(f"[AudioConverter] Converting '{input_path}' to '{output_path}'")
    try:
        startupinfo = None
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        res = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            startupinfo=startupinfo,
            check=True,
        )
        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            logging.info(f"[AudioConverter] Successfully converted to '{output_path}'. Removing original file.")
            try:
                os.remove(input_path)
            except Exception as e:
                logging.warning(f"[AudioConverter] Could not remove original file '{input_path}': {e}")
            return output_path
        else:
            logging.error(f"[AudioConverter] Output file '{output_path}' is missing or empty.")
            return input_path
    except Exception as e:
        logging.error(f"[AudioConverter] Failed to convert audio: {e}")
        return input_path

