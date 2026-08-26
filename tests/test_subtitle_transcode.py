"""Regression tests for incompatible/unreadable subtitle codec handling.

Two distinct problems, both discovered from real streaming-service WEBDL
rips crashing every worker with "Nothing was written into output file":

1. Some subtitle codecs (mov_text from MP4 sources, eia_608 closed
   captions) make the Matroska muxer refuse to write the output header at
   all ("Subtitle codec N is not supported"), killing every stream in the
   file. FFmpeg can decode these fine, so the fix is to re-encode just that
   stream to SubRip when (and only when) the actual ffmpeg output target
   is Matroska; MP4 targets must leave mov_text alone since it's the
   native, valid codec there.

2. Some subtitle streams have no identifiable codec at all (ffprobe
   reports an empty codec_name, or the literal string "unknown" after an
   mkvmerge remux) — seen with WebVTT tracks from some streaming-service
   WEBDL rips whose muxing tool didn't set the Matroska BlockAddition/
   CodecPrivate fields FFmpeg expects. There is no decode path to
   transcode from here, so the only safe handling is to drop the stream
   entirely, in every worker, regardless of target container.

   Dropping a stream shifts every later stream's *output* subtitle
   position down by one (verified empirically against real ffmpeg) — the
   subtle part of this fix, and the one most likely to silently regress,
   is that every per-stream ``-c:s:N`` / ``-metadata:s:s:N`` option for a
   later stream must use its position among the streams that actually
   survive into the output, not its position in the original stream list.
"""

from dataclasses import replace
from pathlib import Path
import sys
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.utils.config import AudioConfig, CleanupConfig
from backend.utils.ffprobe import (
    AudioStream,
    FFProbe,
    MediaInfo,
    SubtitleStream,
    subtitle_is_unreadable,
    subtitle_needs_transcode,
)
from backend.workers.audio import AudioConverter
from backend.workers.cleanup import StreamCleanup
from backend.workers.video import VideoConverter


def test_subtitle_needs_transcode_flags_known_incompatible_codecs():
    """mov_text (MP4) and eia_608 (closed captions) break the Matroska muxer."""
    assert subtitle_needs_transcode("mov_text") is True
    assert subtitle_needs_transcode("eia_608") is True


def test_subtitle_needs_transcode_leaves_compatible_codecs_alone():
    """Codecs the Matroska muxer already accepts must not be flagged."""
    for codec in ("subrip", "ass", "ssa", "hdmv_pgs_subtitle", "dvd_subtitle"):
        assert subtitle_needs_transcode(codec) is False


def _sub(index: int, codec_name: str, language: str = "eng") -> SubtitleStream:
    return SubtitleStream(
        index=index,
        codec_name=codec_name,
        language=language,
        title=None,
        is_default=False,
        is_forced=False,
        is_hearing_impaired=False,
    )


def _media_info(subs: list[SubtitleStream]) -> MediaInfo:
    return MediaInfo(
        path=Path("/library/Movie (2014)/Movie (2014).mp4"),
        format_name="mov,mp4,m4a,3gp,3g2,mj2",
        duration=1200.0,
        size=10**8,
        bitrate=2_000_000,
        video_streams=[],
        audio_streams=[],
        subtitle_streams=subs,
        attachment_streams=[],
        chapters=[],
    )


def test_cleanup_overrides_movtext_when_output_target_is_mkv():
    """A .mkv output target (e.g. chained after Video, which always writes
    .mkv) must transcode mov_text to srt — copying it would abort the mux.
    """
    worker = StreamCleanup(CleanupConfig())
    subs = [_sub(2, "mov_text")]
    info = _media_info(subs)
    cmd = worker._build_ffmpeg_command(
        "/tmp/in.mkv", "/tmp/.remuxcode-temp-x/in.mkv", info, [], subs
    )
    assert "-c:s:0" in cmd
    assert cmd[cmd.index("-c:s:0") + 1] == "srt"


def test_cleanup_leaves_movtext_alone_when_output_target_is_mp4():
    """A standalone Cleanup job on an untouched .mp4 source writes an .mp4
    temp file (mirrors the input's own extension) — mov_text is the native,
    correct codec there and must be left as a plain copy.
    """
    worker = StreamCleanup(CleanupConfig())
    subs = [_sub(2, "mov_text")]
    info = _media_info(subs)
    cmd = worker._build_ffmpeg_command(
        "/tmp/in.mp4", "/tmp/.remuxcode-temp-x/in.mp4", info, [], subs
    )
    assert "-c:s:0" not in cmd


def test_cleanup_does_not_touch_already_compatible_subtitle_codec():
    """A codec already valid in Matroska (subrip) is never overridden."""
    worker = StreamCleanup(CleanupConfig())
    subs = [_sub(2, "subrip")]
    info = _media_info(subs)
    cmd = worker._build_ffmpeg_command(
        "/tmp/in.mkv", "/tmp/.remuxcode-temp-x/in.mkv", info, [], subs
    )
    assert "-c:s:0" not in cmd


# --- subtitle_is_unreadable: streams FFmpeg can't identify at all -----------


def test_subtitle_is_unreadable_flags_blank_and_unknown_codec():
    """No codec_name field at all (real production ffprobe output) and the
    literal string "unknown" (same stream after an mkvmerge remux) both mean
    the same thing: FFmpeg has no usable codec info for this stream.
    """
    assert subtitle_is_unreadable("") is True
    assert subtitle_is_unreadable("unknown") is True
    assert subtitle_is_unreadable("UNKNOWN") is True


def test_subtitle_is_unreadable_leaves_real_codecs_alone():
    """Any codec FFmpeg can actually name is never treated as unreadable,
    even webvtt itself when muxed compliantly enough for ffprobe to ID it.
    """
    for codec in ("subrip", "mov_text", "hdmv_pgs_subtitle", "webvtt"):
        assert subtitle_is_unreadable(codec) is False


def test_cleanup_always_drops_unreadable_subtitle_even_with_clean_subtitles_off(tmp_path):
    """Unreadable streams are a technical necessity, not a clean_subtitles
    content preference — dropping one must not depend on that setting.

    This exercises cleanup()'s own call-site selection (readable_subs vs.
    subtitle_keep when clean_subtitles is off), not just the command
    builder in isolation — a real ffmpeg run against a tiny fixture, with
    ffprobe's result stubbed to also report a second, unreadable subtitle
    stream that was never really muxed into the fixture (harmless: it's
    dropped before a -map for it is ever emitted).
    """
    fixture = Path(__file__).parent / "fixtures" / "tiny_with_subtitle.mkv"
    real_info = FFProbe().get_file_info(str(fixture))
    assert real_info is not None
    fake_unreadable = _sub(99, "", language="spa")
    stubbed_info = replace(
        real_info, subtitle_streams=[*real_info.subtitle_streams, fake_unreadable]
    )

    stub_probe = MagicMock(spec=FFProbe)
    stub_probe.get_file_info.return_value = stubbed_info

    worker = StreamCleanup(CleanupConfig(clean_subtitles=False), ffprobe=stub_probe)
    out = tmp_path / "out.mkv"
    result = worker.cleanup(str(fixture), output_file=str(out), job_id="t1")

    assert result.success, result.error
    assert result.subtitle_kept == 1  # the fixture's one real, readable subtitle
    assert result.subtitle_removed == 1  # the fake unreadable one, dropped unconditionally


def test_video_patch_subtitle_codecs_drop_and_transcode_index_alignment():
    """The exact bug found while building this fix: a dropped stream closes
    the gap in FFmpeg's output numbering rather than leaving it, so a later
    transcoded stream's -c:s:N must count only surviving streams. A naive
    implementation using the raw enumerate position produces a command
    that targets a nonexistent (or wrong) output stream and still crashes.
    """
    subs = [
        _sub(2, "subrip"),  # kept as-is -> output position 0
        _sub(3, ""),  # dropped entirely -> consumes no output slot
        _sub(4, "mov_text"),  # transcoded -> output position 1, NOT 2
    ]
    cmd = ["ffmpeg", "-i", "in.mkv", "-map", "0:s?", "-c:s", "copy", "out.mkv"]
    patched = VideoConverter._patch_subtitle_codecs(cmd, subs)

    assert "-map" in patched and "-0:3" in patched  # stream 3 excluded by absolute index
    assert "-c:s:1" in patched
    assert patched[patched.index("-c:s:1") + 1] == "srt"
    assert "-c:s:2" not in patched  # would target a stream that doesn't exist
    assert "-c:s:0" not in patched  # subrip at position 0 needs no override


def _audio_stream(index: int, codec_name: str) -> AudioStream:
    return AudioStream(
        index=index,
        codec_name=codec_name,
        codec_long_name="",
        profile=None,
        channels=6,
        channel_layout=None,
        sample_rate=48000,
        bitrate=None,
        language="eng",
        title=None,
        is_default=True,
        is_forced=False,
        is_commentary=False,
    )


def test_audio_build_command_drop_and_transcode_index_alignment():
    """Same ordering guarantee as the video worker, verified against
    AudioConverter's own (already explicit, per-stream) mapping loop.
    """
    audio = _audio_stream(1, "dts")
    subs = [
        _sub(2, "subrip"),  # kept as-is -> output position 0
        _sub(3, ""),  # dropped entirely -> consumes no output slot
        _sub(4, "mov_text"),  # transcoded -> output position 1, NOT 2
    ]
    info = MediaInfo(
        path=Path("/x/y.mkv"),
        format_name="matroska",
        duration=100.0,
        size=10**9,
        bitrate=1000,
        video_streams=[],
        audio_streams=[audio],
        subtitle_streams=subs,
        attachment_streams=[],
        chapters=[],
    )
    worker = AudioConverter(AudioConfig())
    cmd = worker._build_ffmpeg_command("/x/in.mkv", "/x/out.mkv", info, [audio], [])

    assert "0:2" in cmd  # subrip kept
    assert "0:3" not in cmd  # unreadable stream never mapped
    assert "0:4" in cmd  # mov_text kept (for transcode)
    assert "-c:s:1" in cmd
    assert cmd[cmd.index("-c:s:1") + 1] == "srt"
    assert "-c:s:2" not in cmd  # would target a stream that doesn't exist
    assert "-c:s:0" not in cmd  # subrip at position 0 needs no override
