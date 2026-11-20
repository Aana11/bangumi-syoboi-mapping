"""Read only the fields needed from the two public upstreams."""

import hashlib
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from xml.etree import ElementTree
from zipfile import ZipFile

ARCHIVE_INDEX = "https://raw.githubusercontent.com/bangumi/Archive/master/aux/latest.json"
SYOBOI_DB = "https://cal.syoboi.jp/db"
USER_AGENT = "BangumiSyoboiMapping/0.1 (+https://github.com/Aana11/bangumi-syoboi-mapping)"
SYOBOI_CATEGORIES = frozenset({1, 7, 10})
MIN_BANGUMI_ANIME = 10_000
MIN_SYOBOI_TITLES = 5_000


@dataclass(frozen=True)
class BangumiTitle:
    id: int
    title: str
    month: str | None


@dataclass(frozen=True)
class SyoboiTitle:
    id: int
    title: str
    updated: str
    year: int
    month: int
    category: int

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


def extract_anime(archive):
    anime = {}
    with ZipFile(archive) as dump, dump.open("subject.jsonlines") as subjects:
        for line in subjects:
            item = json.loads(line)
            if item["type"] != 2:
                continue
            subject_id, title = item["id"], item["name"]
            if type(subject_id) is not int or subject_id <= 0 or subject_id in anime or not isinstance(title, str):
                raise ValueError("Bangumi Archive contains an invalid or repeated anime entry")
            anime[subject_id] = BangumiTitle(subject_id, title, bangumi_month(item["date"]))
    return anime


def archive_source(metadata):
    return {
        "archive": metadata["name"],
        "sha256": metadata["digest"].removeprefix("sha256:"),
        "created_at": metadata["created_at"],
    }


def read_bangumi(cache, offline=False):
    path = cache / "bangumi-anime-v1.json"
    saved = json.loads(path.read_bytes()) if path.exists() else None
    if offline and not saved:
        raise ValueError("Bangumi anime cache missing; run an online update first")
    metadata = None if offline else json.loads(request(ARCHIVE_INDEX, lambda response: read_limited(response, 128_000)))
    source = saved["source"] if offline else archive_source(metadata)
    if saved and saved["source"] == source:
        anime = {row[0]: BangumiTitle(*row) for row in saved["titles"]}
        if len(anime) < MIN_BANGUMI_ANIME or len(anime) != len(saved["titles"]):
            raise ValueError("Bangumi anime cache is incomplete or contains duplicates")
    else:
        archive = download_archive(metadata, cache)
        anime = extract_anime(archive)
        if len(anime) < MIN_BANGUMI_ANIME:
            raise ValueError(f"Bangumi Archive returned only {len(anime)} anime entries")
        write_cache(path, {
            "source": source,
            "titles": [[item.id, item.title, item.month] for item in anime.values()],
        })
        archive.unlink()
    print(f"Bangumi Archive: {len(anime):,} anime", flush=True)
    return anime, source


def xml_field(node, name):
    element = node.find(name)
    if element is None:
        raise ValueError(f"Syoboi TitleItem is missing {name}")
    return element.text or ""


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
        titles[tid] = SyoboiTitle(tid, title, updated, year, month, category)
    return titles


def syoboi_lookup(ids, with_details=False):
    fields = "TID,Title,LastUpdate"
    if with_details:
        fields += ",FirstYear,FirstMonth,Cat"
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
    path = cache / "syoboi-v1.json"
    saved = json.loads(path.read_bytes()) if path.exists() else None
    previous = {row[0]: SyoboiTitle(*row) for row in saved["titles"]} if saved else {}
    if offline:
        if len(previous) < MIN_SYOBOI_TITLES:
            raise ValueError("Full Syoboi collection missing from local cache")
        return previous, saved["last_update"]

    titles = merge_syoboi(syoboi_lookup("*"), previous)
    if titles != previous:
        write_cache(path, {
            "last_update": max(item.updated for item in titles.values()),
            "titles": [
                [item.id, item.title, item.updated, item.year, item.month, item.category]
                for item in sorted(titles.values(), key=lambda item: item.id)
            ],
        })
    return titles, max(item.updated for item in titles.values())
