"""Synchronized video comparison (issues #8, #10, #11, #14).

Run: python3 -m unittest discover -s tests -t .
"""

import os
import random
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import cli  # noqa: E402
import sequence as sq  # noqa: E402
from models import IntegrityResult, VideoFile  # noqa: E402
from probe import detect_crop_and_upscale, probe_file  # noqa: E402

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


# --------------------------------------------------------------------------
# Pure alignment tests on synthetic thumbnails (no ffmpeg)
# --------------------------------------------------------------------------

def frame(seed: int) -> bytes:
    """A distinctive thumbnail per seed (a blob plus fixed per-seed texture)."""
    rnd = random.Random(seed)
    cx, cy = rnd.uniform(4, 20), rnd.uniform(3, 11)
    amp = rnd.uniform(60, 120)
    base = rnd.uniform(40, 120)
    out = bytearray()
    for y in range(sq.THUMB_H):
        for x in range(sq.THUMB_W):
            v = base + amp * max(0.0, 1 - ((x - cx) ** 2 + (y - cy) ** 2) / 60)
            v = int(max(0, min(255, v + rnd.uniform(-30, 30))))
            out += bytes((v, v, v))
    return bytes(out)


def seq_of(seeds):
    return sq.ThumbSeq([frame(s) for s in seeds])


class AlignTests(unittest.TestCase):
    def test_identical(self):
        a = seq_of(range(60))
        al = sq.align(a, seq_of(range(60)))
        self.assertEqual(al.coverage, 1.0)
        self.assertEqual(sq.find_divergences(al, 60), [])

    def test_start_offset(self):
        a, b = seq_of(range(60)), seq_of(range(3, 60))
        al = sq.align(a, b)
        divs = sq.find_divergences(al, len(b))
        self.assertEqual({o for o in al.offsets if o is not None}, {-3})
        self.assertEqual([d.kind for d in divs], ["extra_in_a"])

    def test_inserted_segment_shifts_timeline(self):
        a = seq_of(range(80))
        b = seq_of(list(range(30)) + list(range(1000, 1012)) + list(range(30, 80)))
        al = sq.align(a, b)
        divs = sq.find_divergences(al, len(b))
        kinds = [(d.kind, d.a_start) for d in divs]
        self.assertEqual(kinds, [("extra_in_b", 30)])
        self.assertIn("12s", divs[0].note)

    def test_removed_segment(self):
        a = seq_of(range(80))
        b = seq_of(list(range(30)) + list(range(42, 80)))
        divs = sq.find_divergences(sq.align(a, b), 68)
        self.assertEqual([(d.kind, d.a_start) for d in divs], [("extra_in_a", 30)])

    def test_replaced_block_same_length(self):
        a = seq_of(range(80))
        b = seq_of(list(range(30)) + list(range(2000, 2012)) + list(range(42, 80)))
        divs = sq.find_divergences(sq.align(a, b), 80)
        self.assertEqual([(d.kind, d.a_start, d.a_end) for d in divs],
                         [("replaced_block", 30, 42)])

    def test_short_replacement_is_glitch(self):
        a = seq_of(range(80))
        b = seq_of(list(range(30)) + [3000, 3001] + list(range(32, 80)))
        divs = sq.find_divergences(sq.align(a, b), 80)
        self.assertEqual([d.kind for d in divs], ["glitch"])

    def test_unrelated_content(self):
        a, b = seq_of(range(60)), seq_of(range(5000, 5060))
        al = sq.align(a, b)
        self.assertTrue(al is None or al.coverage < 0.3)

    def test_flat_scenes_are_uninformative(self):
        flat = bytes([20, 20, 20] * sq.THUMB_PIXELS)
        a = sq.ThumbSeq([frame(i) for i in range(30)] + [flat] * 10 + [frame(i) for i in range(40, 70)])
        b = sq.ThumbSeq([frame(i) for i in range(30)] + [flat] * 10 + [frame(i) for i in range(40, 70)])
        al = sq.align(a, b)
        self.assertEqual(al.coverage, 1.0)
        self.assertEqual(sq.find_divergences(al, 70), [])

    def test_persistent_overlay_and_owner(self):
        def with_logo(seed):
            f = bytearray(frame(seed))
            for y in range(1, 3):
                for x in range(19, 23):
                    i = (y * sq.THUMB_W + x) * 3
                    f[i:i + 3] = bytes((255, 255, 255))
            return bytes(f)
        a = sq.ThumbSeq([frame(i) for i in range(60)])
        b = sq.ThumbSeq([with_logo(i) for i in range(60)])
        al = sq.align(a, b)
        owner, note = sq.detect_overlay(a, b, al, Path("a.mkv"), Path("b.mkv"))
        self.assertEqual(owner, Path("b.mkv"))
        self.assertIn("top-right", note)

    def test_no_overlay_in_identical_pictures(self):
        a = seq_of(range(60))
        al = sq.align(a, seq_of(range(60)))
        self.assertEqual(sq.detect_overlay(a, seq_of(range(60)), al, Path("a"), Path("b")),
                         (None, ""))

    def test_color_cast_is_stable_and_material(self):
        def tinted(seed, dr):
            f = bytearray(frame(seed))
            for i in range(0, len(f), 3):
                f[i] = min(255, f[i] + dr)
            return bytes(f)
        a = sq.ThumbSeq([frame(i) for i in range(40)])
        b = sq.ThumbSeq([tinted(i, 25) for i in range(40)])
        al = sq.align(a, b)
        va, vb = VideoFile(path=Path("a")), VideoFile(path=Path("b"))
        cd = sq.compare_color(a, b, al, va, vb)
        self.assertTrue(cd.stable and cd.material, cd.describe())

    def test_identical_grade_is_not_material(self):
        a = seq_of(range(40))
        al = sq.align(a, seq_of(range(40)))
        cd = sq.compare_color(a, seq_of(range(40)), al, VideoFile(path=Path("a")),
                              VideoFile(path=Path("b")))
        self.assertFalse(cd.material)

    def test_color_metadata_issues(self):
        va, vb = VideoFile(path=Path("a.mkv")), VideoFile(path=Path("b.mkv"))
        vb.video.color_range = "tv"
        vb.video.color_space = "bt709"
        va.video.color_space = "bt470bg"
        issues = sq.color_metadata_issues(va, vb)
        self.assertTrue(any("range missing in a.mkv" in i for i in issues), issues)
        self.assertTrue(any("matrix differs" in i for i in issues), issues)
        self.assertEqual(sq.color_metadata_issues(vb, vb), [])


# --------------------------------------------------------------------------
# Deletion policy with a stubbed comparer
# --------------------------------------------------------------------------

def vf_of(name, parent, size=1000, score=10.0):
    vf = VideoFile(path=Path(parent) / name, file_size=size, duration=1300.0, score=score)
    vf.integrity = IntegrityResult(checked=True, full_checked=True)
    return vf


def fake_comp(a, b, relation="same_cut_reencode", review=False, **kw):
    c = sq.SequenceComparison(a.path, b.path, relation=relation, confidence="high")
    c.review_required = review
    c.review_reasons = ["because"] if review else []
    for k, v in kw.items():
        setattr(c, k, v)
    return c


class DedupePolicyTests(unittest.TestCase):
    def groups(self, *files):
        return {"s": {(1, 1): list(files)}}

    def test_proven_same_video_is_deletable(self):
        a, b = vf_of("S01E01.a.mkv", "/a", score=9), vf_of("S01E01.b.mkv", "/b", score=5)
        keep, delete, review = cli.deletion_candidates(
            self.groups(a, b), comparer=lambda x, y: fake_comp(x, y))
        self.assertEqual((keep, delete, review), ([a], [b], []))
        self.assertIn("same cut reencode", b.content_note)

    def test_unrelated_content_never_deleted(self):
        a, b = vf_of("S01E01.a.mkv", "/a", score=9), vf_of("S01E01.b.mkv", "/b", score=5)
        _, delete, review = cli.deletion_candidates(
            self.groups(a, b),
            comparer=lambda x, y: fake_comp(x, y, "different_content", True))
        self.assertEqual(delete, [])
        self.assertTrue(review)

    def test_different_cut_needs_review(self):
        a, b = vf_of("S01E01.a.mkv", "/a", score=9), vf_of("S01E01.b.mkv", "/b", score=5)
        _, delete, review = cli.deletion_candidates(
            self.groups(a, b), comparer=lambda x, y: fake_comp(x, y, "different_cut", True))
        self.assertEqual(delete, [])

    def test_comparison_failure_needs_review(self):
        a, b = vf_of("S01E01.a.mkv", "/a", score=9), vf_of("S01E01.b.mkv", "/b", score=5)

        def broken(x, y):
            c = sq.SequenceComparison(x.path, y.path)  # default: review required
            c.review_reasons = ["content comparison failed"]
            return c
        _, delete, review = cli.deletion_candidates(self.groups(a, b), comparer=broken)
        self.assertEqual(delete, [])
        self.assertIn("failed", review[0][0])

    def test_redundant_larger_copy_is_deleted_not_the_compact_one(self):
        big = vf_of("S01E01.big.mkv", "/a", size=2000, score=30)
        small = vf_of("S01E01.small.mkv", "/b", size=900, score=20)

        def comp(x, y):
            c = fake_comp(x, y)
            c.redundancy = sq.Redundancy(verdict="redundant_larger",
                                         redundant_path=big.path, size_ratio=2.2)
            return c
        keep, delete, _ = cli.deletion_candidates(self.groups(big, small), comparer=comp)
        self.assertEqual((keep, delete), ([small], [big]))

    def test_better_larger_is_kept(self):
        big = vf_of("S01E01.big.mkv", "/a", size=2000, score=30)
        small = vf_of("S01E01.small.mkv", "/b", size=900, score=20)

        def comp(x, y):
            c = fake_comp(x, y)
            c.redundancy = sq.Redundancy(verdict="better_larger", size_ratio=2.2)
            return c
        keep, delete, _ = cli.deletion_candidates(self.groups(big, small), comparer=comp)
        self.assertEqual((keep, delete), ([big], [small]))

    def test_overlay_on_keeper_blocks_deletion_of_clean_copy(self):
        logo = vf_of("S01E01.logo.mkv", "/a", score=30)
        clean = vf_of("S01E01.clean.mkv", "/b", score=20)
        comp = lambda x, y: fake_comp(x, y, review=True, overlay_owner=logo.path,
                                      overlay_note="overlay top-right")
        _, delete, review = cli.deletion_candidates(self.groups(logo, clean), comparer=comp)
        self.assertEqual(delete, [])
        self.assertTrue(review)

    def test_overlay_penalty_demotes_the_carrier(self):
        logo = vf_of("S01E01.logo.mkv", "/a", score=30)
        clean = vf_of("S01E01.clean.mkv", "/b", score=28)
        comp = lambda x, y: fake_comp(x, y, review=True, overlay_owner=logo.path,
                                      overlay_note="overlay")
        cli.deletion_candidates(self.groups(logo, clean), comparer=comp)
        self.assertEqual(logo.content_penalty, cli.OVERLAY_PENALTY)
        self.assertLess(logo.score, clean.score)

    def test_clean_copy_wins_and_overlay_carrier_is_deleted(self):
        logo = vf_of("S01E01.logo.mkv", "/a", score=30)
        clean = vf_of("S01E01.clean.mkv", "/b", score=28)

        def comp(x, y):
            c = fake_comp(x, y, review=True, overlay_owner=logo.path,
                          overlay_note="overlay top-right")
            c.review_reasons = ["overlay top-right"]
            return c
        keep, delete, _ = cli.deletion_candidates(self.groups(logo, clean), comparer=comp)
        self.assertEqual((keep, delete), ([clean], [logo]))
        self.assertIn("overlay", logo.content_note)

    def test_candidate_with_unique_tracks_is_not_deleted(self):
        a, b = vf_of("S01E01.a.mkv", "/a", score=9), vf_of("S01E01.b.mkv", "/b", score=5)

        def comp(x, y):
            c = fake_comp(x, y)
            c.redundancy = sq.Redundancy(unique_tracks=["B has 1 more audio track(s) [fra]"])
            return c
        _, delete, review = cli.deletion_candidates(self.groups(a, b), comparer=comp)
        self.assertEqual(delete, [])
        self.assertIn("lose", review[0][0])

    def test_keeper_with_extra_tracks_may_still_replace_the_other(self):
        a, b = vf_of("S01E01.a.mkv", "/a", score=9), vf_of("S01E01.b.mkv", "/b", score=5)

        def comp(x, y):
            c = fake_comp(x, y)
            c.redundancy = sq.Redundancy(unique_tracks=["A has 1 more audio track(s) [fra]"])
            return c
        keep, delete, _ = cli.deletion_candidates(self.groups(a, b), comparer=comp)
        self.assertEqual((keep, delete), ([a], [b]))

    def test_swap_never_drops_tracks_of_the_old_keeper(self):
        big = vf_of("S01E01.big.mkv", "/a", size=2000, score=30)
        small = vf_of("S01E01.small.mkv", "/b", size=900, score=20)

        def comp(x, y):
            c = fake_comp(x, y)
            c.redundancy = sq.Redundancy(verdict="redundant_larger", redundant_path=big.path,
                                         unique_tracks=["A has 1 more audio track(s) [fra]"])
            return c
        keep, delete, _ = cli.deletion_candidates(self.groups(big, small), comparer=comp)
        self.assertEqual(keep, [big])   # not swapped: big has a dub small lacks

    def test_failed_decode_is_not_used_or_cached(self):
        from unittest import mock
        from procutil import BytesResult
        vf = VideoFile(path=Path(__file__), duration=100.0)
        partial = BytesResult(1, bytes(sq.THUMB_BYTES * 40), "boom")
        with mock.patch("sequence.run_tool_bytes", return_value=partial):
            self.assertIsNone(sq.extract_thumbs(vf, use_cache=False))
        cut_short = BytesResult(0, bytes(sq.THUMB_BYTES * 40), "")
        with mock.patch("sequence.run_tool_bytes", return_value=cut_short):
            self.assertIsNone(sq.extract_thumbs(vf, use_cache=False))

    def test_higher_resolution_larger_file_is_not_redundant(self):
        big, small = vf_of("a.mkv", "/a", size=4000), vf_of("b.mkv", "/b", size=1000)
        big.video.width, big.video.height = 3840, 2160
        small.video.width, small.video.height = 1920, 1080
        comp = fake_comp(big, small)
        stats = sq.CheckpointStats(hf_a=[5.0], hf_b=[5.0])   # equal at the scaled size
        red = sq.assess_redundancy(big, small, comp, stats, [])
        self.assertEqual(red.verdict, "better_larger")

    def test_color_ambiguity_needs_review(self):
        a, b = vf_of("S01E01.a.mkv", "/a", score=9), vf_of("S01E01.b.mkv", "/b", score=5)
        comp = lambda x, y: fake_comp(x, y, review=True)
        _, delete, _ = cli.deletion_candidates(self.groups(a, b), comparer=comp)
        self.assertEqual(delete, [])


# --------------------------------------------------------------------------
# End-to-end on real encodes
# --------------------------------------------------------------------------

def _ff(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg required")
class EndToEndTests(unittest.TestCase):
    DUR = 40

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls._old_cache = os.environ.get("XDG_CACHE_HOME")
        os.environ["XDG_CACHE_HOME"] = str(cls.tmp / "cache")
        import cache
        cache.CACHE_DIR = Path(os.environ["XDG_CACHE_HOME"]) / "bestcut"
        t = cls.tmp
        src = f"mandelbrot=s=320x180:r=24:end_scale=0.01,gblur=sigma=2"
        _ff("-f", "lavfi", "-i", src, "-f", "lavfi", "-i", "sine=f=440:r=48000",
            "-t", str(cls.DUR), "-c:v", "libx264", "-crf", "20", "-c:a", "aac", str(t / "base.mkv"))
        _ff("-f", "lavfi", "-i", "mandelbrot=s=320x180:r=24:start_scale=3:end_scale=2,gblur=sigma=2",
            "-t", "10", "-c:v", "libx264", "-crf", "20", str(t / "ad.mkv"))
        _ff("-f", "lavfi", "-i", "mandelbrot=s=320x180:r=24:start_scale=1:end_scale=0.3,gblur=sigma=2",
            "-t", str(cls.DUR), "-c:v", "libx264", "-crf", "20", str(t / "other.mkv"))
        b = str(t / "base.mkv")
        _ff("-i", b, "-c:v", "libx264", "-crf", "30", "-c:a", "copy", str(t / "reenc.mkv"))
        _ff("-i", b, "-c:v", "libx264", "-crf", "12", "-preset", "slow", "-c:a", "copy", str(t / "big.mkv"))
        _ff("-i", b, "-c", "copy", str(t / "remux.mp4"))
        shutil.copy(b, t / "copy.mkv")
        _ff("-i", b, "-ss", "3", "-c:v", "libx264", "-crf", "20", "-c:a", "copy", str(t / "trimmed.mkv"))
        cat = ("[0:v]trim=0:{a},setpts=PTS-STARTPTS[x];[1:v]trim=0:{n},setpts=PTS-STARTPTS[y];"
               "[0:v]trim={b}:{d},setpts=PTS-STARTPTS[z];[x][y][z]concat=n=3:v=1:a=0")
        _ff("-i", b, "-i", str(t / "ad.mkv"), "-filter_complex",
            cat.format(a=15, n=8, b=15, d=cls.DUR), "-c:v", "libx264", "-crf", "20", str(t / "inserted.mkv"))
        _ff("-i", b, "-i", str(t / "ad.mkv"), "-filter_complex",
            cat.format(a=15, n=8, b=23, d=cls.DUR), "-c:v", "libx264", "-crf", "20", str(t / "replaced.mkv"))
        _ff("-i", b, "-i", str(t / "ad.mkv"), "-filter_complex",
            cat.format(a=20, n=2, b=22, d=cls.DUR), "-c:v", "libx264", "-crf", "20", str(t / "glitch.mkv"))
        _ff("-i", b, "-vf", "eq=contrast=1.25:brightness=0.02,colorbalance=rs=0.2:bs=-0.2",
            "-c:v", "libx264", "-crf", "20", "-c:a", "copy", str(t / "graded.mkv"))
        _ff("-i", b, "-vf", "drawbox=x=250:y=10:w=60:h=25:color=white@1:t=fill",
            "-c:v", "libx264", "-crf", "20", "-c:a", "copy", str(t / "logo.mkv"))
        _ff("-i", b, "-vf", "scale=240:136,pad=320:180:40:22", "-c:v", "libx264", "-crf", "20",
            "-c:a", "copy", str(t / "pillar.mkv"))
        _ff("-i", b, "-f", "lavfi", "-i", "sine=f=660:r=48000", "-map", "0", "-map", "1:a",
            "-c", "copy", "-metadata:s:a:1", "language=fra", "-t", str(cls.DUR), str(t / "twoaudio.mkv"))
        cls.base = cls.probed("base.mkv")

    @classmethod
    def tearDownClass(cls):
        if cls._old_cache is None:
            os.environ.pop("XDG_CACHE_HOME", None)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @classmethod
    def probed(cls, name):
        p = cls.tmp / name
        vf = probe_file(p, use_cache=False)
        detect_crop_and_upscale(p, vf)
        return vf

    def cmp(self, name):
        return sq.compare_files(self.base, self.probed(name))

    def test_byte_identical(self):
        c = self.cmp("copy.mkv")
        self.assertEqual(c.relation, "byte_identical")
        self.assertTrue(c.safe_to_dedupe)

    def test_remux_is_payload_identical(self):
        c = self.cmp("remux.mp4")
        self.assertEqual(c.relation, "payload_identical")
        self.assertTrue(c.safe_to_dedupe)

    def test_reencode_same_cut(self):
        c = self.cmp("reenc.mkv")
        self.assertEqual(c.relation, "same_cut_reencode")
        self.assertGreater(c.ssim_mean, 0.85)
        self.assertTrue(c.safe_to_dedupe, c.review_reasons)

    def test_transparent_larger_reencode_is_redundant(self):
        c = self.cmp("big.mkv")
        self.assertEqual(c.relation, "same_cut_reencode")
        self.assertEqual(c.redundancy.verdict, "redundant_larger")
        self.assertEqual(c.redundancy.redundant_path, self.tmp / "big.mkv")

    def test_genuinely_better_larger_encode(self):
        c = self.cmp("reenc.mkv")   # base (crf 20) is larger and finer than crf 30
        self.assertEqual(c.redundancy.verdict, "better_larger")

    def test_geometry_only_change_still_same_cut(self):
        c = self.cmp("pillar.mkv")
        self.assertEqual(c.relation, "same_cut_reencode")

    def test_start_offset_is_a_different_cut(self):
        c = self.cmp("trimmed.mkv")
        self.assertEqual(c.relation, "different_cut")
        self.assertFalse(c.safe_to_dedupe)
        self.assertTrue(any(d.kind == "extra_in_a" for d in c.divergences))

    def test_inserted_segment(self):
        c = self.cmp("inserted.mkv")
        self.assertEqual(c.relation, "different_cut")
        shifts = [d for d in c.divergences if d.kind == "extra_in_b"]
        self.assertEqual(len(shifts), 1)
        self.assertAlmostEqual(shifts[0].a_start, 15, delta=2)
        self.assertFalse(c.safe_to_dedupe)

    def test_replaced_block(self):
        c = self.cmp("replaced.mkv")
        blocks = [d for d in c.divergences if d.kind == "replaced_block"]
        self.assertEqual(len(blocks), 1)
        self.assertAlmostEqual(blocks[0].a_start, 15, delta=2)
        self.assertAlmostEqual(blocks[0].a_end, 23, delta=2)
        self.assertFalse(c.safe_to_dedupe)

    def test_two_second_glitch_needs_review(self):
        c = self.cmp("glitch.mkv")
        self.assertTrue(any(d.kind == "glitch" for d in c.divergences))
        self.assertFalse(c.safe_to_dedupe)

    def test_same_title_different_content_is_not_a_duplicate(self):
        c = self.cmp("other.mkv")
        self.assertEqual(c.relation, "different_content")
        self.assertFalse(c.safe_to_dedupe)

    def test_color_grade_is_flagged(self):
        c = self.cmp("graded.mkv")
        self.assertTrue(c.color.material, c.color.describe())
        self.assertFalse(c.safe_to_dedupe)

    def test_logo_overlay_identified_and_owner_found(self):
        c = self.cmp("logo.mkv")
        self.assertEqual(c.overlay_owner, self.tmp / "logo.mkv")
        self.assertFalse(c.safe_to_dedupe)

    def test_extra_audio_language_is_unique_content(self):
        c = self.cmp("twoaudio.mkv")
        self.assertTrue(any("B has 1 more audio" in u for u in c.redundancy.unique_tracks), c.redundancy)
        self.assertNotEqual(c.redundancy.verdict, "redundant_larger")

    def test_report_renders(self):
        from comparator import format_sequence_report
        text = format_sequence_report(self.cmp("inserted.mkv"))
        self.assertIn("extra in b", text)
        self.assertIn("review required", text)

    def test_cli_policy_end_to_end(self):
        files = [self.probed("base.mkv"), self.probed("big.mkv")]
        for f in files:
            f.integrity = IntegrityResult(checked=True, full_checked=True)
            f.score = 20.0 if "big" in f.path.name else 10.0
        keep, delete, review = cli.deletion_candidates(
            {"m": {(1, 1): files}}, comparer=cli.make_comparer())
        self.assertEqual([k.path.name for k in keep], ["base.mkv"])
        self.assertEqual([d.path.name for d in delete], ["big.mkv"])


if __name__ == "__main__":
    unittest.main()
