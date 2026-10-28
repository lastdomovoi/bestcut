"""Click / crackle detection (issue #16)."""

import math
import random
import shutil
import struct
import sys
import tempfile
import unittest
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import audio_clicks as ac  # noqa: E402
from models import AudioTrack  # noqa: E402
from scoring import audio_quality_flags, score_audio_quality  # noqa: E402

SR = ac.SAMPLE_RATE
HAVE_FFMPEG = bool(shutil.which("ffmpeg"))


def music(seconds, seed=1):
    """Sustained chord with a slow envelope and a little noise."""
    rnd = random.Random(seed)
    n = int(seconds * SR)
    freqs = (220, 330, 495, 660, 1320)
    out = []
    for i in range(n):
        t = i / SR
        env = 0.6 + 0.4 * math.sin(2 * math.pi * 0.3 * t)
        v = sum(math.sin(2 * math.pi * f * t) / (k + 2) for k, f in enumerate(freqs))
        out.append(0.3 * env * v + rnd.gauss(0, 0.002))
    return out


def inject_clicks(x, count, seed=2, amp=(0.3, 0.7)):
    rnd = random.Random(seed)
    x = list(x)
    positions = sorted(rnd.sample(range(2000, len(x) - 2000, 97), count))
    for p in positions:
        x[p] += rnd.choice((-1, 1)) * rnd.uniform(*amp)
    return x, positions


def drum_hits(seconds, seed=3, per_sec=3):
    """Noise bursts with an exponential decay: loud transients, not defects."""
    rnd = random.Random(seed)
    n = int(seconds * SR)
    x = [rnd.gauss(0, 0.002) for _ in range(n)]
    for _ in range(int(seconds * per_sec)):
        start = rnd.randrange(0, n - SR // 4)
        for k in range(SR // 5):
            x[start + k] += rnd.gauss(0, 0.5) * math.exp(-k / (0.03 * SR))
    return x


def pitch_pulses(seconds, f0=120.0):
    """Speech-like: an impulse each pitch period exciting a decaying resonance."""
    n = int(seconds * SR)
    x = [0.0] * n
    period = SR / f0
    t = 0.0
    while t < n - 200:
        s = int(t)
        for k in range(160):
            x[s + k] += 0.4 * math.exp(-k / 40.0) * math.sin(2 * math.pi * 700 * k / SR)
        t += period
    return x


class FindClicksTests(unittest.TestCase):
    def test_clean_music_has_none(self):
        self.assertEqual(ac.count_clicks(music(10)), 0)

    def test_pure_tone_and_silence(self):
        tone = [0.5 * math.sin(2 * math.pi * 440 * i / SR) for i in range(SR * 5)]
        self.assertEqual(ac.count_clicks(tone), 0)
        self.assertEqual(ac.count_clicks([0.0] * (SR * 5)), 0)
        rnd = random.Random(0)
        self.assertEqual(ac.count_clicks([rnd.gauss(0, 1e-4) for _ in range(SR * 5)]), 0)

    def test_injected_clicks_are_found_at_their_positions(self):
        x, positions = inject_clicks(music(15), 25)
        found = ac.find_clicks(x)
        hits = sum(1 for p in positions if any(abs(p - f) <= 2 for f in found))
        self.assertGreaterEqual(hits, 23, (hits, len(found)))
        self.assertLessEqual(len(found), 27)           # no inventions

    def test_loud_transients_are_not_clicks(self):
        self.assertEqual(ac.count_clicks(drum_hits(15)), 0)

    def test_regular_pitch_pulses_are_not_clicks(self):
        self.assertEqual(ac.count_clicks(pitch_pulses(10)), 0)

    def test_white_noise_has_none(self):
        rnd = random.Random(5)
        self.assertEqual(ac.count_clicks([rnd.gauss(0, 0.2) for _ in range(SR * 10)]), 0)

    def test_dense_crackle_is_counted(self):
        x, _ = inject_clicks(music(15), 90, amp=(0.2, 0.5))      # 6 per second
        self.assertGreaterEqual(ac.count_clicks(x), 80)

    def test_clicks_on_top_of_drum_hits_still_found(self):
        x, positions = inject_clicks(drum_hits(15), 20, amp=(0.9, 1.2))
        found = ac.find_clicks(x)
        hits = sum(1 for p in positions if any(abs(p - f) <= 2 for f in found))
        self.assertGreaterEqual(hits, 10)

    def test_short_input(self):
        self.assertEqual(ac.find_clicks([0.1] * 100), [])


class ScoringTests(unittest.TestCase):
    def track(self, rate):
        return AudioTrack(codec="flac", channels=2, clicks_per_min=rate)

    def test_levels(self):
        self.assertEqual(score_audio_quality(self.track(0.0)), 0.0)
        self.assertEqual(score_audio_quality(self.track(3.0)), 0.0)
        mild = score_audio_quality(self.track(12.0))
        heavy = score_audio_quality(self.track(200.0))
        self.assertLess(mild, 0)
        self.assertLess(heavy, mild)
        self.assertGreaterEqual(heavy, -2.0)

    def test_labels(self):
        self.assertEqual(audio_quality_flags(self.track(2.0)), [])
        self.assertEqual(audio_quality_flags(self.track(12.0)), ["clicks 12/min"])
        self.assertEqual(audio_quality_flags(self.track(90.0)), ["crackle 90/min"])
        self.assertEqual(audio_quality_flags(AudioTrack()), [])

    def test_unanalyzed_track_is_not_penalized(self):
        self.assertEqual(score_audio_quality(AudioTrack(clicks_per_min=None)), 0.0)


def write_wav(path, samples):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(b"".join(struct.pack("<h", max(-32768, min(32767, int(v * 32767))))
                               for v in samples))


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg required")
class FileTests(unittest.TestCase):
    def test_file_level_rates(self):
        with tempfile.TemporaryDirectory() as d:
            clean, noisy = Path(d) / "clean.wav", Path(d) / "noisy.wav"
            base = music(40)
            write_wav(clean, base)
            x, _ = inject_clicks(base, 60, amp=(0.3, 0.6))
            write_wav(noisy, x)
            c = ac.detect_clicks(clean, 0, 40.0)
            n = ac.detect_clicks(noisy, 0, 40.0)
            self.assertEqual(c.clicks, 0)
            self.assertGreater(n.per_minute, 60)        # 60 clicks in 40 s: ~90/min
            self.assertIsNone(ac.detect_clicks(Path(d) / "missing.wav", 0, 40.0))


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg required")
class BandwidthTests(unittest.TestCase):
    def make(self, d, name, source, *af):
        import subprocess
        out = Path(d) / name
        cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", source]
        if af:
            cmd += ["-af", ",".join(af)]
        subprocess.run(cmd + ["-ar", "48000", "-c:a", "flac", str(out)], check=True)
        return out

    def test_estimates(self):
        from probe import analyze_audio_quality
        with tempfile.TemporaryDirectory() as d:
            full = self.make(d, "full.flac", "anoisesrc=d=40:r=48000:a=0.3")
            lossy = self.make(d, "lossy.flac", "anoisesrc=d=40:r=22050:a=0.3")
            r_full = analyze_audio_quality(full, 0, 40.0, 48000, check_bandwidth=True)
            r_lossy = analyze_audio_quality(lossy, 0, 40.0, 48000, check_bandwidth=True)
            self.assertEqual(r_full["bandwidth_hz"], 20000)
            self.assertLessEqual(r_lossy["bandwidth_hz"], 12000)    # true limit 11 kHz
            self.assertLess(r_lossy["hf_level_db"], r_full["hf_level_db"] - 30)

    def test_label_names_the_bandwidth(self):
        at = AudioTrack(codec="flac", is_lossless=True, bandwidth_hz=16000, sample_rate=48000)
        self.assertEqual(audio_quality_flags(at), ["band-limited ~16kHz"])
        self.assertLess(score_audio_quality(at), 0)
        at.sample_rate = 48000
        self.assertEqual(audio_quality_flags(at), ["band-limited ~16kHz"])
        at.bandwidth_hz = 18000                     # natural rolloff, content above 16 kHz
        self.assertEqual(audio_quality_flags(at), [])
        low = AudioTrack(codec="flac", is_lossless=True, bandwidth_hz=12000, sample_rate=32000)
        self.assertEqual(audio_quality_flags(low), [])   # Nyquist, not a defect
        full = AudioTrack(codec="flac", is_lossless=True, bandwidth_hz=20000)
        self.assertEqual(audio_quality_flags(full), [])
        lossy = AudioTrack(codec="ac3", is_lossless=False, bandwidth_hz=12000)
        self.assertEqual(audio_quality_flags(lossy), [])        # lossy codecs always cut

    def test_no_bandwidth_probe_for_low_sample_rate(self):
        from probe import analyze_audio_quality
        with tempfile.TemporaryDirectory() as d:
            low = Path(d) / "low.flac"
            import subprocess
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                            "anoisesrc=d=20:r=16000:a=0.3", "-c:a", "flac", str(low)], check=True)
            r = analyze_audio_quality(low, 0, 20.0, 16000, check_bandwidth=True)
            self.assertIsNone(r["bandwidth_hz"])        # nothing above 8 kHz to probe
            self.assertIsNotNone(r["clip_ratio"])


if __name__ == "__main__":
    unittest.main()
