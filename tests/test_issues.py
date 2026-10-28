"""Regression tests for GitHub issues #1, #2, #3, #6, #9 and 3D (#15).

Run: python3 -m unittest discover -s tests -t .
"""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import cli  # noqa: E402
from episode_matcher import (group_episodes, normalize_title,  # noqa: E402
                             parse_absolute_episode, parse_episode)
from languages import primary_language_status  # noqa: E402
from models import AudioTrack, IntegrityResult, VideoFile  # noqa: E402
from probe import (detect_3d, effective_fps, normalize_level,  # noqa: E402
                   probe_file)
from procutil import decode_text, run_tool  # noqa: E402
from scoring import score_language_tracks  # noqa: E402

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def make_vf(name="a.mkv", parent="/x", duration=1300.0, langs=("rus",), score=10.0,
            verified=True):
    vf = VideoFile(path=Path(parent) / name, duration=duration, score=score)
    if verified:  # fully decoded without errors (deletion advice requires it)
        vf.integrity = IntegrityResult(checked=True, full_checked=True)
    vf.audio_tracks = [AudioTrack(codec="ac3", channels=2, language=l) for l in langs]
    return vf


class LevelTests(unittest.TestCase):  # issue #6
    def test_hevc(self):
        for raw, want in ((120, "4.0"), (123, "4.1"), (150, "5.0"),
                          (153, "5.1"), (156, "5.2")):
            self.assertEqual(normalize_level("hevc", raw), want)

    def test_avc_1b(self):
        self.assertEqual(normalize_level("h264", 9), "1.0")

    def test_avc(self):
        for raw, want in ((30, "3.0"), (41, "4.1"), (51, "5.1"), (52, "5.2")):
            self.assertEqual(normalize_level("h264", raw), want)

    def test_unknown(self):
        self.assertEqual(normalize_level("hevc", -99), "")
        self.assertEqual(normalize_level("h264", 0), "")
        self.assertEqual(normalize_level("hevc", "garbage"), "")
        self.assertEqual(normalize_level("hevc", None), "")


class FpsTests(unittest.TestCase):  # issue #9
    def test_doubled_nominal_uses_average(self):
        s = {"r_frame_rate": "60000/1001", "avg_frame_rate": "30000/1001"}
        self.assertEqual(effective_fps(s), 29.97)

    def test_frame_count_beats_nominal(self):
        s = {"r_frame_rate": "60000/1001", "avg_frame_rate": "0/0",
             "nb_frames": "166872", "duration": "5568.0"}
        self.assertEqual(effective_fps(s), 29.97)

    def test_integer_rates_not_snapped_to_ntsc(self):
        for rate in ("24/1", "30/1", "60/1", "120/1", "25/1", "50/1"):
            self.assertEqual(effective_fps({"r_frame_rate": rate,
                                            "avg_frame_rate": rate}), float(rate[:-2]))

    def test_padded_container_duration_ignored(self):
        s = {"r_frame_rate": "24000/1001", "avg_frame_rate": "24000/1001",
             "nb_frames": "100000"}
        self.assertEqual(effective_fps(s, container_duration=5000.0), 23.976)

    def test_cfr_rates(self):
        for rate, want in (("24000/1001", 23.976), ("25/1", 25.0),
                           ("30000/1001", 29.97), ("50/1", 50.0),
                           ("60000/1001", 59.94)):
            self.assertEqual(effective_fps({"r_frame_rate": rate,
                                            "avg_frame_rate": rate}), want)

    def test_vfr_keeps_measured(self):
        s = {"r_frame_rate": "1000/1", "avg_frame_rate": "2150/100"}
        self.assertEqual(effective_fps(s), 21.5)

    def test_missing_everything(self):
        self.assertEqual(effective_fps({}), 0.0)
        self.assertEqual(effective_fps({"avg_frame_rate": "0/0"}), 0.0)

    def test_nominal_fallback(self):
        self.assertEqual(effective_fps({"r_frame_rate": "25/1",
                                        "avg_frame_rate": "0/0"}), 25.0)


class DecodeTests(unittest.TestCase):  # issue #1
    def test_utf8(self):
        self.assertEqual(decode_text("Привет".encode("utf-8")), ("Привет", ""))

    def test_cp1251(self):
        text, note = decode_text("Привет".encode("cp1251"))
        self.assertEqual(text, "Привет")
        self.assertIn("cp1251", note)

    def test_invalid_bytes_never_raise(self):
        text, note = decode_text(b"RMS level dB: -20.5 \xca\x98\x81")
        self.assertIn("RMS level dB: -20.5", text)
        self.assertTrue(note)

    def test_run_tool_survives_bad_bytes(self):
        cmd = [sys.executable, "-c",
               "import sys; sys.stderr.buffer.write(b'RMS level dB: -3.0 \\xca\\xff\\n')"]
        res = run_tool(cmd, timeout=20)
        self.assertIn("RMS level dB: -3.0", res.stderr)
        self.assertTrue(res.decode_note)

    @unittest.skipUnless(HAVE_FFMPEG, "ffmpeg required")
    def test_probe_file_with_cp1251_tag(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "old.avi"
            title = "Привет мир".encode("cp1251").decode("latin-1")
            subprocess.run(
                ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                 "testsrc=s=320x240:d=2:r=25", "-f", "lavfi", "-i",
                 "sine=d=2", "-c:v", "mpeg4", "-c:a", "mp3", str(out)],
                check=True)
            # Inject raw cp1251 bytes into the AVI header (INAM chunk)
            data = bytearray(out.read_bytes())
            data += b"\x00"  # keep file valid enough
            raw = "Привет".encode("cp1251")
            idx = data.find(b"Lavf")
            self.assertGreater(idx, 0)
            data[idx:idx + len(raw)] = raw
            out.write_bytes(bytes(data))
            vf = probe_file(out, use_cache=False)
            self.assertGreater(vf.duration, 1)
            self.assertEqual(vf.video.width, 320)
            # ffmpeg prints the raw tag bytes to stderr (the issue #1 crash)
            raw = subprocess.run(["ffmpeg", "-v", "info", "-i", str(out),
                                  "-f", "null", "-"], capture_output=True)
            with self.assertRaises(UnicodeDecodeError):
                raw.stderr.decode("utf-8")
            from probe import detect_real_channels
            detect_real_channels(out, 0, 2, vf.duration)  # must not raise
            res = run_tool(["ffmpeg", "-v", "info", "-i", str(out),
                            "-f", "null", "-"], timeout=60)
            self.assertTrue(res.decode_note)
            self.assertIn("Duration", res.stderr)


class LanguageTests(unittest.TestCase):  # issue #2
    def test_status(self):
        self.assertEqual(primary_language_status(make_vf(langs=("rus",)), "rus"), "present")
        self.assertEqual(primary_language_status(make_vf(langs=("ru",)), "rus"), "present")
        self.assertEqual(primary_language_status(make_vf(langs=("",)), "rus"), "unknown")
        self.assertEqual(primary_language_status(make_vf(langs=("und",)), "rus"), "unknown")
        self.assertEqual(primary_language_status(make_vf(langs=("eng",)), "rus"), "absent")
        self.assertEqual(primary_language_status(make_vf(langs=("eng", "")), "rus"), "unknown")
        self.assertEqual(primary_language_status(make_vf(langs=()), "rus"), "absent")
        self.assertEqual(primary_language_status(make_vf(), ""), "")

    def test_penalty_only_when_confirmed_absent(self):
        self.assertEqual(score_language_tracks(make_vf(langs=("",)), "rus"), 0.0)
        self.assertEqual(score_language_tracks(make_vf(langs=("eng",)), "rus"), -10.0)
        self.assertEqual(score_language_tracks(make_vf(langs=("rus",)), "rus"), 0.0)

    def test_unknown_blocks_deletion_advice(self):
        a = make_vf("a.mkv", "/a", langs=("",), score=20)
        b = make_vf("b.mkv", "/b", langs=("rus",), score=5)
        groups = {"t": {(1, 1): [a, b]}}
        keep, delete, review = cli.deletion_candidates(groups, "rus")
        self.assertEqual((keep, delete), ([], []))
        self.assertEqual(len(review), 1)


class EpisodeTests(unittest.TestCase):  # issue #3
    def test_formats(self):
        self.assertEqual(parse_episode("Интерны. Сезон №4. Серия №071.avi"), (4, 71))
        self.assertEqual(parse_absolute_episode("Интерны. Сезон №4. Серия №071.avi"), 71)
        self.assertEqual(parse_episode("Interny.s10e01.(181)XviD.[tahiy].avi"), (10, 1))
        self.assertEqual(parse_absolute_episode("Interny.s10e01.(181)XviD.[tahiy].avi"), 181)
        self.assertEqual(normalize_title("Интерны. Сезон №4. Серия №071.avi"), "интерны")

    def test_resolution_in_brackets_is_not_an_absolute_number(self):
        for n in (480, 576, 720, 1080):
            self.assertIsNone(parse_absolute_episode(f"Show.S01E05.({n}).mkv"))
        self.assertEqual(parse_absolute_episode("Show.S10E01.(240).mkv"), 240)

    def test_year_and_resolution_are_not_absolute(self):
        self.assertIsNone(parse_absolute_episode("Show.S01E02.(2019).mkv"))
        self.assertIsNone(parse_absolute_episode("Show S01E01 (1080).mkv"))
        self.assertEqual(parse_absolute_episode("Сезон 1 Серия 100.mkv"), 100)  # 3+ digits: series-wide
        self.assertIsNone(parse_absolute_episode("Сезон 1 Серия 12.mkv"))
        self.assertEqual(parse_episode("Сезон_4_Серия_12.mkv"), (4, 12))
        self.assertEqual(normalize_title("шоу сезон 4 серия 12.mkv"), "шоу")

    def test_random_parent_dir_not_an_episode(self):
        vf = make_vf("native.mkv", "/tmp/aa85969e-e5c3-58eb/scratch/v")
        groups = group_episodes([vf])
        self.assertEqual([k for e in groups.values() for k in e], [(None, None)])

    def test_bluray_parent_dir_still_works(self):
        vf = make_vf("00000.m2ts", "/lib/Show.S01_E02/BDMV/STREAM")
        groups = group_episodes([vf])
        self.assertEqual([k for e in groups.values() for k in e], [(1, 2)])

    def test_unparsed_zero_duration_never_deleted(self):
        a = make_vf("a.mkv", "/a", duration=0)
        b = make_vf("b.mkv", "/b", duration=0)
        _, delete, review = cli.deletion_candidates({"t": {(None, None): [a, b]}})
        self.assertEqual(delete, [])
        self.assertTrue(review)

    def test_season_in_title_tail_format(self):
        n = "Interny-13.(01.seriya).2015.WEB-DL.(720p).MediaClub.mkv"
        self.assertEqual(parse_episode(n), (13, 1))
        self.assertEqual(normalize_title(n), "interny")

    def test_278_episode_series_resolves_to_real_identities(self):
        seasons = [20] * 13 + [18]           # 278 episodes
        files, absolute = [], 0
        for s, count in enumerate(seasons, 1):
            for e in range(1, count + 1):
                absolute += 1
                files += [
                    make_vf(f"Интерны. Сезон №{s}. Серия №{absolute:03d}.avi", "/video"),
                    make_vf(f"Interny.s{s:02d}e{e:02d}.({absolute})XviD.[tahiy].avi", "/torrents/x"),
                    make_vf(f"Interny-{s}.({e:02d}.seriya).2015.WEB-DL.(720p).MediaClub.mkv", "/torrents/y"),
                ]
        groups = group_episodes(files)
        keys = [(k, len(v)) for eps in groups.values() for k, v in eps.items()]
        self.assertEqual(len(keys), 278)
        self.assertTrue(all(n == 3 for _, n in keys), [k for k in keys if k[1] != 3][:5])
        self.assertEqual(sorted(k for (k, _) in keys), [(None, i) for i in range(1, 279)])
        _, delete, _ = cli.deletion_candidates(groups, comparer=None)
        # every deletion candidate shares a proven episode with its keeper
        self.assertEqual(len(delete), 278 * 2)

    def test_unreconcilable_numbering_never_merges_different_episodes(self):
        a = make_vf("Интерны. Сезон №4. Серия №071.avi", "/video")
        c = make_vf("Interny-13.(01.seriya).2015.mkv", "/y")      # no offset table
        groups = group_episodes([a, c])
        sizes = sorted(len(v) for eps in groups.values() for v in eps.values())
        self.assertEqual(sizes, [1, 1])

    def test_existing_patterns(self):
        self.assertEqual(parse_episode("Show.S01E02.mkv"), (1, 2))
        self.assertEqual(parse_episode("Show.S01.E02.mkv"), (1, 2))
        self.assertEqual(parse_episode("Show.S2_E05.mkv"), (2, 5))
        self.assertEqual(parse_episode("Show.1x03.mkv"), (1, 3))
        self.assertEqual(parse_episode("Show Season 2 Episode 4.mkv"), (2, 4))
        self.assertEqual(parse_episode("Movie.2010.mkv"), (None, None))

    def test_directory_names_keep_marker(self):
        self.assertEqual(parse_episode("Show.S01_E02"), (1, 2))
        self.assertEqual(parse_episode("Show.S01_E02.BluRay"), (1, 2))

    def test_absolute_number_reconciles_releases(self):
        a = make_vf("Interny.s04e11.(071)XviD.avi", "/a")
        b = make_vf("Интерны. Сезон №4. Серия №071.avi", "/b")
        groups = group_episodes([a, b])
        keys = [k for eps in groups.values() for k in eps]
        self.assertEqual(keys, [(None, 71)])

    def test_unparsed_same_directory_never_deleted(self):
        files = [make_vf(f"Interny-13.({n:02d}.seriya).2015.mkv", "/torrents/x",
                         score=10 - n) for n in range(1, 5)]
        files.append(make_vf("Интерны Серия ???.mkv", "/other"))
        groups = {"interny": {(None, None): files}}
        keep, delete, review = cli.deletion_candidates(groups)
        self.assertEqual(delete, [])
        self.assertEqual(len(review), 1)

    def test_unparsed_different_duration_never_deleted(self):
        a = make_vf("one.mkv", "/a", duration=1300)
        b = make_vf("two.mkv", "/b", duration=2600)
        _, delete, review = cli.deletion_candidates({"t": {(None, None): [a, b]}})
        self.assertEqual(delete, [])
        self.assertTrue(review)

    def test_unparsed_movie_duplicates_still_work(self):
        a = make_vf("Movie.mkv", "/a", duration=6000, score=9)
        b = make_vf("Movie.1080p.mkv", "/b", duration=6010, score=5)
        keep, delete, review = cli.deletion_candidates({"movie": {(None, None): [a, b]}})
        self.assertEqual(keep, [a])
        self.assertEqual(delete, [b])
        self.assertEqual(review, [])

    def test_parsed_episode_duplicates(self):
        a = make_vf("S01E01.a.mkv", "/a", score=9)
        b = make_vf("S01E01.b.mkv", "/b", score=5)
        keep, delete, _ = cli.deletion_candidates({"s": {(1, 1): [a, b]}})
        self.assertEqual((keep, delete), ([a], [b]))


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg required")
class Stereo3DTests(unittest.TestCase):  # issue #15
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def make(self, name, *extra):
        out = Path(self.tmp) / name
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                        "testsrc=s=1920x1080:d=2:r=24", "-c:v", "libx264",
                        *extra, str(out)], check=True)
        vf = probe_file(out, use_cache=False)
        detect_3d(out, vf)
        return vf.video

    def test_mkv_left_right(self):
        v = self.make("plain.mkv", "-metadata:s:v:0", "stereo_mode=left_right")
        self.assertTrue(v.is_3d)
        self.assertEqual(v.stereo_mode, "side_by_side")
        self.assertEqual((v.eye_width, v.eye_height), (960, 1080))
        self.assertFalse(v.stereo_full_res)

    def test_mkv_top_bottom(self):
        v = self.make("plain2.mkv", "-metadata:s:v:0", "stereo_mode=top_bottom")
        self.assertEqual(v.stereo_mode, "top_bottom")
        self.assertEqual((v.eye_width, v.eye_height), (1920, 540))

    def test_filename_hint_without_metadata(self):
        out = Path(self.tmp) / "flat.3D.SBS.mkv"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                        "testsrc=s=1920x1080:d=2:r=24", "-c:v", "libx264",
                        "-vf", "setsar=1", str(out)], check=True)
        vf = probe_file(out, use_cache=False)
        detect_3d(out, vf)
        self.assertTrue(vf.video.is_3d)  # no metadata: filename hint applies

    def test_explicit_2d_metadata_beats_filename(self):
        from unittest import mock
        from procutil import ToolResult
        side = json.dumps({"streams": [{"side_data_list": [
            {"side_data_type": "Stereo 3D", "type": "2D"}]}]})
        vf = VideoFile(path=Path("movie.3D.SBS.mkv"))
        with mock.patch("probe.run_tool", return_value=ToolResult(0, side, "")):
            detect_3d(vf.path, vf)
        self.assertFalse(vf.video.is_3d)

    def test_filename_false_positive(self):
        v = self.make("Four.Brothers.2005.mkv")
        self.assertFalse(v.is_3d)



# ---------------------------------------------------------------- issue #12
import random  # noqa: E402

from integrity import check_full  # noqa: E402
from models import IntegrityResult  # noqa: E402
from scoring import rank_key  # noqa: E402


def _mk(cmd):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *cmd], check=True)


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg required")
class IntegrityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        t = cls.tmp
        _mk(["-f", "lavfi", "-i", "testsrc2=s=640x360:d=20:r=25", "-f", "lavfi",
             "-i", "sine=f=440:d=20", "-c:v", "libx264", "-g", "25", "-c:a", "aac",
             "-shortest", str(t / "clean.mp4")])
        data = (t / "clean.mp4").read_bytes()
        # localized video corruption in the middle of the file
        bad = bytearray(data)
        rnd = random.Random(1)
        mid = len(bad) // 2
        for i in range(mid, mid + 400):
            bad[i] = rnd.randrange(256)
        (t / "corrupt.mp4").write_bytes(bytes(bad))
        # truncated
        (t / "truncated.mp4").write_bytes(data[: int(len(data) * 0.6)])
        # clean file with different metadata only
        _mk(["-i", str(t / "clean.mp4"), "-c", "copy", "-metadata", "title=Другой",
             "-metadata", "comment=x", str(t / "meta.mp4")])
        # audio-only file, corrupted
        _mk(["-f", "lavfi", "-i", "sine=f=330:d=20", "-c:a", "mp3", str(t / "a.mp3")])
        a = bytearray((t / "a.mp3").read_bytes())
        for i in range(len(a) // 2, len(a) // 2 + 300):
            a[i] = rnd.randrange(256)
        (t / "a_bad.mp3").write_bytes(bytes(a))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_clean(self):
        r = check_full(self.tmp / "clean.mp4", 20)
        self.assertTrue(r.verified_clean, r.errors[:3])

    def test_clean_with_different_metadata(self):
        self.assertTrue(check_full(self.tmp / "meta.mp4", 20).verified_clean)

    def test_localized_video_corruption(self):
        r = check_full(self.tmp / "corrupt.mp4", 20)
        self.assertTrue(r.damaged)
        self.assertIn("video", r.streams_affected())
        self.assertIsNotNone(r.first_error_time)

    def test_truncated(self):
        r = check_full(self.tmp / "truncated.mp4", 20)
        self.assertTrue(r.damaged)

    def test_audio_only_corruption_attributed_to_audio(self):
        r = check_full(self.tmp / "a_bad.mp3", 20)
        self.assertTrue(r.damaged, "mp3 corruption should be reported")
        self.assertEqual(set(r.streams_affected()) - {"audio", "container", "unknown"}, set())

    def test_unreadable_file(self):
        r = check_full(self.tmp / "missing.mp4", 20)
        self.assertTrue(r.damaged)

    def test_error_span_not_truncated_by_issue_cap(self):
        from integrity import _add_issue
        r = IntegrityResult(checked=True)
        for i in range(500):
            _add_issue(r, "[h264 @ 0x1] error", "", float(i))
        _add_issue(r, "[aac @ 0x2] error", "", 900.0)
        self.assertEqual(r.error_count, 501)
        self.assertEqual(r.last_error_time, 900.0)
        self.assertEqual(r.streams_affected(), ["audio", "video"])

    def test_stream_attribution_new_and_old_ffmpeg(self):
        from integrity import classify_stream
        self.assertEqual(classify_stream("[vist#0:0/h264 @ 0x1] x"), "video")
        self.assertEqual(classify_stream("[aist#0:1/aac @ 0x1] x"), "audio")
        self.assertEqual(classify_stream("[h264_cuvid @ 0x1] x"), "video")
        self.assertEqual(classify_stream("[mp3float @ 0x1] x"), "audio")
        self.assertEqual(classify_stream("[mpegts @ 0x1] x"), "container")
        self.assertEqual(classify_stream("[matroska,webm @ 0x1] x"), "container")

    def test_quick_check_does_not_invent_times(self):
        from integrity import _add_issue
        r = IntegrityResult(checked=True)
        _add_issue(r, "[h264 @ 0x1] error", "start")
        self.assertIsNone(r.first_error_time)

    def test_summary_tie_logic_prefers_clean(self):
        good = make_vf("a.mkv", "/a", score=5)
        bad = make_vf("b.mkv", "/b", score=50)
        bad.integrity = IntegrityResult(checked=True, full_checked=True, error_count=3)
        from comparator import compare_pair
        comp = compare_pair(good, bad)
        self.assertIs(comp["absolute_winner"], good)
        self.assertTrue(comp["damage_decided"])

    def test_damaged_never_wins_or_gets_deleted_clean_never_deleted_for_it(self):
        good = make_vf("S01E01.good.mkv", "/a", score=5)
        good.integrity = check_full(self.tmp / "clean.mp4", 20)
        bad = make_vf("S01E01.bad.mkv", "/b", score=50)  # much higher nominal score
        bad.integrity = check_full(self.tmp / "corrupt.mp4", 20)
        self.assertGreater(rank_key(good), rank_key(bad))
        keep, delete, review = cli.deletion_candidates({"s": {(1, 1): [good, bad]}})
        self.assertEqual((keep, delete), ([good], [bad]))
        self.assertEqual(cli.damaged_without_replacement({"s": {(1, 1): [good, bad]}}), [])

    def test_damaged_without_verified_replacement_is_kept(self):
        bad = make_vf("S01E01.bad.mkv", "/b", score=50)
        bad.integrity = check_full(self.tmp / "corrupt.mp4", 20)
        unverified = make_vf("S01E01.other.mkv", "/a", score=5)
        unverified.integrity = IntegrityResult(checked=True)  # quick only
        groups = {"s": {(1, 1): [bad, unverified]}}
        keep, delete, review = cli.deletion_candidates(groups)
        self.assertEqual(delete, [])
        self.assertTrue(review)
        self.assertEqual(cli.damaged_without_replacement(groups), [bad])

    def test_all_damaged_none_deleted(self):
        a = make_vf("S01E01.a.mkv", "/a")
        b = make_vf("S01E01.b.mkv", "/b")
        a.integrity = check_full(self.tmp / "corrupt.mp4", 20)
        b.integrity = check_full(self.tmp / "truncated.mp4", 20)
        _, delete, review = cli.deletion_candidates({"s": {(1, 1): [a, b]}})
        self.assertEqual(delete, [])
        self.assertTrue(review)

    def test_unchecked_files_not_deleted(self):
        a = make_vf("S01E01.a.mkv", "/a", score=9, verified=False)
        b = make_vf("S01E01.b.mkv", "/b", score=5, verified=False)
        _, delete, _ = cli.deletion_candidates({"s": {(1, 1): [a, b]}})
        self.assertEqual(delete, [])


# ---------------------------------------------------------------- issue #13
from channels import analyze_channels  # noqa: E402


def _noise(seed, amp=0.2):
    return f"anoisesrc=d=130:r=16000:a={amp}:seed={seed}:c=white"


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg required")
class ChannelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        t = cls.tmp

        def make(name, inputs, graph, ch_codec="flac"):
            cmd = []
            for src in inputs:
                cmd += ["-f", "lavfi", "-i", src]
            cmd += ["-filter_complex", graph, "-c:a", ch_codec, str(t / name)]
            _mk(cmd)

        six = [_noise(i) for i in range(1, 7)]
        make("discrete51.flac", six,
             "[0][1][2][3][4][5]amerge=inputs=6")
        make("mono51.flac", [six[0]],
             "pan=5.1|c0=c0|c1=c0|c2=c0|c3=c0|c4=c0|c5=c0")
        make("stereo_real.flac", six[:2], "[0][1]amerge=inputs=2")
        make("stereo_mono.flac", [six[0]], "pan=stereo|c0=c0|c1=c0")
        make("phase.flac", [six[0]], "pan=stereo|c0=c0|c1=-1*c0")
        # matrixed: C = mono, LFE quiet noise, SL = L-R, SR = R-L
        make("matrixed.flac", six[:3],
             "[0][1][2]amerge=inputs=3,"
             "pan=5.1|c0=c0|c1=c1|c2=c2|c3=0.05*c2|c4=c0-c1|c5=c1-c0")
        # surrounds only sound in the first half: silent in 2 of 4 scenes
        make("quiet_surround.flac", six,
             "[0][1][2][3][4][5]amerge=inputs=6,"
             "pan=5.1|c0=c0|c1=c1|c2=c2|c3=c3|c4=c4|c5=c5,"
             "volume=0:enable='gt(t,70)'")
        # dialogue (centre only) in the first three scenes, full mix in the last
        gate = "volume=0:enable='lt(t,90)'"
        make("dialogue_then_music.flac", six,
             f"[0]{gate}[a];[1]{gate}[b];[3]{gate}[d];[4]{gate}[e];[5]{gate}[f];"
             "[a][b][2][d][e][f]amerge=inputs=6")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def run_one(self, name, channels):
        return analyze_channels(self.tmp / name, 0, channels, 130.0)

    def test_discrete_51(self):
        r = self.run_one("discrete51.flac", 6)
        self.assertEqual(r.real_channels, 6, r.notes)
        self.assertEqual(r.confidence, "high")

    def test_mono_packed_in_51(self):
        r = self.run_one("mono51.flac", 6)
        self.assertEqual(r.real_channels, 1, r.notes)
        self.assertTrue(any("identical" in n for n in r.notes))

    def test_real_stereo_is_not_mono(self):
        # old heuristic: equal L/R levels => "mono"
        self.assertEqual(self.run_one("stereo_real.flac", 2).real_channels, 2)

    def test_stereo_with_identical_channels(self):
        self.assertEqual(self.run_one("stereo_mono.flac", 2).real_channels, 1)

    def test_phase_inverted_copy(self):
        r = self.run_one("phase.flac", 2)
        self.assertEqual(r.real_channels, 1)
        self.assertTrue(any("phase-inverted" in n for n in r.notes), r.notes)

    def test_matrixed_upmix(self):
        r = self.run_one("matrixed.flac", 6)
        self.assertTrue(any("matrixed" in n for n in r.notes), r.notes)
        self.assertEqual(r.real_channels, 3)  # L, R, C

    def test_temporarily_silent_surrounds_are_not_fake(self):
        r = self.run_one("quiet_surround.flac", 6)
        self.assertEqual(r.real_channels, 6, r.notes)

    def test_dialogue_only_scenes_are_not_fake(self):
        r = self.run_one("dialogue_then_music.flac", 6)
        self.assertEqual(r.real_channels, 6, r.notes)

    def test_channel_loud_where_other_is_nearly_silent_is_not_a_copy(self):
        # ch1 == ch2 in the first half; in the second ch2 is -65 dB while ch1 is loud
        t = self.tmp
        _mk(["-f", "lavfi", "-i", _noise(1), "-filter_complex",
             "pan=stereo|c0=c0|c1=c0,volume=0.0005:enable='gt(t,70)'",
             "-c:a", "flac", str(t / "tmp_a.flac")])
        _mk(["-f", "lavfi", "-i", _noise(1), "-f", "lavfi", "-i", _noise(1),
             "-filter_complex",
             "[0]anull[a];[1]volume=0.0005:enable='gt(t,70)'[b];[a][b]amerge=inputs=2",
             "-c:a", "flac", str(t / "one_sided.flac")])
        self.assertEqual(self.run_one("one_sided.flac", 2).real_channels, 2)

    def test_silent_file_gives_no_verdict(self):
        _mk(["-f", "lavfi", "-i", "anullsrc=r=16000:cl=stereo", "-t", "130",
             "-c:a", "flac", str(self.tmp / "silence.flac")])
        r = self.run_one("silence.flac", 2)
        self.assertIsNone(r.real_channels)

    def test_mono_track_untouched(self):
        self.assertIsNone(analyze_channels(self.tmp / "x", 0, 1, 130).real_channels)

    def test_undecodable_gives_no_verdict(self):
        r = analyze_channels(self.tmp / "missing.flac", 0, 6, 130.0)
        self.assertIsNone(r.real_channels)


# ---------------------------------------------------------------- issue #7
from probe import detect_crop_and_upscale, stable_crop  # noqa: E402
from scoring import bits_per_pixel, score_resolution  # noqa: E402


class StableCropTests(unittest.TestCase):
    def test_agreeing_samples(self):
        self.assertEqual(stable_crop([(1440, 1080)] * 5), (1440, 1080))

    def test_dark_scene_does_not_shrink_crop(self):
        samples = [(1440, 1080)] * 4 + [(640, 480)]  # one dark scene
        self.assertEqual(stable_crop(samples), (1440, 1080))

    def test_subtitles_in_bars_do_not_discard_the_crop(self):
        samples = [(1920, 800)] * 5 + [(1920, 1080)]
        self.assertEqual(stable_crop(samples), (1920, 800))

    def test_disagreement_is_uncertain(self):
        self.assertIsNone(stable_crop([(1920, 800), (1440, 1080), (1920, 1080),
                                       (1280, 720), (1000, 1080)]))

    def test_unusable_samples(self):
        self.assertIsNone(stable_crop([]))
        self.assertIsNone(stable_crop([(1440, 1080)]))
        self.assertIsNone(stable_crop([(2, 2), (4, 4)]))


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg required")
class BlackBarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        t = cls.tmp
        src = "testsrc2=s=720x540:d=30:r=24"
        _mk(["-f", "lavfi", "-i", src, "-vf", "pad=960:540:120:0",
             "-c:v", "libx264", "-b:v", "2M", str(t / "pillar.mkv")])
        _mk(["-f", "lavfi", "-i", src, "-c:v", "libx264", "-b:v", "2M",
             str(t / "cropped.mkv")])
        _mk(["-f", "lavfi", "-i", "testsrc2=s=960x400:d=30:r=24",
             "-vf", "pad=960:540:0:70", "-c:v", "libx264", "-b:v", "2M",
             str(t / "letter.mkv")])
        _mk(["-f", "lavfi", "-i", "testsrc2=s=960x540:d=30:r=24",
             "-c:v", "libx264", "-b:v", "2M", str(t / "free.mkv")])
        # pillarboxed, with black scenes covering 40% of the film
        _mk(["-f", "lavfi", "-i", src, "-vf",
             "pad=960:540:120:0,drawbox=0:0:960:540:black:t=fill:"
             "enable='between(t,8,20)'",
             "-c:v", "libx264", "-b:v", "2M", str(t / "dark.mkv")])

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def probed(self, name):
        p = self.tmp / name
        vf = probe_file(p, use_cache=False)
        detect_crop_and_upscale(p, vf)
        return vf

    def test_pillarbox(self):
        v = self.probed("pillar.mkv").video
        self.assertTrue(v.has_black_bars)
        self.assertAlmostEqual(v.active_width, 720, delta=8)
        self.assertEqual(v.active_height, 540)

    def test_letterbox(self):
        v = self.probed("letter.mkv").video
        self.assertAlmostEqual(v.active_height, 400, delta=8)

    def test_crop_free(self):
        v = self.probed("free.mkv").video
        self.assertFalse(v.has_black_bars)
        self.assertEqual((v.active_width, v.active_height), (960, 540))

    def test_dark_scenes_do_not_fake_a_crop(self):
        v = self.probed("dark.mkv").video
        self.assertAlmostEqual(v.active_width, 720, delta=8)
        self.assertEqual(v.active_height, 540)

    def test_bars_earn_no_resolution_or_bpp(self):
        a, b = self.probed("pillar.mkv"), self.probed("cropped.mkv")
        self.assertAlmostEqual(score_resolution(a), score_resolution(b), delta=0.3)
        self.assertAlmostEqual(bits_per_pixel(a), bits_per_pixel(b),
                               delta=bits_per_pixel(b) * 0.1)


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------- issue #2 (rest)
from languages import (OverrideStoreError, apply_overrides,  # noqa: E402
                       language_label, resolve_language, save_override, title_hint)


class LanguageEvidenceTests(unittest.TestCase):
    def track(self, lang="", title="", override=""):
        return AudioTrack(codec="ac3", channels=2, language=lang, title=title,
                          language_override=override)

    def test_sources(self):
        self.assertEqual(resolve_language(self.track("rus")), ("rus", "tag"))
        self.assertEqual(resolve_language(self.track("ru")), ("rus", "tag"))
        self.assertEqual(resolve_language(self.track("", "Russian 5.1")), ("", ""))   # hint only
        self.assertEqual(title_hint(self.track("", "Russian 5.1")), "rus")
        self.assertEqual(title_hint(self.track("und", "Русский")), "rus")
        self.assertEqual(title_hint(self.track("", "DUB")), "")
        self.assertEqual(resolve_language(self.track("", "DUB")), ("", ""))
        self.assertEqual(resolve_language(self.track("", "")), ("", ""))
        self.assertEqual(resolve_language(self.track("", "", "ru")), ("rus", "override"))

    def test_conflicting_title_is_not_even_a_hint(self):
        self.assertEqual(title_hint(self.track("", "Russian / English")), "")

    def test_commentary_titles_cannot_hide_a_missing_primary_track(self):
        vf = make_vf(langs=("",))
        vf.audio_tracks[0].title = "Original (Russian dub)"
        self.assertEqual(primary_language_status(vf, "rus"), "unknown")
        vf.audio_tracks[0].title = "English commentary"
        self.assertEqual(primary_language_status(vf, "eng"), "unknown")

    def test_tag_beats_title_override_beats_tag(self):
        self.assertEqual(resolve_language(self.track("eng", "Russian")), ("eng", "tag"))
        self.assertEqual(resolve_language(self.track("eng", "", "rus")), ("rus", "override"))

    def test_labels(self):
        self.assertEqual(language_label(self.track("rus")), "rus (tag)")
        self.assertEqual(language_label(self.track("")), "unknown (untagged)")
        self.assertEqual(language_label(self.track("", "Russian")), "unknown (title suggests rus)")

    def test_filename_never_counts(self):
        vf = make_vf("Фильм.на.русском.mkv", langs=("",))
        self.assertEqual(primary_language_status(vf, "rus"), "unknown")

    def test_status_uses_resolved_language(self):
        vf = make_vf(langs=("",))
        vf.audio_tracks[0].language_override = "rus"
        self.assertEqual(primary_language_status(vf, "rus"), "present")
        self.assertEqual(score_language_tracks(vf, "rus"), 0.0)

    def test_confirmed_track_is_not_penalized_like_absent(self):
        absent = make_vf(langs=("eng",))
        confirmed = make_vf(langs=("",))
        confirmed.audio_tracks[0].language_override = "rus"
        self.assertEqual(score_language_tracks(absent, "rus"), -10.0)
        self.assertEqual(score_language_tracks(confirmed, "rus"), 0.0)

    def test_override_roundtrip_and_removal(self):
        with tempfile.TemporaryDirectory() as d:
            media = Path(d) / "movie.avi"
            media.write_bytes(b"x" * 10)
            store = Path(d) / "ov.json"
            vf = make_vf(str(media.name), d, langs=("", "eng"))
            vf.path = media
            save_override(media, 0, "ru", store)
            apply_overrides(vf, store)
            self.assertEqual(resolve_language(vf.audio_tracks[0]), ("rus", "override"))
            self.assertEqual(vf.audio_tracks[1].language_override, "")
            save_override(media, 0, "", store)           # removal
            apply_overrides(vf, store)
            self.assertEqual(resolve_language(vf.audio_tracks[0]), ("", ""))

    def test_changed_file_loses_the_confirmation(self):
        with tempfile.TemporaryDirectory() as d:
            media = Path(d) / "movie.avi"
            media.write_bytes(b"x" * 10)
            store = Path(d) / "ov.json"
            vf = make_vf("movie.avi", d, langs=("",))
            vf.path = media
            save_override(media, 0, "rus", store)
            media.write_bytes(b"y" * 20)                  # replaced by another file
            apply_overrides(vf, store)
            self.assertEqual(vf.audio_tracks[0].language_override, "")

    def test_unknown_language_code_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            media = Path(d) / "m.avi"
            media.write_bytes(b"x")
            with self.assertRaises(ValueError):
                save_override(media, 0, "rsu", Path(d) / "ov.json")
            self.assertFalse((Path(d) / "ov.json").exists())

    def test_damaged_store_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as d:
            media, store = Path(d) / "m.avi", Path(d) / "ov.json"
            media.write_bytes(b"x")
            store.write_text("{not json", encoding="utf-8")
            with self.assertRaises(OverrideStoreError):
                save_override(media, 0, "rus", store)
            self.assertEqual(store.read_text(encoding="utf-8"), "{not json")
            store.write_text("[1, 2]", encoding="utf-8")        # valid JSON, wrong shape
            with self.assertRaises(OverrideStoreError):
                save_override(media, 0, "rus", store)

    def test_unwritable_store_reports_cleanly(self):
        with tempfile.TemporaryDirectory() as d:
            media = Path(d) / "m.avi"
            media.write_bytes(b"x")
            with self.assertRaises(OverrideStoreError):
                save_override(media, 0, "rus", Path(d) / "no_such_dir" / "ov.json")

    def test_override_never_enters_the_probe_cache(self):
        import cache
        vf = make_vf(langs=("",))
        vf.audio_tracks[0].language_override = "rus"
        data = cache._serialize(vf)
        self.assertNotIn("language_override", data["audio_tracks"][0])

    def test_set_language_cli_checks_the_track(self):
        if not HAVE_FFMPEG:
            self.skipTest("ffmpeg required")
        with tempfile.TemporaryDirectory() as d:
            media = Path(d) / "m.mkv"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=d=1",
                            str(media)], check=True)
            run = lambda *a: subprocess.run([sys.executable, str(ROOT / "cli.py"), *a],
                                            capture_output=True, text=True, timeout=60)
            self.assertEqual(run("--set-language", str(media), "5", "rus").returncode, 2)
            self.assertEqual(run("--set-language", str(media), "1", "klingon").returncode, 2)
            self.assertEqual(run("--set-language", str(media), "x", "rus").returncode, 2)

    def test_override_is_reset_not_taken_from_cache(self):
        vf = make_vf(langs=("",))
        vf.audio_tracks[0].language_override = "rus"      # stale value from a cached copy
        with tempfile.TemporaryDirectory() as d:
            apply_overrides(vf, Path(d) / "none.json")
        self.assertEqual(vf.audio_tracks[0].language_override, "")
