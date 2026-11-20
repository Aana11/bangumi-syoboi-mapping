"""Read only the fields needed from the two public upstreams."""

import hashlib
import json
import re
import time
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen
from unicodedata import normalize as unicode_normalize
from xml.etree import ElementTree
from zipfile import ZipFile

ARCHIVE_INDEX = "https://raw.githubusercontent.com/bangumi/Archive/master/aux/latest.json"
SYOBOI_DB = "https://cal.syoboi.jp/db"
USER_AGENT = "BangumiSyoboiMapping/0.1 (+https://github.com/Aana11/bangumi-syoboi-mapping)"
SYOBOI_CATEGORIES = frozenset({1, 7, 10})
# bangumi/common subject_platforms.yml identifies theatrical anime films with 3.
BANGUMI_MOVIE_PLATFORM = 3
MIN_BANGUMI_ANIME = 10_000
MIN_BANGUMI_LIVE_ACTION = 1_000
MIN_SYOBOI_TITLES = 5_000
INFOBOX_ALIASES = frozenset({"别名", "原名", "日文名", "英文名", "简体中文名", "台湾名称", "港澳名称"})
INFOBOX_AIR_DATES = frozenset({"放送开始", "首播", "首播日期", "日本首播", "日本放送开始", "上映日", "发售日"})
INFOBOX_WEBSITES = frozenset({"官方网站", "官网", "公式サイト"})
OFFICIAL_COMMENT = re.compile(r"\[\[公式(?:サイト)?(?:[(（][^）)\]\r\n]+[）)])?\s+(https?://[^\s\]]+)\s*\]\]")
COMMENT_LINK = re.compile(r"\[\[([^]\r\n]+?)\s+(https?://[^\s\]]+)\s*\]\]")


@dataclass(frozen=True)
class BangumiTitle:
    id: int
    title: str
    month: str | None
    aliases: tuple[str, ...] = ()
    broadcast_months: tuple[str, ...] = ()
    websites: tuple[str, ...] = ()
    platform: int | None = None
    release_date: str = ""
    episodes: tuple[tuple[int, str], ...] = ()
    cast: tuple[tuple[str, str], ...] = ()
    live_action: bool = False


@dataclass(frozen=True)
class SyoboiTitle:
    id: int
    title: str
    updated: str
    year: int
    month: int
    category: int
    websites: tuple[str, ...] = ()
    english_title: str = ""
    episodes: tuple[tuple[int, str], ...] = ()
    short_title: str = ""
    keywords: tuple[str, ...] = ()
    links: tuple[str, ...] = ()
    cast: tuple[tuple[str, str], ...] = ()

    @property
    def premiere(self):
        if self.year and self.month:
            return f"{self.year:04d}-{self.month:02d}"
        return None


def request(url, read, interval=0):
    for attempt in range(3):
        if interval:
            time.sleep(interval)
        try:
            with urlopen(Request(url, headers={"User-Agent": USER_AGENT}), timeout=60) as response:
                return read(response)
        except (OSError, URLError) as error:
            if isinstance(error, HTTPError) and error.code not in {408, 429, 500, 502, 503, 504}:
                raise
            if attempt == 2:
                raise
            retry_after = error.headers.get("Retry-After", "") if isinstance(error, HTTPError) else ""
            if retry_after.isdecimal():
                delay = int(retry_after)
                if delay > 120:
                    raise
            else:
                delay = 2 ** (attempt + 1)
            time.sleep(delay)


def read_limited(response, limit):
    content = response.read(limit + 1)
    if len(content) > limit:
        raise ValueError("Upstream response exceeded the expected size")
    return content


def download_archive(metadata, cache):
    url = metadata["browser_download_url"]
    digest = metadata["digest"]
    size = metadata["size"]
    if not re.fullmatch(r"https://github\.com/bangumi/Archive/releases/download/archive/dump-[\w.-]+\.zip", url):
        raise ValueError("Unexpected Bangumi Archive download URL")
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", digest) or type(size) is not int or not 0 < size <= 2**30:
        raise ValueError("Invalid Bangumi Archive checksum or size")
    checksum = digest.removeprefix("sha256:")
    path = cache / f"archive-{checksum}.zip"
    if path.exists() and path.stat().st_size == size:
        with path.open("rb") as archive:
            if hashlib.file_digest(archive, "sha256").hexdigest() == checksum:
                return path

    temporary = path.with_suffix(".part")

    def save(response):
        sha256 = hashlib.sha256()
        total = 0
        with temporary.open("wb") as archive:
            while chunk := response.read(2**20):
                total += len(chunk)
                if total > size:
                    raise ValueError("Bangumi Archive is larger than the advertised size")
                sha256.update(chunk)
                archive.write(chunk)
        if total != size or sha256.hexdigest() != checksum:
            raise ValueError("Bangumi Archive size or checksum mismatch")

    try:
        request(url, save)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def bangumi_month(value):
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"([1-9][0-9]{3})-(0[1-9]|1[0-2])(?:-[0-9]{2})?", value)
    return f"{match[1]}-{match[2]}" if match else None


def website_key(value):
    try:
        url = urlsplit(value.strip())
        host = url.hostname
        if (
            url.scheme not in {"http", "https"} or not host or "." not in host
            or url.username or url.password or url.port
        ):
            return None
    except ValueError:
        return None
    host = host.removeprefix("www.")
    if host in {"x.com", "twitter.com", "youtube.com", "youtu.be", "instagram.com", "facebook.com"}:
        return None
    path = re.sub(r"/index\.(?:html?|php)$", "/", url.path).rstrip("/")
    return host + path + (f"?{url.query}" if url.query else "")


def infobox_fields(infobox):
    fields = INFOBOX_ALIASES | INFOBOX_AIR_DATES | INFOBOX_WEBSITES
    # Infobox values are single-line strings or brace-delimited lists.
    for match in re.finditer(r"(?m)^\|([^=\r\n]+)=[ \t]*(\{\s*\r?\n[^}]*?^ *\}|[^\r\n]*)", infobox):
        key, raw = match[1].strip(), match[2].strip()
        if key not in fields:
            continue
        if raw.startswith("{"):
            for element in re.findall(r"(?m)^[ \t]*\[([^\r\n]+)\][ \t]*\r?$", raw):
                label, sep, value = element.partition("|")
                if key in INFOBOX_ALIASES and sep and re.search(r"简称|略称|昵称|缩写|通称", label):
                    continue
                yield key, (value if sep else label).strip()
        else:
            yield key, raw


def field_month(value):
    value = unicode_normalize("NFKC", value)
    match = re.fullmatch(
        r"([1-9][0-9]{3})\s*(?:[-/.]|年)\s*(0?[1-9]|1[0-2])"
        r"(?:\s*(?:[-/.]|月)\s*(?:0?[1-9]|[12][0-9]|3[01])日?|\s*月)?",
        value,
    )
    return f"{match[1]}-{int(match[2]):02d}" if match else None


def bangumi_evidence(item):
    aliases = {item["name_cn"]} if item.get("name_cn") else set()
    broadcast_months, websites = set(), set()
    for key, value in infobox_fields(item.get("infobox") or ""):
        if key in INFOBOX_ALIASES and 3 <= len(value) <= 250:
            aliases.add(value)
        elif key in INFOBOX_AIR_DATES and (month := field_month(value)):
            broadcast_months.add(month)
        elif key in INFOBOX_WEBSITES:
            websites.update(
                site for url in re.findall(r"https?://[^\s\]>,，]+", value)
                if (site := website_key(url))
            )
    return (
        tuple(sorted(aliases - {item["name"]})), tuple(sorted(broadcast_months)),
        tuple(sorted(websites)), item.get("platform") or None, item.get("date") or "",
    )


def extract_cast(dump, anime_ids):
    roles = defaultdict(list)
    character_ids, person_ids = set(), set()
    with dump.open("person-characters.jsonlines") as relationships:
        for line in relationships:
            row = json.loads(line)
            if row["subject_id"] in anime_ids:
                character_id, person_id = row["character_id"], row["person_id"]
                roles[row["subject_id"]].append((character_id, person_id))
                character_ids.add(character_id)
                person_ids.add(person_id)
    names = []
    for member, ids in (("character.jsonlines", character_ids), ("person.jsonlines", person_ids)):
        with dump.open(member) as records:
            table = {}
            for line in records:
                row = json.loads(line)
                if row["id"] in ids:
                    table[row["id"]] = row["name"]
            names.append(table)
    characters, actors = names
    return {
        subject_id: tuple(sorted({
            (characters[character], actors[actor]) for character, actor in cast
            if characters.get(character) and actors.get(actor)
        }))
        for subject_id, cast in roles.items()
    }


def extract_catalog(archive):
    catalog = {}
    with ZipFile(archive) as dump:
        with dump.open("subject.jsonlines") as subjects:
            for line in subjects:
                item = json.loads(line)
                if item["type"] not in {2, 6}:
                    continue
                subject_id, title = item["id"], item["name"]
                if type(subject_id) is not int or subject_id <= 0 or subject_id in catalog or not isinstance(title, str):
                    raise ValueError("Bangumi Archive contains an invalid or repeated subject")
                catalog[subject_id] = BangumiTitle(
                    subject_id, title, bangumi_month(item["date"]), *bangumi_evidence(item),
                    live_action=item["type"] == 6,
                )
        anime_ids = {subject_id for subject_id, item in catalog.items() if not item.live_action}
        episodes = defaultdict(list)
        with dump.open("episode.jsonlines") as chapter_file:
            for line in chapter_file:
                episode = json.loads(line)
                subject_id = episode["subject_id"]
                if episode["type"] != 0 or subject_id not in anime_ids:
                    continue
                number = episode["sort"]
                title = (episode.get("name") or "").strip()
                if type(number) in {int, float} and 1 <= number <= 10_000 and number == int(number) and title:
                    episodes[subject_id].append((int(number), title))
        cast = extract_cast(dump, anime_ids)
    for subject_id in anime_ids:
        catalog[subject_id] = replace(
            catalog[subject_id], episodes=tuple(sorted(set(episodes.get(subject_id, ())))),
            cast=cast.get(subject_id, ()),
        )
    return catalog


def archive_source(metadata):
    return {
        "archive": metadata["name"],
        "sha256": metadata["digest"].removeprefix("sha256:"),
        "created_at": metadata["created_at"],
    }


def read_bangumi(cache, offline=False):
    path = cache / "bangumi-catalog-v4.json"
    saved = json.loads(path.read_bytes()) if path.exists() else None
    if offline and not saved:
        raise ValueError("Bangumi anime cache missing; run an online update first")
    metadata = None if offline else json.loads(request(ARCHIVE_INDEX, lambda response: read_limited(response, 128_000)))
    source = saved["source"] if offline else archive_source(metadata)
    if saved and saved["source"] == source:
        catalog = {
            row[0]: BangumiTitle(
                *row[:3], tuple(row[3]), tuple(row[4]), tuple(row[5]), row[6] or None,
                row[7], tuple(tuple(part) for part in row[8]), tuple(tuple(role) for role in row[9]), row[10],
            )
            for row in saved["titles"]
        }
        if len(catalog) != len(saved["titles"]):
            raise ValueError("Bangumi anime cache is incomplete or contains duplicates")
    else:
        archive = download_archive(metadata, cache)
        catalog = extract_catalog(archive)

    live_count = sum(item.live_action for item in catalog.values())
    anime_count = len(catalog) - live_count
    if anime_count < MIN_BANGUMI_ANIME or live_count < MIN_BANGUMI_LIVE_ACTION:
        raise ValueError(f"Bangumi snapshot is incomplete: {anime_count} anime and {live_count} live-action entries")
    if not saved or saved["source"] != source:
        write_cache(path, {
            "source": source,
            "titles": [
                [item.id, item.title, item.month, item.aliases, item.broadcast_months,
                 item.websites, item.platform, item.release_date, item.episodes, item.cast, item.live_action]
                for item in catalog.values()
            ],
        })
        archive.unlink()
    print(f"Bangumi Archive: {anime_count:,} anime and {live_count:,} live-action entries", flush=True)
    return catalog, source


def xml_field(node, name):
    element = node.find(name)
    if element is None:
        raise ValueError(f"Syoboi TitleItem is missing {name}")
    return element.text or ""


def syoboi_episodes(raw):
    return tuple(sorted({
        (int(match[1]), match[2].strip())
        for match in re.finditer(r"(?m)^\*([0-9]{1,4})\*([^\r\n]+)", raw)
        if int(match[1]) > 0 and match[2].strip()
    }))


def syoboi_comment_evidence(comment):
    links, cast = set(), set()
    section = ""
    for raw in comment.splitlines():
        line = raw.strip()
        if line.startswith("*"):
            section = line.lstrip("*").strip()
        elif section == "リンク":
            for label, url in COMMENT_LINK.findall(line):
                if re.search(r"原作|漫画|小説|検索|Wikipedia", label, re.IGNORECASE):
                    continue
                if site := website_key(url):
                    links.add(site)
        elif section.startswith(("キャスト", "声の出演")) and line.startswith(":"):
            role, separator, voice = line[1:].partition(":")
            if separator and role.strip() and voice.strip():
                cast.update((role.strip(), actor.strip()) for actor in re.split(r"[、／]", voice) if actor.strip())
    return tuple(sorted(links)), tuple(sorted(cast))


def parse_syoboi(content, with_details):
    root = ElementTree.fromstring(content)
    items = root.find("TitleItems")
    code = root.findtext("Result/Code")
    if root.tag != "TitleLookupResponse" or code != "200" or items is None:
        raise ValueError(f"Invalid Syoboi TitleLookup response (Result/Code: {code})")
    titles = {}
    for node in items:
        tid = int(xml_field(node, "TID"))
        if node.tag != "TitleItem" or tid <= 0 or tid in titles or int(node.attrib.get("id", 0)) != tid:
            raise ValueError(f"Invalid or repeated Syoboi TID: {tid}")
        title = xml_field(node, "Title")
        updated = xml_field(node, "LastUpdate")
        datetime.strptime(updated, "%Y-%m-%d %H:%M:%S")
        if not with_details:
            titles[tid] = (title, updated)
            continue
        year = int(xml_field(node, "FirstYear") or "0")
        month = int(xml_field(node, "FirstMonth") or "0")
        category = int(xml_field(node, "Cat"))
        if year < 0 or year > 9999 or not 0 <= month <= 12 or category < 0:
            raise ValueError(f"Invalid date or category for Syoboi TID {tid}")
        comment = xml_field(node, "Comment")
        websites = {
            site for url in OFFICIAL_COMMENT.findall(comment)
            if (site := website_key(url))
        }
        english_title = xml_field(node, "TitleEN").strip()
        episodes = syoboi_episodes(xml_field(node, "SubTitles"))
        short_title = xml_field(node, "ShortTitle").strip()
        keywords = tuple(sorted({key.strip() for key in xml_field(node, "Keywords").split(",") if key.strip()}))
        titles[tid] = SyoboiTitle(
            tid, title, updated, year, month, category, tuple(sorted(websites)), english_title,
            episodes, short_title, keywords, *syoboi_comment_evidence(comment),
        )
    return titles


def syoboi_lookup(ids, with_details=False):
    fields = "TID,Title,LastUpdate"
    if with_details:
        fields += ",FirstYear,FirstMonth,Cat,Comment,TitleEN,SubTitles,ShortTitle,Keywords"
    query = urlencode({"Command": "TitleLookup", "TID": ids, "Fields": fields})
    source = request(f"{SYOBOI_DB}?{query}", lambda response: read_limited(response, 8_000_000), interval=1.1)
    return parse_syoboi(source, with_details)


def write_cache(path, content):
    temporary = path.with_suffix(".tmp")
    try:
        temporary.write_text(json.dumps(content, ensure_ascii=False) + "\n", encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def merge_syoboi(index, previous, lookup=syoboi_lookup):
    # The full lightweight index detects removals and compares LastUpdate in situ,
    # including after a missed schedule. Request details only for changed/new IDs.
    result = previous
    for attempt in range(3):
        if len(index) < MIN_SYOBOI_TITLES:
            raise ValueError(f"Syoboi index returned only {len(index)} titles")
        if previous and len(index.keys() & previous.keys()) * 100 < len(previous) * 95:
            raise ValueError("Syoboi index unexpectedly dropped more than 5% of cached TIDs")
        result = {
            tid: item for tid, item in result.items()
            if index.get(tid) == (item.title, item.updated)
        }
        pending = sorted(index.keys() - result.keys())
        print(f"Syoboi: {len(index):,} TIDs, {len(pending):,} details to fetch", flush=True)
        for start in range(0, len(pending), 100):
            batch = pending[start:start + 100]
            details = lookup(",".join(map(str, batch)), with_details=True)
            if details.keys() - set(batch):
                raise ValueError("Syoboi detail batch returned unrequested TIDs")
            result.update(details)
            if (start + len(batch)) % 1000 == 0 or len(result) == len(index):
                print(f"Syoboi details: {len(result):,}/{len(index):,}", flush=True)
        if result.keys() == index.keys() and all(
            index[tid] == (item.title, item.updated) for tid, item in result.items()
        ):
            return result
        if attempt < 2:
            print("Syoboi changed during collection or omitted details; reconciling against a fresh index", flush=True)
            index = lookup("*")
    raise ValueError("Syoboi details did not converge with the full title index")


def read_syoboi(cache, offline=False):
    path = cache / "syoboi-v4.json"
    saved = json.loads(path.read_bytes()) if path.exists() else None
    previous = {
        row[0]: SyoboiTitle(
            *row[:6], tuple(row[6]), row[7], tuple(tuple(part) for part in row[8]),
            row[9], tuple(row[10]), tuple(row[11]), tuple(tuple(role) for role in row[12]),
        )
        for row in saved["titles"]
    } if saved else {}
    if offline:
        if len(previous) < MIN_SYOBOI_TITLES:
            raise ValueError("Full Syoboi collection missing from local cache")
        return previous, saved["last_update"]

    titles = merge_syoboi(syoboi_lookup("*"), previous)
    if titles != previous:
        write_cache(path, {
            "last_update": max(item.updated for item in titles.values()),
            "titles": [
                [item.id, item.title, item.updated, item.year, item.month, item.category,
                 item.websites, item.english_title, item.episodes, item.short_title,
                 item.keywords, item.links, item.cast]
                for item in sorted(titles.values(), key=lambda item: item.id)
            ],
        })
    return titles, max(item.updated for item in titles.values())
