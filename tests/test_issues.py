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
from models import AudioTrack, VideoFile  # noqa: E402
from probe import (detect_3d, effective_fps, normalize_level,  # noqa: E402
                   probe_file)
from procutil import decode_text, run_tool  # noqa: E402
from scoring import score_language_tracks  # noqa: E402

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def make_vf(name="a.mkv", parent="/x", duration=1300.0, langs=("rus",), score=10.0):
    vf = VideoFile(path=Path(parent) / name, duration=duration, score=score)
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

    def test_year_and_resolution_are_not_absolute(self):
        self.assertIsNone(parse_absolute_episode("Show.S01E02.(2019).mkv"))
        self.assertIsNone(parse_absolute_episode("Show S01E01 (1080).mkv"))
        self.assertIsNone(parse_absolute_episode("Сезон 1 Серия 100.mkv"))
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


if __name__ == "__main__":
    unittest.main()
