"""Series-directory discovery (issue #4)."""

import json
import os
import subprocess
import sys
import tempfile
import unicodedata
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import discovery  # noqa: E402
from discovery import find_series, normalize_name  # noqa: E402


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        video = self.root / "video" / "tv-series"
        tor = self.root / "Torrents"
        for p in (video / "Интерны" / "Сезон 1", video / "Интерны" / "Сезон 2",
                  video / "Другой сериал" / "Сезон 1",
                  tor / "Интерны", tor / "Interny.XviD.[tahiy]" / "Season 3",
                  tor / "Unrelated Show"):
            p.mkdir(parents=True)
        (video / "Интерны" / "Сезон 1" / "s01e01.mkv").write_bytes(b"x")

    def tearDown(self):
        self._tmp.cleanup()

    def names(self, result):
        return sorted(str(p.relative_to(self.root)) for p in result.matches)

    def test_cyrillic_and_latin_alias_across_roots(self):
        r = find_series("Интерны", ["Interny"], [self.root / "video", self.root / "Torrents"])
        self.assertEqual(self.names(r), [
            "Torrents/Interny.XviD.[tahiy]", "Torrents/Интерны",
            "video/tv-series/Интерны"])
        self.assertTrue(r.searched_ok)

    def test_case_and_yo_insensitive(self):
        r = find_series("ИНТЕРНЫ", [], [self.root / "video"])
        self.assertEqual(len(r.matches), 1)
        self.assertEqual(normalize_name("Ёлки"), normalize_name("елки"))

    def test_nested_season_folders_do_not_duplicate(self):
        r = find_series("Интерны", [], [self.root / "video"])
        self.assertEqual(self.names(r), ["video/tv-series/Интерны"])

    def test_unicode_normalization_nfd_vs_nfc(self):
        (self.root / "video" / "Ёжик и Йога").mkdir()          # stored as NFC
        nfd = unicodedata.normalize("NFD", "Ёжик и Йога")
        self.assertNotEqual(nfd, "Ёжик и Йога")
        r = find_series(nfd, [], [self.root / "video"])
        self.assertEqual(len(r.matches), 1)
        # and the other way: directory stored as NFD, query typed as NFC
        (self.root / "video" / unicodedata.normalize("NFD", "Мёртвый сезон")).mkdir()
        r = find_series("Мёртвый сезон", [], [self.root / "video"])
        self.assertEqual(len(r.matches), 1)

    def test_no_match_is_empty_but_ok(self):
        r = find_series("Нет такого", ["Nothing"], [self.root / "video"])
        self.assertEqual(r.matches, [])
        self.assertTrue(r.searched_ok)

    def test_missing_root_is_distinct_from_no_match(self):
        r = find_series("Интерны", [], [self.root / "nope", self.root / "video"])
        self.assertEqual(r.missing_roots, [self.root / "nope"])
        self.assertFalse(r.searched_ok)
        self.assertEqual(len(r.matches), 1)

    def test_unreadable_directory_is_reported(self):
        real = os.scandir

        def fake(path):
            if str(path).endswith("Другой сериал"):
                raise PermissionError(13, "Permission denied")
            return real(path)
        with mock.patch("discovery.os.scandir", side_effect=fake):
            r = find_series("Интерны", [], [self.root / "video"])
        self.assertTrue(r.unreadable)
        self.assertIn("Permission denied", r.unreadable[0][1])
        self.assertEqual(len(r.matches), 1)

    def test_root_that_is_a_file(self):
        f = self.root / "file.txt"
        f.write_text("x")
        r = find_series("x", [], [f])
        self.assertTrue(r.unreadable)

    def test_word_boundary_not_substring(self):
        (self.root / "video" / "Reinterny things").mkdir()
        r = find_series("Interny", [], [self.root / "video"])
        self.assertEqual(r.matches, [])

    def test_whole_words_only(self):
        (self.root / "video" / "Domashniy arrest").mkdir()
        (self.root / "video" / "Friendship").mkdir()
        self.assertEqual(find_series("Dom", [], [self.root / "video"]).matches, [])
        self.assertEqual(find_series("Friends", [], [self.root / "video"]).matches, [])
        self.assertEqual(len(find_series("Friendship", [], [self.root / "video"]).matches), 1)

    def test_symlinked_series_directory_is_found_once(self):
        target = self.root / "elsewhere" / "Интерны"
        target.mkdir(parents=True)
        link = self.root / "video" / "linked"
        link.mkdir()
        os.symlink(target, link / "Интерны")
        r = find_series("Интерны", [], [self.root / "video"])
        self.assertTrue(any(p.name == "Интерны" and p.parent.name == "linked" for p in r.matches))

    def test_symlink_loop_terminates(self):
        loop = self.root / "video" / "loop"
        loop.mkdir()
        os.symlink(self.root / "video", loop / "back")
        r = find_series("nothing here", [], [self.root / "video"])
        self.assertEqual(r.matches, [])

    def test_zero_depth_scans_nothing(self):
        self.assertEqual(find_series("Интерны", [], [self.root / "video"], max_depth=0).matches, [])

    def test_never_runs_a_subprocess(self):
        with mock.patch.object(subprocess, "run", side_effect=AssertionError("ffprobe!")), \
                mock.patch.object(subprocess, "Popen", side_effect=AssertionError("popen!")):
            find_series("Интерны", ["Interny"], [self.root / "video", self.root / "Torrents"])

    def test_does_not_descend_into_matches_or_beyond_depth(self):
        deep = self.root / "a" / "b" / "c" / "d" / "e" / "Интерны"
        deep.mkdir(parents=True)
        r = find_series("Интерны", [], [self.root / "a"], max_depth=3)
        self.assertEqual(r.matches, [])
        r = find_series("Интерны", [], [self.root / "a"], max_depth=6)
        self.assertEqual(len(r.matches), 1)

    def test_json_shape(self):
        r = find_series("Интерны", [], [self.root / "video", self.root / "gone"])
        data = r.to_json()
        self.assertEqual(set(data), {"matches", "missing_roots", "unreadable"})
        json.dumps(data, ensure_ascii=False)


class FindSeriesCliTests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(ROOT / "cli.py"), *args],
                              capture_output=True, text=True, timeout=60)

    def test_cli_found_notfound_missing(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "Интерны" / "Сезон 1").mkdir(parents=True)
            ok = self.run_cli("--find-series", "Интерны", d)
            self.assertEqual(ok.returncode, 0, ok.stderr)
            self.assertEqual(ok.stdout.strip(), str(Path(d) / "Интерны"))
            none = self.run_cli("--find-series", "Nothing", d)
            self.assertEqual(none.returncode, 1)
            missing = self.run_cli("--find-series", "Интерны", d + "/zzz")
            self.assertEqual(missing.returncode, 2)
            self.assertIn("MISSING ROOT", missing.stderr)
            js = self.run_cli("--find-series", "Интерны", "--alias", "Interny", "--json", d)
            data = json.loads(js.stdout)
            self.assertEqual(data["matches"], [str(Path(d) / "Интерны")])

    def test_cli_requires_a_root(self):
        self.assertEqual(self.run_cli("--find-series", "x").returncode, 2)


if __name__ == "__main__":
    unittest.main()
