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


def convert_audio(input_path: str, target_format: str, sampling_rate: str = "default", mp3_bitrate: str = "320k") -> str:
    """
    Converts input audio file to target_format (e.g. 'mp3', 'wav', 'flac', 'ogg', 'opus', 'aac').
    Optionally resamples audio to specified sampling_rate (e.g. '48000', '44100') and applies mp3_bitrate.
    If target_format is 'default' and sampling_rate is 'default', returns input_path untouched.
    On successful conversion, removes the original input file and returns the new file path.
    """
    import logging
    import subprocess

    format_needed = bool(target_format and target_format.lower() not in ("default", ""))
    rate_needed = bool(sampling_rate and str(sampling_rate).isdigit() and int(sampling_rate) > 0)

    if not format_needed and not rate_needed:
        return input_path

    target_ext = target_format.lower().lstrip(".") if format_needed else os.path.splitext(input_path)[1].lower().lstrip(".")
    current_ext = os.path.splitext(input_path)[1].lower().lstrip(".")
    if current_ext == target_ext and not rate_needed:
        return input_path

    app_vars.ensure_ffmpeg_in_path()
    ffmpeg_bin = app_vars.get_ffmpeg_path()

    base_path = os.path.splitext(input_path)[0]
    output_path = f"{base_path}.{target_ext}"

    # Ensure output filename does not collide with input
    if output_path == input_path:
        output_path = f"{base_path}_converted.{target_ext}"

    cmd = [
        ffmpeg_bin,
        "-y",
        "-i", input_path,
        "-vn",
    ]

    # Optimized codec flags for popular formats
    if target_ext == "mp3":
        cmd.extend(["-c:a", "libmp3lame"])
        bitrate_val = mp3_bitrate if mp3_bitrate else "320k"
        if not bitrate_val.endswith("k"):
            bitrate_val = f"{bitrate_val}k"
        cmd.extend(["-b:a", bitrate_val])
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

    if rate_needed:
        cmd.extend(["-ar", str(sampling_rate)])

    cmd.append(output_path)

    logging.info(f"[AudioConverter] Converting '{input_path}' to '{output_path}' using '{ffmpeg_bin}'")
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
    except subprocess.CalledProcessError as e:
        err_msg = e.stderr.decode(errors="replace") if e.stderr else str(e)
        logging.error(f"[AudioConverter] FFmpeg process failed (exit code {e.returncode}): {err_msg}")
        return input_path
    except Exception as e:
        logging.error(f"[AudioConverter] Failed to convert audio: {e}")
        return input_path


def format_file_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"

