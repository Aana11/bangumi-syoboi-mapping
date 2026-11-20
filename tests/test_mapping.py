import json
import sys
import tempfile
import unittest
from dataclasses import replace
from datetime import date
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from matching_evidence import edition_key
from sources import BangumiTitle, SyoboiTitle, bangumi_evidence, bangumi_month, merge_syoboi, parse_syoboi
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

    def test_edition_normalization_preserves_season_cour_and_title_numbers(self):
        self.assertEqual(edition_key("星の旅（第二期）第2クール"), edition_key("星の旅 Second Season Cour 2"))
        self.assertNotEqual(edition_key("星の旅 第2期"), edition_key("星の旅 第2クール"))
        self.assertNotEqual(edition_key("星の旅 第2期"), edition_key("星の旅 第3期"))
        self.assertNotEqual(edition_key("星の旅 四季"), edition_key("星の旅 第4季"))
        self.assertNotEqual(edition_key("星の旅 2"), edition_key("星の旅"))
        self.assertNotEqual(edition_key("7-11"), edition_key("711"))
        self.assertEqual(edition_key("空の旅 2ndシーズン"), edition_key("空の旅 第2シリーズ"))
        self.assertEqual(edition_key("空の旅 Part.2"), edition_key("空の旅 パート2"))
        self.assertNotEqual(edition_key("空の旅 Part 2"), edition_key("空の旅 第2期"))
        self.assertEqual(edition_key("塔の冒険～迷宮篇～"), edition_key("塔の冒険〜迷宮編〜"))

    def test_edition_alias_and_declared_broadcast_month(self):
        syoboi = [syo(1, "星の旅(第二期)第2クール"), syo(2, "天空の城")]
        bangumi = [
            BangumiTitle(10, "星の旅 Season 2 Cour 2", "2026-09", broadcast_months=("2026-10",)),
            BangumiTitle(20, "天上之城", "2026-10", aliases=("天空の城",)),
        ]
        methods, unmatched = match(syoboi, bangumi)
        self.assertEqual(methods, {(1, 10): "title_and_air_month", (2, 20): "alias_and_month"})
        self.assertFalse(unmatched)

    def test_alias_cannot_override_uniqueness(self):
        method, _ = match(
            [syo(1, "星の旅")],
            [BangumiTitle(10, "Star Travel", "2026-10", aliases=("星の旅",)),
             BangumiTitle(20, "Journey to the Stars", "2026-10", aliases=("星の旅",))],
        )
        self.assertFalse(method)

    def test_shared_official_site_checks_edition_and_month(self):
        site = ("example.org/title",)
        syoboi = [
            SyoboiTitle(1, "星の旅(2026)", STAMP, 2026, 10, 10, site),
            SyoboiTitle(2, "星の旅 第2クール", STAMP, 2025, 10, 10, site),
            SyoboiTitle(3, "星の旅 第4期", STAMP, 2027, 10, 10, site),
        ]
        bangumi = [
            BangumiTitle(10, "星の旅 第3期", "2026-10", websites=site),
            BangumiTitle(20, "星の旅", "2025-10", websites=site),
            BangumiTitle(30, "星の旅 第5期", "2027-10", websites=site),
        ]
        methods, _ = match(syoboi, bangumi)
        self.assertEqual(methods, {(1, 10): "official_site"})

    def test_official_film_requires_theatrical_platform_and_same_title(self):
        site = ("example.org/film",)
        syoboi = [SyoboiTitle(1, "銀河鉄道の夜", STAMP, 2026, 10, 8, site)]
        film = BangumiTitle(10, "銀河鉄道の夜", "2026-10", websites=site, platform=3)
        tv = BangumiTitle(10, "銀河鉄道の夜", "2026-10", websites=site, platform=1)
        self.assertEqual(match(syoboi, [film])[0], {(1, 10): "official_site"})
        self.assertFalse(match(syoboi, [tv])[0])

    def test_month_boundary_checks_exact_day_and_catalog_uniqueness(self):
        syoboi = [syo(1, "星の旅の記憶")]
        near = BangumiTitle(10, "星の旅の記憶", "2026-09", release_date="2026-09-29")
        far = BangumiTitle(10, "星の旅の記憶", "2026-09", release_date="2026-09-10")
        self.assertEqual(match(syoboi, [near])[0], {(1, 10): "month_boundary"})
        self.assertFalse(match(syoboi, [far])[0])
        self.assertFalse(match(syoboi, [near, bgm(20, near.title, "2000-10")])[0])

    def test_episode_sequences_allow_a_consistent_number_offset(self):
        chapters = ("山奥に眠る王宮", "追憶の港へ向かう舟", "白い狼の遠吠え")
        syoboi = replace(syo(1, "日本語のシリーズ名"), episodes=tuple(enumerate(chapters, 12)))
        bangumi = replace(
            bgm(10, "Series name", "2026-09"), aliases=("日本語のシリーズ名",),
            episodes=tuple(enumerate(chapters, 1)),
        )
        matches, unmatched = match([syoboi], [bangumi])
        self.assertEqual(matches, {(1, 10): "episode_sequence"})
        self.assertFalse(unmatched)

    def test_episode_sequences_keep_conflicting_editions_and_duplicates_for_review(self):
        episodes = ((1, "眠りの王宮"), (2, "北の海を渡る舟"), (3, "狼の遠吠え"))
        original = replace(syo(1, "星の旅 第2期"), episodes=episodes)
        season3 = replace(bgm(10, "星の旅 第3期"), episodes=episodes)
        methods, unmatched = match([original], [season3])
        self.assertFalse(methods)
        self.assertEqual(unmatched[0]["episode_candidates"][0]["matched_episodes"], 3)
        first = replace(season3, title="星の旅 Season 2")
        second = replace(season3, id=20, title="星の旅 (第2期)")
        self.assertFalse(match([original], [first, second])[0])

    def test_copied_episode_titles_do_not_establish_work_identity(self):
        episodes = tuple(enumerate(
            ("眠りの王宮", "北の海を渡る舟", "狼の遠吠え", "消えたカラスの秘密"), 1
        ))
        syo_item = replace(syo(1, "紅い旅"), episodes=episodes)
        another = replace(bgm(10, "月の見える庭園"), episodes=episodes)
        methods, unmatched = match([syo_item], [another])
        self.assertFalse(methods)
        self.assertEqual(unmatched[0]["episode_candidates"][0]["matched_episodes"], 4)

    def test_episode_titles_with_incompatible_numbering_are_insufficient(self):
        chapters = ((1, "眠りの王宮"), (2, "北の海を渡る舟"), (3, "狼の遠吠え"))
        syoboi = replace(syo(1, "日本語のシリーズ名"), episodes=chapters)
        bangumi = replace(bgm(10, "Series name"), episodes=tuple((i * 3, title) for i, title in chapters))
        self.assertFalse(match([syoboi], [bangumi])[0])

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
        <FirstYear/><FirstMonth/><Cat>8</Cat><TitleEN>Title EN</TitleEN>
        <SubTitles>*001*眠りの王宮
*0*Ignored
*2*北の海を渡る舟</SubTitles>
        <Comment>-[[公式（作品） https://www.example.org/Film/index.html]]
        -[[公式（動画） https://www.youtube.com/@sharedchannel]]
        -[[X https://x.com/film]]</Comment></TitleItem></TitleItems></TitleLookupResponse>"""
        result = parse_syoboi(xml, with_details=True)
        self.assertIsNone(result[1].premiere)
        self.assertEqual(result[1].category, 8)
        self.assertEqual(result[1].websites, ("example.org/Film",))
        self.assertEqual(result[1].english_title, "Title EN")
        self.assertEqual(result[1].episodes, ((1, "眠りの王宮"), (2, "北の海を渡る舟")))
        with self.assertRaisesRegex(ValueError, "403"):
            parse_syoboi(xml.replace("200", "403"), with_details=True)

    def test_infobox_provides_titles_air_dates_and_only_official_links(self):
        item = {
            "name": "Title", "name_cn": "标题", "platform": 1, "date": "2026-09-25",
            "infobox": """{{Infobox animanga/TVAnime
|别名= {
  [日文名|星の旅]
  [简称|星旅]
}
|放送开始=2026年10月1日
|官方网站=http://www.example.org/Title/
|播放结束=2027年3月20日
|导演=Director
|其他=https://example.org/unrelated
}}""",
        }
        self.assertEqual(
            bangumi_evidence(item),
            (("星の旅", "标题"), ("2026-10",), ("example.org/Title",), 1, "2026-09-25"),
        )

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
