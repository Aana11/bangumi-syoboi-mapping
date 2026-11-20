"""Use release dates, programme links and credits to resolve catalogue gaps."""

import re
import unicodedata
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from urllib.parse import urlsplit

from episode_evidence import dates_agree, edition_compatible, titles_corroborate, work_titles
from matching_evidence import MOVIE_CATEGORY, edition_key, normalize, year_edition_matches
from sources import BANGUMI_MOVIE_PLATFORM, SYOBOI_CATEGORIES

LIVE_ACTION_MOVIE_PLATFORM = 6002  # bangumi/common subject_platforms.yml


def film_evidence(syoboi, anime, live_action):
    """A dated, unique title can identify a film, given both types of catalogue."""
    if live_action is None:
        return set(), set(), {}
    subjects, titles = defaultdict(set), defaultdict(set)
    subject_ids, title_ids = defaultdict(set), defaultdict(set)
    for item in (*anime.values(), *live_action.values()):
        for title in (item.title, *item.aliases):
            if key := normalize(title):
                subject_ids[key].add(item.id)
                if item.month:
                    subjects[key, item.month].add(item.id)
    for tid, item in syoboi.items():
        if key := normalize(item.title):
            title_ids[key].add(tid)
            if item.premiere:
                titles[key, item.premiere].add(tid)

    mapped, live, review = set(), set(), {}
    for tid, source in syoboi.items():
        if source.category != MOVIE_CATEGORY or not (key := normalize(source.title)):
            continue
        lookup = key, source.premiere
        ids = subjects.get(lookup, set())
        if anime_ids := ids & anime.keys():
            review[tid] = anime_ids
        if len(titles.get(lookup, ())) == len(ids) == 1:
            subject_id = next(iter(ids))
        elif not ids and len(key) >= 5 and len(title_ids[key]) == len(subject_ids[key]) == 1:
            subject_id = next(iter(subject_ids[key]))
            candidate = anime.get(subject_id) or live_action[subject_id]
            if not candidate.month or not release_month_supports(source, candidate.month):
                continue
        else:
            continue
        item = anime.get(subject_id) or live_action[subject_id]
        if normalize(item.title) != key:
            continue
        if item.live_action and item.platform == LIVE_ACTION_MOVIE_PLATFORM:
            live.add(tid)
        elif not item.live_action and item.platform == BANGUMI_MOVIE_PLATFORM:
            mapped.add((tid, subject_id))
    return mapped, live, review


def release_month_supports(source, month):
    year, release_month = map(int, month.split("-"))
    if source.premiere:
        return abs((source.year - year) * 12 + source.month - release_month) <= 1
    return source.year == year


def unique_original_titles(syoboi, anime, live_action):
    syoboi_names, bangumi_names = defaultdict(list), defaultdict(list)
    for tid, item in syoboi.items():
        syoboi_names[normalize(item.title)].append(tid)
    for item in (*anime.values(), *(live_action or {}).values()):
        bangumi_names[normalize(item.title)].append(item)
    for title, tids in syoboi_names.items():
        others = bangumi_names.get(title, ())
        if len(title) < 4 or len(tids) != 1 or len(others) != 1:
            continue
        item, subject = syoboi[tids[0]], others[0]
        if item.category not in SYOBOI_CATEGORIES or subject.live_action or not item.year:
            continue
        known_months = {subject.month, *subject.broadcast_months} - {None}
        if any(release_month_supports(item, month) for month in known_months) or (
            not item.premiere and subject.release_date == str(item.year)
        ):
            yield item.id, subject.id


def related_work_titles(item):
    """References may name a whole franchise, so require separate evidence."""
    keywords = []
    for key in item.keywords:
        if ":" not in key and len(key) >= 5:
            keywords.append(key)
    return work_titles((item.title, item.english_title, item.short_title, *keywords))


def programme_links(syoboi, anime, live_action):
    linked_tids, official_subjects = defaultdict(set), defaultdict(set)
    for tid, item in syoboi.items():
        for site in set(item.websites) | set(item.links):
            linked_tids[site].add(tid)
    for item in (*anime.values(), *(live_action or {}).values()):
        for site in item.websites:
            official_subjects[site].add(item.id)
    for site in official_subjects.keys() & linked_tids.keys():
        tids, ids = linked_tids[site], official_subjects[site]
        distinct = len(tids) == len(ids) == 1
        # Generic domains and search/listing URLs are unsuitable for this check.
        endpoint = urlsplit(f"https://{site}")
        if not endpoint.path or not endpoint.path.strip("/") or re.search(r"/(?:search|list)(?:/|$)", endpoint.path):
            continue
        for tid in tids:
            item = syoboi[tid]
            if item.category not in SYOBOI_CATEGORIES or site not in item.links:
                continue
            for subject_id in ids & anime.keys():
                bgm = anime[subject_id]
                if not edition_compatible(item.title, bgm.title):
                    continue
                primary = work_titles((item.title, item.english_title))
                names = work_titles((bgm.title, *bgm.aliases))
                source_version = edition_key(item.title)
                subject_version = edition_key(bgm.title)
                full_aliases = {edition_key(title) for title in (bgm.title, *bgm.aliases)}
                same_dated_work = bool(primary & names)
                same_full_version = source_version in full_aliases and source_version[1] == subject_version[1]
                same_year_edition = (
                    item.premiere and item.premiere in {bgm.month, *bgm.broadcast_months}
                    and year_edition_matches(item, bgm)
                )
                if distinct and same_full_version or same_year_edition or (
                    dates_agree(item, bgm) and (
                        same_dated_work
                        or distinct and titles_corroborate(related_work_titles(item), names)
                    )
                ):
                    yield tid, subject_id


def credit_key(name):
    text = unicodedata.normalize("NFKC", name).casefold()
    return "".join(char for char in text if char.isalnum())


def role_keys(cast):
    return {
        (role, actor) for raw_role, raw_actor in cast
        if len(role := credit_key(raw_role)) >= 2 and len(actor := credit_key(raw_actor)) >= 3
    }


def cast_evidence(syoboi, anime):
    credits = defaultdict(set)
    for subject_id, item in anime.items():
        for key in role_keys(item.cast):
            credits[key].add(subject_id)

    mapped, review = set(), {}
    for tid, item in syoboi.items():
        if item.category not in SYOBOI_CATEGORIES or not item.cast:
            continue
        shared = defaultdict(set)
        for key in role_keys(item.cast):
            subjects = credits.get(key, ())
            if len(subjects) <= 24:
                for bgm_id in subjects:
                    shared[bgm_id].add(key)
        names = related_work_titles(item)
        direct_names = work_titles((item.title, item.english_title))
        for bgm_id, pairs in shared.items():
            if len({role for role, _ in pairs}) < 2 or len({actor for _, actor in pairs}) < 2:
                continue
            rare = sum(len(credits[pair]) <= 2 for pair in pairs)
            if not rare:
                continue
            review.setdefault(tid, {})[bgm_id] = len(pairs)
            subject = anime[bgm_id]
            subject_names = work_titles((subject.title, *subject.aliases))
            title_support = titles_corroborate(names, subject_names)
            same_month = bool(item.premiere and item.premiere in {subject.month, *subject.broadcast_months})
            dates = dates_agree(item, subject)
            same_original = normalize(item.title) == normalize(subject.title)
            if edition_compatible(item.title, subject.title) and (
                same_month and title_support
                or dates and bool(direct_names & subject_names)
                or same_original and rare >= 2
            ):
                mapped.add((tid, bgm_id))
    return mapped, review


def title_fragments(title):
    return {title[index:index + 3] for index in range(len(title) - 2)}


def title_review(syoboi, anime):
    """Rank plausible spellings for review without creating any mapping."""
    if not syoboi:
        return {}
    names = {bgm_id: work_titles((item.title, *item.aliases)) for bgm_id, item in anime.items()}
    fragments = defaultdict(set)
    for bgm_id, aliases in names.items():
        for alias in aliases:
            for fragment in title_fragments(alias):
                fragments[fragment].add(bgm_id)

    review = {}
    for tid, item in syoboi.items():
        queries = work_titles((item.title, item.english_title, item.short_title))
        useful_fragments = {fragment for query in queries for fragment in title_fragments(query)}
        shared = Counter(
            bgm_id for fragment in useful_fragments
            if 0 < len(families := fragments.get(fragment, ())) <= 60
            for bgm_id in families
        )
        ranked = []
        for bgm_id, count in sorted(shared.items(), key=lambda pair: (-pair[1], pair[0]))[:32]:
            if count < 2:
                break
            score = max(
                SequenceMatcher(None, query, name, autojunk=False).ratio()
                for query in queries for name in names[bgm_id]
            )
            nearby = dates_agree(item, anime[bgm_id])
            if score >= (0.68 if nearby else 0.84):
                ranked.append((score, nearby, bgm_id))
        if ranked:
            review[tid] = [
                {"bgm_id": bgm_id, "title_similarity": round(score * 100)}
                for score, _, bgm_id in sorted(ranked, key=lambda row: (-row[1], -row[0], row[2]))[:3]
            ]
    return review
