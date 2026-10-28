"""Episode-order anomalies across releases (issue #5)."""

import random
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import cli  # noqa: E402
import episode_sequence as es  # noqa: E402
from episode_sequence import ContentMatch  # noqa: E402
from models import IntegrityResult, VideoFile  # noqa: E402

N = 30


def base_durations():
    rnd = random.Random(7)
    return {k: rnd.uniform(1300, 1700) for k in range(1, N + 1)}


DUR = base_durations()


class World:
    """Two releases whose files each carry a hidden `content` episode id."""

    def __init__(self):
        self.content = {}          # path -> (episode id, share of it that is present)
        self.calls = 0
        self.base = {}
        self.other = {}
        for k in range(1, N + 1):
            self.base[(None, k)] = self.make("base", k, k, DUR[k])
            self.other[(None, k)] = self.make("other", k, k, DUR[k] * 1.002)

    def make(self, release, label, content, duration, share=1.0):
        vf = VideoFile(path=Path(f"/{release}/ep{label:03d}.mkv"), duration=duration,
                       file_size=1000)
        vf.integrity = IntegrityResult(checked=True, full_checked=True)
        self.content[vf.path] = (content, share)
        return vf

    def put(self, label, content, duration=None, share=1.0):
        d = DUR[content] if duration is None else duration
        self.other[(None, label)] = self.make("other", label, content, d, share)

    def matcher(self, a, b):
        self.calls += 1
        (ca, _), (cb, share) = self.content[a.path], self.content[b.path]
        return ContentMatch(share if ca == cb else 0.0, 0)

    def report(self, **kw):
        return es.analyze_pair("show", self.base, self.other, 0, 1, self.matcher, **kw)


def kinds(report):
    return {(f.kind, f.other_key[1] if f.other_key else None,
             f.mapped_key[1] if f.mapped_key else None)
            for f in report.findings if f.kind != "missing_in_other"}


class SequenceTests(unittest.TestCase):
    def test_clean_release_needs_no_decoding(self):
        w = World()
        r = w.report()
        self.assertEqual(r.anomalies(), [])
        self.assertEqual(w.calls, 0)          # durations alone raised nothing

    def test_normal_duration_variation_is_not_an_anomaly(self):
        w = World()
        rnd = random.Random(1)
        for k in range(1, N + 1):
            w.put(k, k, DUR[k] * (1 + rnd.uniform(-0.004, 0.004)))
        r = w.report()
        self.assertEqual(r.anomalies(), [])
        self.assertEqual(w.calls, 0)

    def test_adjacent_swap(self):
        w = World()
        w.put(10, 11)
        w.put(11, 10)
        r = w.report()
        self.assertEqual({(f.kind, f.other_key[1], f.mapped_key[1]) for f in r.anomalies()},
                         {("shifted", 10, 11), ("shifted", 11, 10)})
        self.assertEqual(set(r.blocked()), {(None, 10), (None, 11)})

    def test_one_episode_shift_is_a_persistent_run(self):
        w = World()
        for k in range(10, N + 1):
            w.put(k, k - 1)               # label k shows episode k-1
        r = w.report()
        shifted = [f for f in r.findings if f.kind == "shifted"]
        self.assertEqual(len(shifted), N - 10)
        self.assertTrue(all(f.mapped_key[1] == f.other_key[1] - 1 for f in shifted))
        # at the start of the shift episode 9 is shown twice (labels 9 and 10)
        self.assertEqual({f.other_key[1] for f in r.findings if f.kind == "duplicate"}, {9, 10})
        self.assertEqual(len(r.persistent), 1)
        self.assertIn("earlier", r.persistent[0])

    def test_missing_episode(self):
        w = World()
        del w.other[(None, 15)]
        r = w.report()
        gaps = [f for f in r.findings if f.kind == "missing_in_other"]
        self.assertEqual([g.mapped_key[1] for g in gaps], [15])
        self.assertEqual(r.anomalies(), [])      # a gap alone blocks nothing

    def test_duplicated_episode(self):
        w = World()
        w.put(21, 20, duration=DUR[20])          # 21 is a second copy of 20
        r = w.report()
        dup = [f for f in r.findings if f.kind == "duplicate"]
        self.assertEqual({f.other_key[1] for f in dup}, {20, 21})
        self.assertEqual({f.mapped_key[1] for f in dup}, {20})
        self.assertIn((None, 21), r.blocked())
        self.assertTrue(any(f.kind == "missing_in_other" and f.mapped_key[1] == 21
                            for f in r.findings))

    def test_truncated_cut(self):
        w = World()
        w.put(5, 5, duration=DUR[5] * 0.6, share=0.6)
        r = w.report()
        self.assertEqual([(f.kind, f.other_key[1]) for f in r.anomalies()], [("partial", 5)])

    def test_special_inserted_into_absolute_numbering(self):
        w = World()
        # a 31st file at 20 is a special; everything from 20 on moves up one
        specials = {}
        new = {}
        for k in range(1, N + 1):
            if k < 20:
                new[(None, k)] = w.other[(None, k)]
        w.other = new
        w.put(20, 99, duration=1100.0)            # the special (content unknown to base)
        w.content[w.other[(None, 20)].path] = (999, 1.0)
        for k in range(20, N + 1):
            w.put(k + 1, k)
        r = w.report()
        by_key = {f.other_key[1]: f for f in r.findings if f.other_key}
        self.assertEqual(by_key[20].kind, "unmatched")
        self.assertTrue(all(by_key[k + 1].kind == "shifted" for k in range(20, N + 1)
                            if k + 1 in by_key))
        self.assertTrue(r.persistent)
        self.assertIn("earlier", r.persistent[0])

    def test_unrelated_episodes_of_similar_length(self):
        w = World()
        for k in range(1, N + 1):
            w.put(k, 100 + k, duration=DUR[k] * 1.001)     # other show, same lengths
        self.assertEqual(w.report().anomalies(), [])       # durations cannot tell
        r = w.report(verify_all=True)                      # pictures can
        self.assertEqual({f.kind for f in r.anomalies()}, {"unmatched"})
        self.assertEqual(len(r.anomalies()), N)

    def test_issue_workset_swap_with_equal_duration(self):
        # tahiy 148 has the duration of main 147 and shows 147; tahiy 147 shows 146
        w = World()
        for k in (25, 26, 27, 28):
            w.put(k, k - 1)
        r = w.report()
        shifted = {(f.other_key[1], f.mapped_key[1]) for f in r.findings if f.kind == "shifted"}
        self.assertTrue({(26, 25), (27, 26), (28, 27)} <= shifted, shifted)
        self.assertTrue(es.analyze_pair('show', w.base, w.other, 0, 1, w.matcher).persistent)

    def test_decode_failure_is_not_reported_as_unmatched(self):
        w = World()
        w.put(10, 11)
        r = es.analyze_pair("show", w.base, w.other, 0, 1, lambda a, b: None)
        f = [x for x in r.findings if x.other_key == (None, 10)][0]
        self.assertEqual(f.kind, "unverified")
        self.assertIn((None, 10), r.blocked())

    def test_partial_cut_does_not_make_the_real_episode_a_duplicate(self):
        w = World()
        w.put(5, 6, duration=DUR[6] * 0.6, share=0.6)      # label 5: a cut-down episode 6
        r = w.report()
        kinds_ = {f.other_key[1]: f.kind for f in r.findings if f.other_key}
        self.assertEqual(kinds_.get(5), "partial")
        self.assertNotEqual(kinds_.get(6), "duplicate")

    def test_mixed_key_forms_are_not_neighbours(self):
        base = {(None, 5): VideoFile(path=Path("/b/a.mkv"), duration=1500.0),
                (3, 1): VideoFile(path=Path("/b/b.mkv"), duration=1500.0)}
        other = {(3, 1): VideoFile(path=Path("/o/b.mkv"), duration=1503.0)}
        self.assertEqual(es.screen(base, other), {})
        self.assertEqual(es._ordered([(None, 1), (2, 3), (1, 9)]), [(1, 9), (2, 3), (None, 1)])

    def test_without_matcher_only_suspicions_are_reported(self):
        w = World()
        w.put(10, 11)
        r = es.analyze_pair("show", w.base, w.other, 0, 1, matcher=None)
        self.assertEqual({f.kind for f in r.findings}, {"suspected"})
        self.assertFalse(r.verified)
        self.assertIn((None, 10), r.blocked())

    def test_false_alarm_is_cleared_by_content(self):
        w = World()
        w.put(7, 7, duration=DUR[7] * 1.05)      # re-timed, still the same picture
        r = w.report()
        self.assertEqual(r.anomalies(), [])
        self.assertEqual([f.kind for f in r.findings if f.other_key == (None, 7)], ["ok"])

    def test_format_mentions_remaps_and_never_acts(self):
        w = World()
        w.put(10, 11)
        w.put(11, 10)
        text = es.format_reports([w.report()])
        self.assertIn("remap candidate", text)
        self.assertIn("no files are renamed or deleted", text)


class DeletionBlockTests(unittest.TestCase):
    def test_blocked_episode_gets_no_deletion_advice(self):
        a = VideoFile(path=Path("/a/S01E05.mkv"), duration=1300, score=9)
        b = VideoFile(path=Path("/b/S01E05.mkv"), duration=1300, score=5)
        for f in (a, b):
            f.integrity = IntegrityResult(checked=True, full_checked=True)
        groups = {"show": {(1, 5): [a, b], (1, 6): []}}
        keep, delete, review = cli.deletion_candidates(
            groups, blocked={("show", (1, 5)): "episode order is ambiguous"})
        self.assertEqual(delete, [])
        self.assertIn("ambiguous", review[0][0])

    def test_assign_releases(self):
        with __import__("tempfile").TemporaryDirectory() as d:
            ra, rb = Path(d) / "a", Path(d) / "b"
            ra.mkdir()
            rb.mkdir()
            f1, f2 = ra / "x.mkv", rb / "sub" / "y.mkv"
            f2.parent.mkdir()
            f1.write_bytes(b"1")
            f2.write_bytes(b"2")
            files = [VideoFile(path=f1), VideoFile(path=f2)]
            es.assign_releases(files, [ra, rb])
            self.assertEqual([f.release for f in files], [0, 1])


import os  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402
import tempfile  # noqa: E402

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg required")
class RealFilesTests(unittest.TestCase):
    """Swapped episodes in real encodes: durations differ, pictures decide."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        os.environ["XDG_CACHE_HOME"] = str(cls.tmp / "cache")
        import cache
        cache.CACHE_DIR = Path(os.environ["XDG_CACHE_HOME"]) / "bestcut"
        cls.durations = {1: 24, 2: 30, 3: 36, 4: 42}
        for rel in ("rel_a", "rel_b"):
            (cls.tmp / rel).mkdir()
        for ep, dur in cls.durations.items():
            src = {1: "mandelbrot=s=320x180:r=24:end_scale=0.02,gblur=sigma=2",
                   2: "gradients=s=320x180:r=24:speed=0.03:nb_colors=4,gblur=sigma=1",
                   3: "life=s=320x180:r=24:ratio=0.15:mold=8:life_color=#00ff00,"
                      "scale=320:180:flags=neighbor,gblur=sigma=3",
                   4: "mandelbrot=s=320x180:r=24:start_scale=0.2:end_scale=0.001,"
                      "hue=h=120,gblur=sigma=2"}[ep]
            out = cls.tmp / "rel_a" / f"Show.S01E{ep:02d}.mkv"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", src, "-t",
                            str(dur), "-c:v", "libx264", "-crf", "24", str(out)], check=True)
        # release B re-encodes the same episodes, but labels 2 and 3 are swapped
        label = {1: 1, 2: 3, 3: 2, 4: 4}
        for ep, lab in label.items():
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i",
                            str(cls.tmp / "rel_a" / f"Show.S01E{ep:02d}.mkv"), "-c:v", "libx264",
                            "-crf", "30", str(cls.tmp / "rel_b" / f"Show.S01E{lab:02d}.mkv")],
                           check=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_swapped_labels_found_by_content(self):
        from episode_matcher import group_episodes
        from probe import probe_file
        from sequence import content_match
        files = [probe_file(p, use_cache=False)
                 for r in ("rel_a", "rel_b") for p in sorted((self.tmp / r).glob("*.mkv"))]
        groups = group_episodes(files)
        es.assign_releases(files, [self.tmp / "rel_a", self.tmp / "rel_b"])
        reports = es.analyze_groups(groups, matcher=lambda a, b: content_match(a, b))
        self.assertEqual(len(reports), 1)
        r = reports[0]
        shifted = {(f.other_key[1], f.mapped_key[1]) for f in r.findings if f.kind == "shifted"}
        self.assertEqual(shifted, {(2, 3), (3, 2)})
        self.assertEqual(set(r.blocked()), {(1, 2), (1, 3)})
        # untouched episodes are not flagged
        self.assertFalse(any(f.other_key in ((1, 1), (1, 4)) and f.kind != "ok"
                             for f in r.findings))


if __name__ == "__main__":
    unittest.main()
