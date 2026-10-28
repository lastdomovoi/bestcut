"""Frame cadence (issue #9): timestamps, VFR, telecine, repeated frames."""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import cadence as cd  # noqa: E402
from models import VideoTrack  # noqa: E402
from probe import probe_file  # noqa: E402

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


class IntervalStatsTests(unittest.TestCase):
    def cfr(self, fps, n=200):
        return [i / fps for i in range(n)]

    def test_constant_rate(self):
        for fps in (23.976, 25.0, 29.97, 50.0, 59.94):
            st = cd.interval_stats(self.cfr(fps), [])
            self.assertAlmostEqual(1 / st["mode"], fps, delta=fps * 0.002)
            self.assertGreater(st["stable_share"], 0.99)
            self.assertEqual((st["duplicates"], st["gaps"]), (0, 0))

    def test_decode_order_does_not_matter(self):
        pts = self.cfr(25)
        shuffled = pts[:]
        for i in range(0, len(shuffled) - 3, 4):          # B-frame style reordering
            shuffled[i + 1], shuffled[i + 2] = shuffled[i + 2], shuffled[i + 1]
        st = cd.interval_stats(shuffled, [])
        self.assertEqual(st["gaps"], 0)
        self.assertGreater(st["stable_share"], 0.99)

    def test_millisecond_rounded_timestamps_stay_constant(self):
        # MKV rounds to 1 ms: 30/60/119.88 fps look irregular but are constant
        for fps in (29.97, 30.0, 59.94, 60.0, 119.88):
            pts = [round(i / fps, 3) for i in range(300)]
            st = cd.interval_stats(pts, [])
            self.assertGreater(st["stable_share"], 0.99, fps)
            self.assertEqual(st["gaps"], 0, fps)

    def test_duplicate_timestamps(self):
        pts = self.cfr(25)
        pts[50] = pts[49]
        pts[90] = pts[89]
        self.assertEqual(cd.interval_stats(pts, [])["duplicates"], 2)

    def test_dropped_frames_are_gaps(self):
        pts = [t for i, t in enumerate(self.cfr(25, 400)) if i % 20 != 7]
        st = cd.interval_stats(pts, [])
        self.assertGreaterEqual(st["gaps"], 19)            # 20 dropped, tail trimmed
        self.assertLessEqual(st["gaps"], 20)

    def test_window_tail_is_not_a_gap(self):
        pts = self.cfr(25, 300)
        decode_window = [t for t in pts[:300] if t not in pts[288:290]]  # B-frames not read yet
        self.assertEqual(cd.interval_stats(decode_window, [])["gaps"], 0)

    def test_variable_rate_is_unstable(self):
        import random
        rnd = random.Random(3)
        t, pts = 0.0, []
        for _ in range(300):
            pts.append(t)
            t += rnd.choice((0.02, 0.04, 0.08, 0.16))
        self.assertLess(cd.interval_stats(pts, [])["stable_share"], 0.9)

    def test_non_monotonic_dts(self):
        dts = [i * 0.04 for i in range(100)]
        dts[40], dts[41] = dts[41], dts[40]
        self.assertEqual(cd.interval_stats(self.cfr(25, 100), dts)["non_monotonic_dts"], 1)

    def test_too_few_packets(self):
        self.assertIsNone(cd.interval_stats([0, 0.04, 0.08], [])["mode"])

    def test_idet_parsing(self):
        text = ("[Parsed_idet_0 @ 0x1] Repeated Fields: Neither:    72 Top:     0 Bottom:     0\n"
                "[Parsed_idet_0 @ 0x1] Multi frame detection: TFF:     0 BFF:     0 Progressive:    72 Undetermined:     0\n"
                "[Parsed_idet_0 @ 0x2] Repeated Fields: Neither:    60 Top:    20 Bottom:    20\n"
                "[Parsed_idet_0 @ 0x2] Multi frame detection: TFF:    90 BFF:     0 Progressive:     5 Undetermined:     5\n")
        got = cd.idet_summary(text)                       # the last filter instance counts
        self.assertAlmostEqual(got["repeat_share"], 0.4)
        self.assertAlmostEqual(got["interlaced_share"], 0.9)   # informational only
        self.assertIsNone(cd.idet_summary("nothing"))


class MeasureCadenceTests(unittest.TestCase):
    def test_interlacing_comes_from_field_order(self):
        from unittest import mock
        from models import VideoFile
        from probe import measure_cadence
        for order, expect in (("tt", True), ("progressive", False), ("", False)):
            vf = VideoFile(path=Path("x.mkv"), duration=100.0)
            vf.video.field_order = order
            with mock.patch("probe.analyze_cadence", return_value=cd.Cadence()):
                self.assertTrue(measure_cadence(vf.path, vf))
                self.assertFalse(measure_cadence(vf.path, vf))   # measured once
            self.assertEqual(vf.video.interlaced, expect)
            self.assertEqual("interlaced" in vf.video.cadence_notes, expect)


class QualityFpsTests(unittest.TestCase):
    def test_repeated_pictures_lower_the_quality_rate(self):
        v = VideoTrack(fps=30.0)
        self.assertEqual(v.quality_fps, 30.0)
        v.repeated_frame_share = 0.2
        self.assertAlmostEqual(v.quality_fps, 24.0)
        v.telecine = True                    # telecine is film cadence, keep the rate
        self.assertEqual(v.quality_fps, 30.0)
        v.telecine, v.repeated_frame_share = False, 0.05
        self.assertEqual(v.quality_fps, 30.0)


def _make(path, vf, fps=25, seconds=60, extra=()):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"testsrc2=s=320x180:r={fps}:d={seconds}", "-vf", vf, *extra,
                    "-c:v", "libx264", "-preset", "veryfast", str(path)], check=True)


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg required")
class FileCadenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        t = cls.tmp
        _make(t / "cfr.mkv", "null")
        _make(t / "vfr.mkv", "select='gt(random(0),0.45)'", extra=("-fps_mode", "vfr"))
        _make(t / "gaps.mkv", "select='mod(n,25)'", extra=("-fps_mode", "vfr"))
        _make(t / "telecine.mkv", "fps=24000/1001,telecine=pattern=23", fps=24)
        _make(t / "padded.mkv", "fps=30", fps=24)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def run_one(self, name):
        p = self.tmp / name
        vf = probe_file(p, use_cache=False)
        return cd.analyze_cadence(p, vf.duration, vf.video.nominal_fps, vf.video.fps)

    def test_cfr_is_clean(self):
        c = self.run_one("cfr.mkv")
        self.assertAlmostEqual(c.interval_fps, 25.0, delta=0.1)
        self.assertFalse(c.vfr)
        self.assertEqual((c.gaps, c.duplicate_timestamps), (0, 0))
        self.assertFalse(c.telecine)
        self.assertEqual(c.notes, [])
        self.assertFalse(c.interlaced)

    def test_variable_frame_rate(self):
        c = self.run_one("vfr.mkv")
        self.assertTrue(c.vfr)
        self.assertIn("variable frame rate", c.notes)

    def test_dropped_frames_are_reported(self):
        c = self.run_one("gaps.mkv")
        self.assertGreater(c.gaps, 10)

    def test_hard_telecine(self):
        c = self.run_one("telecine.mkv")
        self.assertTrue(c.telecine, c.notes)

    def test_padded_frame_rate_shows_repeated_pictures(self):
        c = self.run_one("padded.mkv")
        self.assertGreater(c.repeated_frame_share, 0.15)
        self.assertLess(c.repeated_frame_share, 0.30)
        self.assertFalse(c.telecine)
        self.assertTrue(any("repeated pictures" in n for n in c.notes))

    def test_static_stretch_is_not_a_padded_rate(self):
        from unittest import mock
        with mock.patch("cadence.repeated_share", side_effect=[0.6, 0.02]):
            self.assertAlmostEqual(cd.repeated_share_over(Path("x"), 100.0), 0.02)
        with mock.patch("cadence.repeated_share", side_effect=[0.2, None]):
            self.assertIsNone(cd.repeated_share_over(Path("x"), 100.0))

    def test_cfr_has_no_repeated_pictures(self):
        self.assertLess(self.run_one("cfr.mkv").repeated_frame_share or 0, 0.02)


if __name__ == "__main__":
    unittest.main()
