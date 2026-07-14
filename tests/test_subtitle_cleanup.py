"""Regression tests for subtitle cleanup flagging vs. worker behavior.

Covers the tag-aware keep-all safety net: files whose only subtitles are
explicitly tagged with non-kept languages must be flagged AND actually have
those subtitles removed, while files whose removal candidates include
untagged streams must neither be flagged nor stripped (the worker would
keep everything, so flagging them created a permanent no-op Cleanup badge).
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.utils.config import CleanupConfig
from backend.utils.ffprobe import MediaInfo, SubtitleStream
from backend.workers.cleanup import StreamCleanup


def _sub(index: int, language: str | None, *, forced: bool = False) -> SubtitleStream:
    return SubtitleStream(
        index=index,
        codec_name="subrip",
        language=language,
        title=None,
        is_default=False,
        is_forced=forced,
        is_hearing_impaired=False,
    )


def _media_info(subs: list[SubtitleStream]) -> MediaInfo:
    return MediaInfo(
        path=Path("/library/Movie (2014)/Movie (2014).mkv"),
        format_name="matroska",
        duration=5400.0,
        size=10**9,
        bitrate=8_000_000,
        video_streams=[],
        audio_streams=[],
        subtitle_streams=subs,
        attachment_streams=[],
        chapters=[],
    )


class _StubFFProbe:
    def __init__(self, info: MediaInfo):
        self._info = info

    def get_file_info(self, _path: str) -> MediaInfo:
        return self._info


class _StubLanguageDetector:
    def detect_original_language(self, _path: str) -> str:
        return "fre"


def _worker(subs: list[SubtitleStream]) -> StreamCleanup:
    config = CleanupConfig(keep_languages=["eng"], keep_undefined=False)
    return StreamCleanup(
        config,
        ffprobe=_StubFFProbe(_media_info(subs)),
        language_detector=_StubLanguageDetector(),
    )


def test_safety_net_lets_tagged_foreign_subs_be_removed():
    """All removal candidates explicitly tagged → removal proceeds (net off)."""
    remove = [_sub(2, "fre")]
    assert StreamCleanup._subtitle_safety_net_applies([], remove) is False


def test_safety_net_protects_untagged_candidates():
    """An untagged candidate in an all-removed set → keep everything."""
    for lang in (None, "", "und"):
        remove = [_sub(2, lang, forced=True)]
        assert StreamCleanup._subtitle_safety_net_applies([], remove) is True


def test_safety_net_irrelevant_when_some_subs_survive():
    """A partial removal (some subs survive) never engages the safety net."""
    keep = [_sub(2, "eng")]
    remove = [_sub(3, "fre")]
    assert StreamCleanup._subtitle_safety_net_applies(keep, remove) is False


def test_should_cleanup_flags_single_tagged_foreign_sub():
    """The Jack and the Cuckoo-Clock Heart case: one FRE-tagged sub, keep=eng.

    The worker will remove it, so the file must be flagged.
    """
    worker = _worker([_sub(2, "fre")])
    assert worker.should_cleanup("/library/Movie (2014)/Movie (2014).mkv") is True


def test_should_cleanup_does_not_flag_protected_forced_untagged_sub():
    """Forced untagged sub with keep_undefined off: worker keeps everything,
    so flagging it would create a permanent no-op Cleanup badge.
    """
    worker = _worker([_sub(2, None, forced=True)])
    assert worker.should_cleanup("/library/Movie (2014)/Movie (2014).mkv") is False


def test_should_cleanup_ignores_plain_untagged_sub():
    """Non-forced untagged subs are never removal candidates at all."""
    worker = _worker([_sub(2, "und")])
    assert worker.should_cleanup("/library/Movie (2014)/Movie (2014).mkv") is False


def test_should_cleanup_flags_partial_removal():
    """Mixed kept/removed languages still flag the file normally."""
    worker = _worker([_sub(2, "eng"), _sub(3, "fre")])
    assert worker.should_cleanup("/library/Movie (2014)/Movie (2014).mkv") is True


def test_should_cleanup_clean_file_not_flagged():
    """A file whose only subtitle is in a kept language is not flagged."""
    worker = _worker([_sub(2, "eng")])
    assert worker.should_cleanup("/library/Movie (2014)/Movie (2014).mkv") is False
