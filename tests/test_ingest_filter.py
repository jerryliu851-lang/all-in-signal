from __future__ import annotations

from allin.ingest.youtube import PlaylistEntry, VideoMeta, classify_episode, meta_from_info


def E(title="Some title", dur=5580, in_pl=True, live=None):
    return PlaylistEntry(video_id="abc", title=title, duration_seconds=dur, in_full_episode_playlist=in_pl, live_status=live)


def test_full_playlist_member(settings):
    assert classify_episode(E(dur=93 * 60), None, settings)[0] is True


def test_interview_excluded(settings):
    ok, why = classify_episode(E(dur=2400, in_pl=False), None, settings)
    assert not ok and why == "duration_2400s_below_2700s_floor"
    ok, why = classify_episode(E(dur=3000, in_pl=False), None, settings)
    assert not ok and why == "not_in_full_episode_playlist"


def test_legacy_title(settings):
    assert classify_episode(E(title="E123: Foo", dur=3000, in_pl=False), None, settings)[0] is True


def test_short_and_unknown(settings):
    assert classify_episode(E(dur=1200), None, settings)[0] is False
    assert classify_episode(E(dur=None), None, settings) == (False, "duration_unknown")


def test_live(settings):
    assert classify_episode(E(live="is_upcoming"), None, settings) == (False, "live_or_upcoming")


def test_meta_overrides_duration(settings):
    meta = VideoMeta(video_id="abc", title="t", duration_seconds=5000)
    assert classify_episode(E(dur=None), meta, settings)[0] is True


def test_fixture_meta(load_fixture, settings):
    m = meta_from_info(load_fixture("youtube/video_2v8b062QzTI_meta.json"), "2v8b062QzTI")
    assert m.published_at == "2026-11-07" and len(m.chapters) == 5
    assert classify_episode(E(dur=None), m, settings)[0]


def test_playlist_fixture(load_fixture, settings):
    from allin.ingest.youtube import _entries_from_info

    entries = _entries_from_info(load_fixture("youtube/playlist_allin_flat.json"), True)
    assert entries and all(e.in_full_episode_playlist for e in entries)
    assert sum(classify_episode(e, None, settings)[0] for e in entries) >= 10
