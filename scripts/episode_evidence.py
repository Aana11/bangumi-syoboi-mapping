"""Compare episode sequences with a consistent episode-number offset."""

import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher

from matching_evidence import edition_key

GENERIC_TITLE = re.compile(r"(?:第)?(?:[0-9]+|[零一二三四五六七八九十]+)(?:話|回)?|(?:episode|ep|part|chapter)[0-9]+")
FORMAT_MARKS = re.compile(
    r"リマスター|remaster|リメイク|remake|総集編|セレクション|selection|"
    r"tv\s*(?:版|edition|edit)|再編集|再放送|digest|ダイジェスト|傑作選|短縮版|先行上映",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class EpisodeEvidence:
    bgm_id: int
    matches: int
    rare: int
    compared: int
    offset: int
    confirmed: bool


def chapter_title(value):
    text = unicodedata.normalize("NFKC", value).casefold()
    key = "".join(char for char in text if char.isalnum())
    if len(key) >= 3 and not GENERIC_TITLE.fullmatch(key):
        return key
    return ""


def chapter_numbers(episodes):
    names = defaultdict(set)
    for number, title in episodes:
        if key := chapter_title(title):
            names[number].add(key)
    return {number: next(iter(keys)) for number, keys in names.items() if len(keys) == 1}


def edition_compatible(syo_title, bgm_title):
    syo_base, syo_editions = edition_key(syo_title)
    bgm_base, bgm_editions = edition_key(bgm_title)
    for kind in {kind for kind, _ in syo_editions} & {kind for kind, _ in bgm_editions}:
        if {number for group, number in syo_editions if group == kind} != {
            number for group, number in bgm_editions if group == kind
        }:
            return False
    for prefix in ("新", "続"):
        if syo_base == prefix + bgm_base or bgm_base == prefix + syo_base:
            return False
    syo_format = FORMAT_MARKS.search(unicodedata.normalize("NFKC", syo_title))
    bgm_format = FORMAT_MARKS.search(unicodedata.normalize("NFKC", bgm_title))
    if syo_format or bgm_format:
        return bool(syo_format and bgm_format and syo_format[0].casefold() == bgm_format[0].casefold())
    return True


def dates_agree(syo, bgm):
    if not syo.premiere:
        return False
    months = (*bgm.broadcast_months, bgm.month)
    return any(
        abs((syo.year - int(month[:4])) * 12 + syo.month - int(month[5:])) <= 2
        for month in months if month
    )


def work_titles(titles):
    keys = set()
    for title in titles:
        key = "".join(char for char in edition_key(title)[0] if char.isalnum())
        if len(key) >= 3 or len(key) == 2 and all("\u3400" <= char <= "\u9fff" for char in key):
            keys.add(key)
    return keys


def titles_corroborate(syo_titles, bgm_titles):
    if syo_titles & bgm_titles:
        return True
    for left in syo_titles:
        for right in bgm_titles:
            shorter = min(len(left), len(right))
            if shorter < 6:
                continue
            overlap = SequenceMatcher(None, left, right, autojunk=False).find_longest_match().size
            if overlap >= 6 and overlap * 3 >= shorter * 2:
                return True
    return False


def titles_support_dense_sequence(syo_titles, bgm_titles):
    for left in syo_titles:
        for right in bgm_titles:
            shorter = min(len(left), len(right))
            if shorter < 3:
                continue
            matcher = SequenceMatcher(None, left, right, autojunk=False)
            overlap = matcher.find_longest_match().size
            if overlap == shorter or (shorter >= 6 and (overlap >= 6 and overlap * 2 >= shorter or matcher.ratio() >= 0.82)):
                return True
    return False


def compare_episodes(syoboi, bangumi):
    numbered = {bgm_id: chapter_numbers(item.episodes) for bgm_id, item in bangumi.items()}
    index = defaultdict(list)
    for bgm_id, chapters in numbered.items():
        for number, title in chapters.items():
            index[title].append((bgm_id, number))
    # Common or reused episode names provide little identity evidence.
    occurrences = {key: len({bgm_id for bgm_id, _ in entries}) for key, entries in index.items()}
    bgm_names = {}
    result = {}
    for tid, item in syoboi.items():
        chapters = chapter_numbers(item.episodes)
        if len(set(chapters.values())) < 2:
            continue
        syo_names = work_titles((item.title, item.english_title))
        anchors = defaultdict(set)
        for number, title in chapters.items():
            if occurrences.get(title, 0) > 20 or len(index.get(title, ())) > 50:
                continue
            for bgm_id, bgm_number in index.get(title, ()):
                anchors[bgm_id, bgm_number - number].add(title)

        alternatives = defaultdict(list)
        for (bgm_id, offset), hits in anchors.items():
            if len(hits) < 2:
                continue
            rare = sum(occurrences[key] <= 2 for key in hits)
            if not rare:
                continue
            compared = sum(number + offset in numbered[bgm_id] for number in chapters)
            bgm = bangumi[bgm_id]
            shared_site = bool(set(item.websites) & set(bgm.websites))
            if bgm_id not in bgm_names:
                bgm_names[bgm_id] = work_titles((bgm.title, *bgm.aliases))
            supporting = shared_site or dates_agree(item, bgm)
            same_month = bool(item.premiere and item.premiere in {bgm.month, *bgm.broadcast_months})
            confirmed = (
                len(hits) >= (2 if supporting else 4)
                and rare >= (1 if supporting else 2)
                and sum(map(len, hits)) >= (9 if supporting else 20)
                and len(hits) * 3 >= compared * 2
                and edition_compatible(item.title, bgm.title)
                and (
                    shared_site or titles_corroborate(syo_names, bgm_names[bgm_id])
                    or same_month and rare >= 5 and titles_support_dense_sequence(syo_names, bgm_names[bgm_id])
                )
            )
            alternatives[bgm_id].append(EpisodeEvidence(bgm_id, len(hits), rare, compared, offset, confirmed))

        proofs = []
        for options in alternatives.values():
            best = max(options, key=lambda proof: (proof.confirmed, proof.matches, proof.rare, -abs(proof.offset), -proof.offset))
            if sum(proof.confirmed for proof in options) > 1:
                best = EpisodeEvidence(best.bgm_id, best.matches, best.rare, best.compared, best.offset, False)
            proofs.append(best)
        if proofs:
            result[tid] = sorted(
                proofs, key=lambda proof: (-proof.confirmed, -proof.matches, -proof.rare, proof.bgm_id)
            )
    return result
