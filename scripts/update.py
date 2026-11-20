"""Build the ID mapping, review queue and README statistics."""

import argparse
import json
import sys
import tempfile
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from itertools import chain
from pathlib import Path

from episode_evidence import compare_episodes
from matching_evidence import METHODS, MOVIE_CATEGORY, additional_pairs, normalize
from sources import SYOBOI_CATEGORIES, read_bangumi, read_syoboi

ROOT = Path(__file__).resolve().parents[1]
START = "<!-- mapping-stats:start -->"
END = "<!-- mapping-stats:end -->"


def pair_fields(tid, bgm_id):
    return {"bgm_id": bgm_id, "syoboi_tid": tid}


def read_overrides(document, syoboi, bangumi):
    if set(document) != {"mappings", "blocked_pairs"}:
        raise ValueError("Overrides must have mappings and blocked_pairs lists")
    pairs = []
    for group in ("mappings", "blocked_pairs"):
        if not isinstance(document[group], list):
            raise ValueError(f"Overrides {group} must be a list")
        parsed = set()
        for row in document[group]:
            tid, bgm_id = row["syoboi_tid"], row["bgm_id"]
            if type(tid) is not int or type(bgm_id) is not int or min(tid, bgm_id) < 1:
                raise ValueError(f"Invalid IDs in {group}")
            pair = (tid, bgm_id)
            if pair in parsed or not isinstance(row["evidence"], str) or not row["evidence"].strip():
                raise ValueError(f"Duplicate override or missing evidence: {pair}")
            if group == "mappings":
                item = syoboi.get(tid)
                if not item or item.category not in SYOBOI_CATEGORIES | {MOVIE_CATEGORY} or bgm_id not in bangumi:
                    raise ValueError(f"Manual mapping references an unavailable or out-of-scope ID: {pair}")
                if row["syoboi_title"] != item.title:
                    raise ValueError(f"Review the manual mapping for TID {tid}: its upstream title changed")
            parsed.add(pair)
        pairs.append(parsed)
    manual, blocked = pairs
    if manual & blocked:
        raise ValueError("An override cannot simultaneously map and block the same pair")
    return manual, blocked


def match_titles(syoboi, bangumi, overrides):
    manual, blocked = read_overrides(overrides, syoboi, bangumi)
    candidates = {
        tid: item for tid, item in syoboi.items()
        if item.category in SYOBOI_CATEGORIES | {MOVIE_CATEGORY}
    }
    methods = dict.fromkeys(manual, "manual")
    taken_tids = {tid for tid, _ in manual}
    taken_bgm = {bgm_id for _, bgm_id in manual}
    review = defaultdict(set)
    title_ids = defaultdict(set)
    for bgm_id, item in bangumi.items():
        if key := normalize(item.title):
            title_ids[key].add(bgm_id)

    for method, key in (("exact", str.strip), ("normalized", normalize)):
        bgm_keys = defaultdict(list)
        tid_keys = defaultdict(list)
        for bgm_id, item in bangumi.items():
            if item.month and (title := key(item.title)):
                bgm_keys[title, item.month].append(bgm_id)
        for tid, item in candidates.items():
            if item.premiere and (title := key(item.title)):
                tid_keys[title, item.premiere].append(tid)
        for lookup, tids in tid_keys.items():
            ids = bgm_keys.get(lookup, [])
            for tid in tids:
                review[tid].update(ids)
            if len(tids) != 1 or len(ids) != 1:
                continue
            tid, bgm_id = tids[0], ids[0]
            if (
                candidates[tid].category in SYOBOI_CATEGORIES
                and tid not in taken_tids and bgm_id not in taken_bgm
                and (tid, bgm_id) not in blocked
            ):
                methods[tid, bgm_id] = method
                taken_tids.add(tid)
                taken_bgm.add(bgm_id)

    episode_proofs = compare_episodes({
        tid: item for tid, item in candidates.items()
        if tid not in taken_tids and item.category in SYOBOI_CATEGORIES
    }, bangumi)
    episode_pairs = {
        (tid, proof.bgm_id) for tid, proofs in episode_proofs.items()
        for proof in proofs if proof.confirmed
    }
    evidence_batches = chain(additional_pairs(syoboi, bangumi), (("episode_sequence", episode_pairs),))
    for method, pairs in evidence_batches:
        choices = defaultdict(set)
        users = defaultdict(set)
        for tid, bgm_id in pairs:
            review[tid].add(bgm_id)
            if candidates[tid].category in SYOBOI_CATEGORIES or method == "official_site":
                choices[tid].add(bgm_id)
                users[bgm_id].add(tid)
        for tid, ids in choices.items():
            if len(ids) != 1 or tid in taken_tids:
                continue
            bgm_id = next(iter(ids))
            if len(users[bgm_id]) == 1 and bgm_id not in taken_bgm and (tid, bgm_id) not in blocked:
                methods[tid, bgm_id] = method
                taken_tids.add(tid)
                taken_bgm.add(bgm_id)

    unmatched = []
    for tid in sorted(candidates.keys() - taken_tids):
        item = candidates[tid]
        ids = review[tid]
        proofs = episode_proofs.get(tid, [])
        if item.category == MOVIE_CATEGORY:
            reason = "movie_review"
        elif not item.premiere:
            reason = "missing_premiere_month"
        elif ids and all((tid, bgm_id) in blocked for bgm_id in ids):
            reason = "blocked"
        elif ids:
            reason = "ambiguous_or_reserved"
        elif proofs:
            reason = "episode_review"
        elif ids := title_ids.get(normalize(item.title)):
            reason = "premiere_month_mismatch_or_missing"
        else:
            reason = "no_title_match"
        ids = (ids or title_ids.get(normalize(item.title), set())) | {proof.bgm_id for proof in proofs}
        row = {
            "syoboi_tid": tid,
            "category": item.category,
            "first_year": item.year or None,
            "first_month": item.month or None,
            "reason": reason,
            "candidate_bgm_count": len(ids),
            "candidate_bgm_ids": sorted(ids)[:12],
        }
        if proofs:
            row["episode_candidates"] = [
                {"bgm_id": proof.bgm_id, "matched_episodes": proof.matches,
                 "compared_episodes": proof.compared, "episode_number_offset": proof.offset}
                for proof in proofs[:5]
            ]
        unmatched.append(row)
    return methods, unmatched


def coverage(tids, mapped):
    return {"total": len(tids), "matched": len(tids & mapped), "unmatched": len(tids - mapped)}


def statistics(syoboi, bangumi, methods, unmatched, bangumi_source, syoboi_last_update, today):
    mapped = {tid for tid, _ in methods}
    anime = {tid for tid, item in syoboi.items() if item.category in SYOBOI_CATEGORIES}
    movies = {tid for tid, item in syoboi.items() if item.category == MOVIE_CATEGORY}
    current_quarter = (today.month - 1) // 3 + 1
    quarter_tids = {
        tid for tid in anime if syoboi[tid].year == today.year and syoboi[tid].month
        and (syoboi[tid].month - 1) // 3 + 1 == current_quarter
    }
    years = defaultdict(set)
    for tid in anime:
        if year := syoboi[tid].year:
            years[year].add(tid)
    method_counts = Counter(methods.values())
    return {
        "schema_version": 1,
        "sources": {
            "bangumi": bangumi_source,
            "syoboi": {"titles": len(syoboi), "latest_upstream_edit": syoboi_last_update},
        },
        "bangumi_anime": {"total": len(bangumi), "matched": len({bgm_id for _, bgm_id in methods})},
        "syoboi_by_category": dict(sorted(Counter(item.category for item in syoboi.values()).items())),
        "syoboi_anime": coverage(anime, mapped),
        "syoboi_mixed_movies": coverage(movies, mapped),
        "mapped_pairs": len(methods),
        "match_methods": {name: method_counts[name] for name in METHODS},
        "unmatched_reasons": dict(sorted(Counter(row["reason"] for row in unmatched).items())),
        "by_year": {year: coverage(tids, mapped) for year, tids in sorted(years.items())},
        "current_quarter": {
            "year": today.year, "quarter": current_quarter, **coverage(quarter_tids, mapped),
        },
    }


def readme_table(stats):
    def row(label, counts):
        total, matched = counts["total"], counts["matched"]
        rate = f"{matched / total:.2%}" if total else "—"
        return f"| {label} | {total:,} | {matched:,} | {total - matched:,} | {rate} |"

    quarter = stats["current_quarter"]
    source = stats["sources"]["bangumi"]["created_at"]
    edit = stats["sources"]["syoboi"]["latest_upstream_edit"]
    lines = [
        f"Bangumi Archive 快照 {source} · Syoboi 上游最近编辑 {edit}",
        "",
        "| 范围 | Syoboi TID | 已关联 | 待关联 | 覆盖率 |",
        "| --- | ---: | ---: | ---: | ---: |",
        row("全部历史动画", stats["syoboi_anime"]),
        row(f"当季 {quarter['year']} Q{quarter['quarter']}", quarter),
    ]
    for year in range(quarter["year"], quarter["year"] - 3, -1):
        lines.append(row(str(year), stats["by_year"].get(year, coverage(set(), set()))))
    lines += [
        "",
        f"采集了 Syoboi 全分类 **{stats['sources']['syoboi']['titles']:,}** 个 TID，"
        f"对照 Bangumi Archive **{stats['bangumi_anime']['total']:,}** 个动画条目，"
        f"生成 **{stats['mapped_pairs']:,}** 对 ID。",
        f"混合电影分类有 **{stats['syoboi_mixed_movies']['total']:,}** 个 TID，"
        f"其中 **{stats['syoboi_mixed_movies']['matched']:,}** 个已关联。",
    ]
    return "\n".join(lines)


def guard_previous(old, stats):
    if not old:
        return
    for name, previous, current in (
        ("Bangumi anime count", old["bangumi_anime"]["total"], stats["bangumi_anime"]["total"]),
        ("Syoboi title count", old["sources"]["syoboi"]["titles"], stats["sources"]["syoboi"]["titles"]),
        ("Syoboi anime count", old["syoboi_anime"]["total"], stats["syoboi_anime"]["total"]),
        ("Matched Syoboi anime", old["syoboi_anime"]["matched"], stats["syoboi_anime"]["matched"]),
    ):
        if current * 100 < previous * 95:
            raise ValueError(f"{name} fell from {previous:,} to {current:,}; investigate before replacing the snapshot")


def write_snapshot(root, documents, stats):
    readme = (root / "README.md").read_text(encoding="utf-8")
    if readme.count(START) != 1 or readme.count(END) != 1 or readme.index(START) >= readme.index(END):
        raise ValueError("README has missing or misplaced statistics markers")
    start = readme.index(START) + len(START)
    end = readme.index(END)
    readme = f"{readme[:start]}\n\n{readme_table(stats)}\n\n{readme[end:]}"
    encoded = {
        f"data/{name}.json": json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        for name, payload in documents.items()
    }
    encoded["README.md"] = readme
    # Finish parsing, validation and serialization before replacing any output.
    # The workflow commits the entire snapshot in one Git commit.
    with tempfile.TemporaryDirectory(dir=root) as temporary:
        prepared = []
        for number, (name, content) in enumerate(encoded.items()):
            target = root / name
            data = content.encode("utf-8")
            if target.exists() and target.read_bytes() == data:
                continue
            source = Path(temporary) / str(number)
            source.write_bytes(data)
            prepared.append((source, target))
        replaced = []
        try:
            for source, target in prepared:
                previous = target.read_bytes() if target.exists() else None
                source.replace(target)
                replaced.append((source, target, previous))
        except OSError:
            for source, target, previous in reversed(replaced):
                if previous is None:
                    target.unlink()
                else:
                    source.write_bytes(previous)
                    source.replace(target)
            raise


@contextmanager
def update_lock(cache):
    with (cache / "update.lock").open("ab") as lock:
        try:
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise ValueError("Could not acquire the local update lock") from error
        yield


def update(cache, offline, today):
    overrides = json.loads((ROOT / "data/overrides.json").read_bytes())
    previous_stats = ROOT / "data/stats.json"
    old = json.loads(previous_stats.read_bytes()) if previous_stats.exists() else None
    with ThreadPoolExecutor(max_workers=2) as readers:
        bgm_result = readers.submit(read_bangumi, cache, offline)
        syoboi_result = readers.submit(read_syoboi, cache, offline)
        bangumi, bangumi_source = bgm_result.result()
        syoboi, syoboi_last_update = syoboi_result.result()
    methods, unmatched = match_titles(syoboi, bangumi, overrides)
    if not methods:
        raise ValueError("No matches; refusing to generate an empty mapping")
    stats = statistics(syoboi, bangumi, methods, unmatched, bangumi_source, syoboi_last_update, today)
    guard_previous(old, stats)
    mapping = [pair_fields(tid, bgm_id) for tid, bgm_id in sorted(methods)]
    write_snapshot(ROOT, {"mapping": mapping, "unmatched": unmatched, "stats": stats}, stats)
    print(f"Generated {len(mapping):,} pairs; {stats['syoboi_anime']['unmatched']:,} core anime TIDs to review.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="Rebuild from the last complete local inputs")
    parser.add_argument("--as-of", type=date.fromisoformat, help="Override the date for quarterly statistics")
    args = parser.parse_args()
    today = args.as_of or datetime.now(timezone(timedelta(hours=9))).date()
    cache = ROOT / ".cache"
    cache.mkdir(exist_ok=True)
    with update_lock(cache):
        update(cache, args.offline, today)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        sys.exit(f"Update failed: {error}")
