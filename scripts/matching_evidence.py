"""Additional matches supported by edition, release and official-site evidence."""

import re
import unicodedata
from calendar import monthrange
from collections import defaultdict
from datetime import date, timedelta

from sources import BANGUMI_MOVIE_PLATFORM, SYOBOI_CATEGORIES

MOVIE_CATEGORY = 8
METHODS = (
    "manual", "exact", "normalized", "title_and_air_month", "alias_and_month",
    "official_site", "month_boundary", "episode_sequence",
)
FORMATTING_MARKS = frozenset("・「」『』【】《》〈〉")
TITLE_SEPARATORS = frozenset("()[]:~〜") | FORMATTING_MARKS
NUMERAL = r"[0-9]{1,2}|[一二三四五六七八九十]{1,3}"
EDITION_RULES = (
    ("season", re.compile(rf"第\s*({NUMERAL})\s*(?:期|季)(?!刊|末|初)")),
    ("season", re.compile(r"(?<![0-9])([0-9]{1,2})\s*(?:期|季)(?!刊|末|初)")),
    ("season", re.compile(rf"第\s*({NUMERAL})\s*(?:シーズン|シリーズ)")),
    ("season", re.compile(rf"(?:シーズン|(?<![a-z])season)\s*({NUMERAL})(?![0-9])")),
    ("season", re.compile(r"([0-9]{1,2})(?:st|nd|rd|th)\s*(?:season\b|シーズン)")),
    ("season", re.compile(r"\b(first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\s+season\b")),
    ("cour", re.compile(rf"(?<![0-9])(?:第\s*)?({NUMERAL})(?:st|nd|rd|th)?\s*クール(?:目)?")),
    ("cour", re.compile(r"cour\s*([0-9]{1,2})(?![0-9])")),
    ("part", re.compile(r"(?:(?<![a-z])part|パート)\s*\.?\s*([0-9]{1,2})(?![0-9])")),
)
ORDINALS = ("first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth")


def normalize(title):
    text = unicodedata.normalize("NFKC", title).casefold()
    return "".join(char for char in text if not char.isspace() and char not in FORMATTING_MARKS)


def edition_number(number):
    if number.isdecimal():
        return int(number)
    if number in ORDINALS:
        return ORDINALS.index(number) + 1
    numerals = "〇一二三四五六七八九"
    if "十" in number:
        tens, _, ones = number.partition("十")
        if len(tens) <= 1 and len(ones) <= 1 and "十" not in ones:
            return 10 * (numerals.index(tens) if tens else 1) + (numerals.index(ones) if ones else 0)
    if len(number) == 1 and number in numerals:
        return numerals.index(number)
    return None


def edition_key(title):
    text = unicodedata.normalize("NFKC", title).casefold()
    editions = set()
    for kind, pattern in EDITION_RULES:
        def record(match):
            number = edition_number(match[1])
            if number is None:
                return match[0]
            editions.add((kind, number))
            return " "

        text = pattern.sub(record, text)
    text = re.sub(r"(?<![0-9])[-‐‑‒–—―](?![0-9])", "", text)
    # Retain title words, subtitles, all remaining numbers and meaningful symbols.
    text = "".join(char for char in text if not char.isspace() and char not in TITLE_SEPARATORS)
    if text.endswith("篇"):
        text = text[:-1] + "編"
    return text, tuple(sorted(editions))


def title_keys(titles):
    return {key for title in titles if title and len((key := edition_key(title))[0]) >= 3}


def join_by_month(syoboi, bangumi, syoboi_keys, bangumi_keys, months):
    subjects = defaultdict(set)
    for bgm_id in bangumi:
        for month in months[bgm_id]:
            for title in bangumi_keys[bgm_id]:
                subjects[month, title].add(bgm_id)
    return {
        (tid, bgm_id)
        for tid, item in syoboi.items() if item.premiere
        for title in syoboi_keys[tid]
        for bgm_id in subjects.get((item.premiere, title), ())
    }


def dates_support(syo, bgm, months):
    if syo.premiere and months:
        year, month = map(int, syo.premiere.split("-"))
        return any(
            abs(12 * (year - int(value[:4])) + month - int(value[5:])) <= 2
            for value in months
        )
    if syo.year and months:
        return any(int(value[:4]) == syo.year for value in months)
    if syo.year and bgm.release_date.isdecimal() and len(bgm.release_date) == 4:
        return syo.year == int(bgm.release_date)
    return True


def official_pairs(syoboi, bangumi, syo_titles, bgm_titles, months):
    syoboi_sites, bangumi_sites = defaultdict(set), defaultdict(set)
    for tid, item in syoboi.items():
        for site in item.websites:
            syoboi_sites[site].add(tid)
    for bgm_id, item in bangumi.items():
        for site in item.websites:
            bangumi_sites[site].add(bgm_id)
    for site in syoboi_sites.keys() & bangumi_sites.keys():
        tids, ids = syoboi_sites[site], bangumi_sites[site]
        distinct_page = len(tids) == len(ids) == 1
        for tid in tids:
            syo = syoboi[tid]
            if tid not in syo_titles:
                continue
            for bgm_id in ids:
                bgm = bangumi[bgm_id]
                same_month = syo.premiere in months[bgm_id]
                common_titles = syo_titles[tid] & bgm_titles[bgm_id]
                same_title = bool(common_titles)
                if syo.category == MOVIE_CATEGORY:
                    if bgm.platform == BANGUMI_MOVIE_PLATFORM and same_month and same_title and distinct_page:
                        yield tid, bgm_id
                elif syo.category in SYOBOI_CATEGORIES:
                    if same_title and (same_month or (distinct_page and (
                        dates_support(syo, bgm, months[bgm_id]) or any(len(key[0]) >= 5 for key in common_titles)
                    ))):
                        yield tid, bgm_id
                    elif same_month and year_edition_matches(syo, bgm):
                        yield tid, bgm_id
                    elif distinct_page and same_month and edition_key(syo.title)[1] == edition_key(bgm.title)[1]:
                        yield tid, bgm_id


def year_edition_matches(syo, bgm):
    title = unicodedata.normalize("NFKC", syo.title).strip()
    marker = re.search(r"\(([12][0-9]{3})\)", title)
    if not marker or int(marker[1]) > syo.year:
        return False
    base, syo_version = edition_key(title[:marker.start()] + title[marker.end():])
    bgm_base, bgm_version = edition_key(bgm.title)
    return len(base) >= 3 and base == bgm_base and (
        bool(syo_version) and syo_version == bgm_version
        or int(marker[1]) == syo.year and not syo_version and all(kind == "season" for kind, _ in bgm_version)
    )


def month_boundary_pairs(syoboi, bangumi):
    tids, ids = defaultdict(list), defaultdict(list)
    for tid, item in syoboi.items():
        tids[normalize(item.title)].append(tid)
    for bgm_id, item in bangumi.items():
        ids[normalize(item.title)].append(bgm_id)
    for title in tids.keys() & ids.keys():
        if len(title) < 4 or len(tids[title]) != 1 or len(ids[title]) != 1:
            continue
        tid, bgm_id = tids[title][0], ids[title][0]
        syo, bgm = syoboi[tid], bangumi[bgm_id]
        if syo.category not in SYOBOI_CATEGORIES or not syo.premiere or not bgm.month or bgm.month == syo.premiere:
            continue
        try:
            release = date.fromisoformat(bgm.release_date)
        except ValueError:
            continue
        start = date(syo.year, syo.month, 1)
        end = date(syo.year, syo.month, monthrange(syo.year, syo.month)[1])
        if start - timedelta(days=10) <= release <= end + timedelta(days=10):
            yield tid, bgm_id


def additional_pairs(syoboi, bangumi):
    eligible = {tid: item for tid, item in syoboi.items() if item.category in SYOBOI_CATEGORIES | {MOVIE_CATEGORY}}
    syo_main = {tid: title_keys((item.title,)) for tid, item in eligible.items()}
    bgm_main = {bgm_id: title_keys((item.title,)) for bgm_id, item in bangumi.items()}
    bgm_aliases = {
        bgm_id: bgm_main[bgm_id] | {
            key for key in title_keys(item.aliases)
            if not any(main[1] for main in bgm_main[bgm_id])
            or any(key[1] == main[1] for main in bgm_main[bgm_id])
        }
        for bgm_id, item in bangumi.items()
    }
    syo_aliases = {
        tid: syo_main[tid] | {
            key for key in title_keys((item.english_title,))
            if key[1] == edition_key(item.title)[1]
        }
        for tid, item in eligible.items()
    }
    months = {
        bgm_id: set(item.broadcast_months) | ({item.month} if item.month else set())
        for bgm_id, item in bangumi.items()
    }
    yield "title_and_air_month", join_by_month(eligible, bangumi, syo_main, bgm_main, months)
    yield "alias_and_month", join_by_month(eligible, bangumi, syo_aliases, bgm_aliases, months)
    yield "official_site", set(official_pairs(syoboi, bangumi, syo_aliases, bgm_aliases, months))
    yield "month_boundary", set(month_boundary_pairs(syoboi, bangumi))
