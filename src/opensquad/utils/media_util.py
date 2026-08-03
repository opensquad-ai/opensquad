import logging
import os

# P2-8: import pydub/imageio lazily so the media plugin can be loaded without
# paying the ffmpeg-discovery cost (and its RuntimeWarning) at agent boot.
# Set on first use by _ensure_media().
AudioSegment = None

logger = logging.getLogger(__name__)


_media_ready = False


def _ensure_media():
    """Lazily import and configure pydub (import + ffmpeg probe ≈ 200-400ms)."""
    global AudioSegment, _media_ready
    if _media_ready:
        return
    import warnings

    import imageio_ffmpeg as ffmpeg_lib

    # pydub warns at import when ffmpeg is not on PATH; we set the converter
    # from imageio's bundled binary right after, so silence the warning.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        from pydub import AudioSegment

    try:
        AudioSegment.converter = ffmpeg_lib.get_ffmpeg_exe()
    except Exception:
        pass
    _media_ready = True


def convert_audio(input_path: str, output_format: str = "wav") -> str:
    """
    Convert audio format.

    Args:
        input_path: Input file path (e.g. .webm)
        output_format: Target format (e.g. wav, mp3)

    Returns:
        Path to the converted file
    """
    _ensure_media()
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"Input file not found: {input_path}")

    # Extract base name and replace extension
    base_name = os.path.splitext(input_path)[0]
    output_path = f"{base_name}.{output_format}"

    logger.info(f"[Media] Converting {input_path} -> {output_path}...")

    try:
        # Load audio (webm typically contains opus encoding)
        audio = AudioSegment.from_file(input_path)

        # Export to target format
        audio.export(output_path, format=output_format)

        logger.info("[Media] Conversion successful.")
        return output_path
    except Exception as e:
        logger.error(f"[Media] Conversion failed: {e}")
        raise e
