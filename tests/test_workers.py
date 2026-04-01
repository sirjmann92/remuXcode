#!/usr/bin/env python3
"""Test scripts for validating backend workers.

Run with: python tests/test_workers.py <test_file.mkv>

Tests each worker independently:
- FFProbe analysis
- Anime detection
- Language detection
- Audio conversion check
- Video conversion check
- Stream cleanup check
"""

from pathlib import Path
import sys

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.utils.anime_detect import AnimeDetector, ContentType
from backend.utils.config import AudioConfig, CleanupConfig, VideoConfig
from backend.utils.ffprobe import FFProbe
from backend.utils.language import LanguageDetector
from backend.workers.audio import AudioConverter
from backend.workers.cleanup import StreamCleanup
from backend.workers.video import VideoConverter


def print_header(title: str):
    """Print a formatted header."""
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")


def print_result(label: str, value, indent: int = 2):
    """Print a formatted result."""
    prefix = " " * indent
    print(f"{prefix}{label}: {value}")


def test_ffprobe(file_path: str) -> bool:
    """Test FFProbe analysis."""
    print_header("FFProbe Analysis")

    probe = FFProbe()
    info = probe.get_file_info(file_path)

    if info is None:
        print("  ERROR: Failed to analyze file")
        return False

    print_result("File", info.path.name)
    print_result("Format", info.format_name)
    print_result("Duration", f"{info.duration:.1f}s ({info.duration / 60:.1f}m)")
    print_result("Size", f"{info.size / 1024 / 1024:.1f} MB")

    # Video streams
    print(f"\n  Video Streams ({len(info.video_streams)}):")
    for vs in info.video_streams:
        print(f"    [{vs.index}] {vs.codec_name} {vs.width}x{vs.height} {vs.bit_depth}-bit")
        print(f"         H.264: {vs.is_h264}, 10-bit H.264: {vs.is_10bit_h264}, HEVC: {vs.is_hevc}")

    # Audio streams
    print(f"\n  Audio Streams ({len(info.audio_streams)}):")
    for audio in info.audio_streams:
        lang = audio.language or "und"
        title = audio.title or ""
        bitrate = f"{audio.bitrate // 1000}k" if audio.bitrate else "?"
        print(
            f"    [{audio.index}] {audio.codec_name} {audio.channels}ch {bitrate} ({lang}) {title}"
        )
        print(
            f"         DTS: {audio.is_dts}, DTS:X: {audio.is_dts_x}, TrueHD: {audio.is_truehd}, Lossless: {audio.is_lossless}"
        )

    # Subtitle streams
    print(f"\n  Subtitle Streams ({len(info.subtitle_streams)}):")
    for sub in info.subtitle_streams:
        lang = sub.language or "und"
        flags = []
        if sub.is_forced:
            flags.append("forced")
        if sub.is_sdh:
            flags.append("SDH")
        if sub.is_default:
            flags.append("default")
        flags_str = f" [{', '.join(flags)}]" if flags else ""
        print(f"    [{sub.index}] {sub.codec_name} ({lang}) {sub.title or ''}{flags_str}")

    # Summary
    print("\n  Summary:")
    print_result("Has DTS", info.has_dts)
    print_result("Has TrueHD", info.has_truehd)
    print_result("Is HEVC", info.is_hevc)

    return True


def test_anime_detection(file_path: str) -> ContentType:
    """Test anime detection."""
    print_header("Anime Detection")

    detector = AnimeDetector()
    content_type = detector.detect(file_path, use_api=False)  # Skip API for quick test

    print_result("File", Path(file_path).name)
    print_result("Content Type", content_type.value)
    print_result("Is Anime", content_type == ContentType.ANIME)

    # Show what influenced the decision
    path_str = str(file_path).lower()
    anime_indicators = []
    if "/anime/" in path_str or "/アニメ/" in path_str:
        anime_indicators.append("Path contains /Anime/")
    if any(ext in Path(file_path).suffix.lower() for ext in [".mkv"]):
        # Check for common anime release groups in filename
        name = Path(file_path).stem.lower()
        groups = ["subsplease", "erai-raws", "horriblesubs", "commie", "coalgirls"]
        anime_indicators.extend(f"Release group: {group}" for group in groups if group in name)

    if anime_indicators:
        print("\n  Detection Hints:")
        for hint in anime_indicators:
            print(f"    - {hint}")

    return content_type


def test_language_detection(file_path: str) -> str:
    """Test language detection."""
    print_header("Language Detection")

    detector = LanguageDetector()
    language = detector.detect_original_language(file_path)

    print_result("File", Path(file_path).name)
    print_result("Detected Language", language)

    # Language name mapping
    lang_names = {
        "eng": "English",
        "jpn": "Japanese",
        "spa": "Spanish",
        "fre": "French",
        "ger": "German",
        "kor": "Korean",
        "chi": "Chinese",
        "rus": "Russian",
        "ita": "Italian",
    }
    if language in lang_names:
        print_result("Language Name", lang_names[language])

    return language


def test_audio_worker(file_path: str):
    """Test audio conversion worker (analysis only, no actual conversion)."""
    print_header("Audio Converter Analysis")

    config = AudioConfig(
        enabled=True,
        convert_dts=True,
        convert_truehd=False,
        keep_original=False,
        prefer_ac3=True,
    )

    converter = AudioConverter(config)

    should_convert = converter.should_convert(file_path)

    print_result("File", Path(file_path).name)
    print_result("Should Convert", should_convert)

    if should_convert:
        print("\n  Would convert DTS → AC3/AAC")
        print(f"  Config: prefer_ac3={config.prefer_ac3}, keep_original={config.keep_original}")


def test_video_worker(file_path: str, content_type: ContentType):
    """Test video conversion worker (analysis only, no actual conversion)."""
    print_header("Video Converter Analysis")

    config = VideoConfig(
        enabled=True,
        convert_10bit_x264=True,
        convert_8bit_x264=False,
        anime_auto_detect=True,
        anime_crf=20,
        anime_preset="slow",
        anime_tune="animation",
        live_action_crf=22,
        live_action_preset="medium",
        live_action_tune=None,
    )

    converter = VideoConverter(config)

    should_convert = converter.should_convert(file_path)

    print_result("File", Path(file_path).name)
    print_result("Should Convert", should_convert)

    if should_convert:
        if content_type == ContentType.ANIME:
            print("\n  Would encode with anime settings:")
            print(
                f"    CRF: {config.anime_crf}, Preset: {config.anime_preset}, Tune: {config.anime_tune}"
            )
        else:
            print("\n  Would encode with live action settings:")
            print(
                f"    CRF: {config.live_action_crf}, Preset: {config.live_action_preset}, Tune: {config.live_action_tune or 'none'}"
            )


def test_cleanup_worker(file_path: str, original_language: str):
    """Test stream cleanup worker (analysis only, no actual cleanup)."""
    print_header("Stream Cleanup Analysis")

    config = CleanupConfig(
        enabled=True,
        clean_audio=True,
        clean_subtitles=True,
        keep_languages=["eng"],
        keep_undefined=False,
        keep_commentary=True,
        keep_audio_description=True,
        keep_sdh=True,
    )

    cleanup = StreamCleanup(config)

    should_cleanup = cleanup.should_cleanup(file_path)

    print_result("File", Path(file_path).name)
    print_result("Should Cleanup", should_cleanup)


def main():
    """Main test runner."""
    if len(sys.argv) < 2:
        print("Usage: python test_workers.py <media_file>")
        print("\nExample:")
        print("  python test_workers.py /path/to/movie.mkv")
        print("\nThis will analyze the file without making any changes.")
        sys.exit(1)

    file_path = sys.argv[1]

    if not Path(file_path).exists():
        print(f"ERROR: File not found: {file_path}")
        sys.exit(1)

    print(f"\n{'#' * 60}")
    print("  remuXcode Worker Tests")
    print(f"  File: {Path(file_path).name}")
    print(f"{'#' * 60}")

    # Run all tests
    if not test_ffprobe(file_path):
        print("\nFFProbe failed - cannot continue with other tests")
        sys.exit(1)

    content_type = test_anime_detection(file_path)
    original_language = test_language_detection(file_path)
    test_audio_worker(file_path)
    test_video_worker(file_path, content_type)
    test_cleanup_worker(file_path, original_language)

    print_header("Summary")
    print("  All tests completed successfully!")
    print("  No files were modified - this was analysis only.")
    print("\n  To actually convert files, use the unified service.")


if __name__ == "__main__":
    main()
