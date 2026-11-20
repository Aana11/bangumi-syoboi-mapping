import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from sources import BangumiTitle, SyoboiTitle, bangumi_month, merge_syoboi, parse_syoboi
from update import END, START, guard_previous, match_titles, statistics, update_lock, write_snapshot

STAMP = "2026-11-11 12:00:00"


def bgm(subject_id, title, month="2026-10"):
    return BangumiTitle(subject_id, title, month)


def syo(tid, title, month=10, category=10):
    return SyoboiTitle(tid, title, STAMP, 2026, month, category)


def match(syoboi, bangumi, manual=(), blocked=()):
    return match_titles(
        {item.id: item for item in syoboi},
        {item.id: item for item in bangumi},
        {"mappings": list(manual), "blocked_pairs": list(blocked)},
    )


class MappingTests(unittest.TestCase):
    def test_exact_before_normalized_and_significant_punctuation(self):
        methods, unmatched = match(
            [syo(1, "Title"), syo(2, "ＳＥＣＯＮＤ・ＳＥＡＳＯＮ"), syo(3, "Series!!"), syo(4, "ＴＩＴＬＥ")],
            [bgm(10, "Title"), bgm(20, "Second Season"), bgm(30, "Series!")],
        )
        self.assertEqual(methods, {(1, 10): "exact", (2, 20): "normalized"})
        self.assertEqual([row["reason"] for row in unmatched], ["no_title_match", "ambiguous_or_reserved"])

    def test_duplicates_in_either_source_are_ambiguous(self):
        methods, unmatched = match(
            [syo(1, "Duplicate"), syo(2, "Duplicate"), syo(3, "Other")],
            [bgm(10, "Duplicate"), bgm(20, "Other"), bgm(30, "Other")],
        )
        self.assertFalse(methods)
        self.assertEqual([row["reason"] for row in unmatched], ["ambiguous_or_reserved"] * 3)
        self.assertEqual(unmatched[2]["candidate_bgm_ids"], [20, 30])

    def test_mismatched_and_missing_dates(self):
        methods, unmatched = match(
            [syo(1, "One", month=1), syo(2, "Two", month=0), syo(3, "Three")],
            [bgm(10, "One"), bgm(20, "Two"), bgm(30, "Three", month=None)],
        )
        self.assertFalse(methods)
        self.assertEqual(
            [row["reason"] for row in unmatched],
            ["premiere_month_mismatch_or_missing", "missing_premiere_month", "premiere_month_mismatch_or_missing"],
        )

    def test_movie_review_and_manual_split(self):
        choices = [
            {"syoboi_tid": 1, "bgm_id": bgm_id, "evidence": "verified", "syoboi_title": "Film"}
            for bgm_id in (10, 20)
        ]
        syoboi = [syo(1, "Film", category=8), syo(2, "Other film", category=8), syo(3, "Radio", category=2)]
        bangumi = [bgm(10, "Film"), bgm(20, "Other part"), bgm(30, "Other film"), bgm(40, "Radio")]
        methods, unmatched = match(syoboi, bangumi, manual=choices)
        self.assertEqual(methods, {(1, 10): "manual", (1, 20): "manual"})
        self.assertEqual([(row["syoboi_tid"], row["reason"]) for row in unmatched], [(2, "movie_review")])
        with self.assertRaisesRegex(ValueError, "upstream title changed"):
            match([syo(1, "Reused title", category=8)], bangumi, manual=choices)

    def test_blocked_and_manual_reservation(self):
        manual = {"syoboi_tid": 1, "bgm_id": 20, "evidence": "corrected", "syoboi_title": "One"}
        blocked = {"syoboi_tid": 3, "bgm_id": 30, "evidence": "different work"}
        methods, unmatched = match(
            [syo(1, "One"), syo(2, "Two"), syo(3, "Three")],
            [bgm(10, "One"), bgm(20, "Two"), bgm(30, "Three")],
            manual=[manual], blocked=[blocked],
        )
        self.assertEqual(methods, {(1, 20): "manual"})
        self.assertEqual([row["reason"] for row in unmatched], ["ambiguous_or_reserved", "blocked"])

    def test_stats_count_distinct_tids_and_only_quarter_premieres(self):
        syoboi = {1: syo(1, "This quarter"), 2: syo(2, "Older", month=9), 3: syo(3, "Film", category=8)}
        bangumi = {10: bgm(10, "This quarter"), 20: bgm(20, "Second part")}
        methods = {(1, 10): "manual", (1, 20): "manual"}
        source = {"created_at": "2026-11-11T08:00:00Z"}
        stats = statistics(syoboi, bangumi, methods, [], source, STAMP, date(2026, 11, 20))
        self.assertEqual(stats["syoboi_anime"], {"total": 2, "matched": 1, "unmatched": 1})
        self.assertEqual(stats["current_quarter"], {"year": 2026, "quarter": 4, "total": 1, "matched": 1, "unmatched": 0})
        self.assertEqual(stats["mapped_pairs"], 2)
        self.assertEqual(stats["syoboi_mixed_movies"]["total"], 1)
        with self.assertRaisesRegex(ValueError, "Matched Syoboi anime"):
            guard_previous(stats, {**stats, "syoboi_anime": {"total": 2, "matched": 0}})

    def test_snapshot_is_deterministic_and_marker_failure_writes_nothing(self):
        stats = statistics({1: syo(1, "A title")}, {10: bgm(10, "A title")}, {(1, 10): "exact"}, [],
                           {"created_at": "2026-11-11T08:00:00Z"}, STAMP, date(2026, 11, 20))
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / "data").mkdir()
            readme = root / "README.md"
            readme.write_text(f"Heading\n{START}\n{END}\nFooter\n", encoding="utf-8")
            documents = {"mapping": [{"bgm_id": 10, "syoboi_tid": 1}], "stats": stats}
            write_snapshot(root, documents, stats)
            original = readme.read_bytes()
            self.assertIn(b"100.00%", original)
            write_snapshot(root, documents, stats)
            self.assertEqual(readme.read_bytes(), original)
            readme.write_text("No markers", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "statistics markers"):
                write_snapshot(root, {"mapping": []}, stats)
            self.assertEqual(json.loads((root / "data/mapping.json").read_bytes()), documents["mapping"])

    def test_failed_replace_restores_already_replaced_files(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / "data").mkdir()
            readme = root / "README.md"
            readme.write_text(f"{START}\n{END}\n", encoding="utf-8")
            mapping = root / "data/mapping.json"
            mapping.write_bytes(b"[1]\n")
            replace = Path.replace

            def fail_readme(path, target):
                if target == readme:
                    raise PermissionError("README is locked")
                return replace(path, target)

            with patch("update.readme_table", return_value="New stats"):
                with patch.object(Path, "replace", fail_readme):
                    with self.assertRaisesRegex(PermissionError, "README is locked"):
                        write_snapshot(root, {"mapping": [2]}, {})
            self.assertEqual(mapping.read_bytes(), b"[1]\n")

    def test_update_lock_rejects_a_second_writer_and_can_be_reacquired(self):
        with tempfile.TemporaryDirectory() as name:
            with update_lock(Path(name)):
                with self.assertRaisesRegex(ValueError, "local update lock"):
                    with update_lock(Path(name)):
                        self.fail("Second writer acquired the lock")
            with update_lock(Path(name)):
                pass


class SourceTests(unittest.TestCase):
    def test_bangumi_month_supports_partial_archive_dates(self):
        for value in ("2026-10", "2026-10-01", "2026-10-00"):
            self.assertEqual(bangumi_month(value), "2026-10")
        for value in ("2026", "0000-01-01", "2026-00", "2026-20", None):
            self.assertIsNone(bangumi_month(value))

    def test_syoboi_checks_inner_result_and_accepts_empty_dates(self):
        xml = f"""<TitleLookupResponse><Result><Code>200</Code></Result><TitleItems>
        <TitleItem id="1"><TID>1</TID><Title>Title</Title><LastUpdate>{STAMP}</LastUpdate>
        <FirstYear/><FirstMonth/><Cat>8</Cat></TitleItem></TitleItems></TitleLookupResponse>"""
        result = parse_syoboi(xml, with_details=True)
        self.assertIsNone(result[1].premiere)
        self.assertEqual(result[1].category, 8)
        with self.assertRaisesRegex(ValueError, "403"):
            parse_syoboi(xml.replace("200", "403"), with_details=True)

    @patch("sources.MIN_SYOBOI_TITLES", 1)
    def test_syoboi_refresh_reuses_unchanged_details(self):
        previous = {1: syo(1, "Stable"), 2: syo(2, "Old title")}
        updated = syo(2, "New title")
        index = {1: ("Stable", STAMP), 2: ("New title", STAMP)}

        def get_batch(ids, with_details):
            self.assertEqual(ids, "2")
            self.assertTrue(with_details)
            return {2: updated}

        self.assertEqual(merge_syoboi(index, previous, get_batch), {1: previous[1], 2: updated})
        with self.assertRaisesRegex(ValueError, "did not converge"):
            merge_syoboi(index, previous, lambda ids, **_: index if ids == "*" else {})
        with self.assertRaisesRegex(ValueError, "unexpectedly dropped"):
            merge_syoboi({1: ("Stable", STAMP)}, previous, get_batch)

    @patch("sources.MIN_SYOBOI_TITLES", 1)
    def test_syoboi_reconciles_edits_without_redownloading_stable_details(self):
        original = {1: ("Stable", STAMP), 2: ("Old title", STAMP)}
        refreshed = {1: ("Stable", STAMP), 2: ("New title", STAMP)}
        details = {1: syo(1, "Stable"), 2: syo(2, "New title")}
        calls = []

        def lookup(ids, with_details=False):
            calls.append(ids)
            return details if with_details else refreshed

        self.assertEqual(merge_syoboi(original, {}, lookup), details)
        self.assertEqual(calls, ["1,2", "*"])
