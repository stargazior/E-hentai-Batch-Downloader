#!/usr/bin/env python3
"""
Standalone E-Hentai/ExHentai batch downloader.

This is intentionally independent from the Android project. It uses only the
Python standard library, reads cookies from the environment or a cookie file,
and downloads the normal image shown on each page by default.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import mimetypes
import os
import random
import re
import socket
import struct
import sys
import threading
import time
import zipfile
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone
from http.client import HTTPException, IncompleteRead
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urljoin, urlparse, urlunparse
from urllib.request import ProxyHandler, Request, build_opener


GALLERY_RE = re.compile(
    r"https?://(?:e-hentai\.org|exhentai\.org)/g/(?P<gid>\d+)/(?P<token>[0-9a-f]+)/?",
    re.IGNORECASE,
)
PAGE_LINK_RE = re.compile(
    r"""href=["'](?P<url>[^"']*/s/(?P<ptoken>[A-Za-z0-9_-]+)/(?P<gid>\d+)-(?P<page>\d+)[^"']*)["']""",
    re.IGNORECASE,
)
DETAIL_RE = re.compile(
    r'var\s+gid\s*=\s*(?P<gid>\d+);.+?var\s+token\s*=\s*"(?P<token>[0-9a-f]+)"',
    re.IGNORECASE | re.DOTALL,
)
PAGES_RE = re.compile(
    r"<tr><td[^>]*>\s*Length:\s*</td><td[^>]*>\s*([\d,]+)\s+pages?\s*</td></tr>",
    re.IGNORECASE,
)
TITLE_RE = re.compile(r'<h1[^>]+id=["\']gn["\'][^>]*>(.*?)</h1>', re.IGNORECASE | re.DOTALL)
IMAGE_RE_LIST = [
    re.compile(r'<img[^>]+id=["\']img["\'][^>]+src=["\']([^"\']+)["\']', re.IGNORECASE),
    re.compile(r'<img[^>]*src=["\']([^"\']+)["\'][^>]*style', re.IGNORECASE),
]
SKIP_HATH_RE = re.compile(r"""onclick=["']return\s+nl\(['"]?([^'")]+)['"]?\)""", re.IGNORECASE)
ORIGIN_RE = re.compile(r"""<a\s+href=["']([^"']*fullimg[^"']*)["']""", re.IGNORECASE)
OTHER_IMAGE_RE = re.compile(r"""onclick=["']prompt\('Copy the URL below\.',\s*'([^']+)'""", re.IGNORECASE)
SHOW_KEY_RE = re.compile(r'var\s+showkey\s*=\s*"([0-9a-z]+)"', re.IGNORECASE)
ARCHIVER_FORM_RE = re.compile(r"<form\b(?P<attrs>[^>]*)>(?P<body>.*?)</form>", re.IGNORECASE | re.DOTALL)
ARCHIVER_INPUT_RE = re.compile(r"<input\b(?P<attrs>[^>]*)>", re.IGNORECASE | re.DOTALL)
HTML_ATTR_RE = re.compile(
    r"""([A-Za-z_:][\w:.-]*)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+))""",
    re.IGNORECASE,
)
ARCHIVER_CONTINUE_RE = re.compile(r"""document\.location\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
ARCHIVER_DOWNLOAD_LINK_RE = re.compile(
    r"""href=["']([^"']+)["'][^>]*>\s*Click\s+Here\s+To\s+Start\s+Downloading""",
    re.IGNORECASE | re.DOTALL,
)
CONTENT_RANGE_RE = re.compile(r"bytes\s+(\d+)-(\d+)/(\d+|\*)", re.IGNORECASE)
TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")
META_CHARSET_RE = re.compile(
    br"<meta[^>]+charset=[\"']?\s*([A-Za-z0-9._-]+)",
    re.IGNORECASE,
)
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
APP_VERSION = "0.6.5"
PROGRESS_PREFIX = "EH_PROGRESS\t"
DEFAULT_ARCHIVE_CONNECTIONS = 8
ARCHIVE_MIN_PART_SIZE = 1024 * 1024
NETWORK_READ_CHUNK_SIZE = 1024 * 1024
SMALL_FILE_BUFFER_LIMIT = 16 * 1024 * 1024
CATEGORY_BITS = {
    "misc": 0x001,
    "doujinshi": 0x002,
    "manga": 0x004,
    "artistcg": 0x008,
    "gamecg": 0x010,
    "imageset": 0x020,
    "cosplay": 0x040,
    "asianporn": 0x080,
    "non-h": 0x100,
    "western": 0x200,
}
CATEGORY_ALIASES = {
    "artist cg": "artistcg",
    "artist-cg": "artistcg",
    "artist_cg": "artistcg",
    "game cg": "gamecg",
    "game-cg": "gamecg",
    "game_cg": "gamecg",
    "image set": "imageset",
    "image-set": "imageset",
    "image_set": "imageset",
    "asian porn": "asianporn",
    "asian-porn": "asianporn",
    "asian_porn": "asianporn",
    "non h": "non-h",
    "nonh": "non-h",
    "non_h": "non-h",
}
ALL_CATEGORY_BITS = 0x3FF
BUILTIN_HOSTS = {
    "e-hentai.org": [
        "104.20.18.168",
        "104.20.19.168",
        "172.66.132.196",
        "172.66.140.62",
        "172.67.2.238",
    ],
    "repo.e-hentai.org": ["104.20.18.168", "104.20.19.168", "172.67.2.238"],
    "forums.e-hentai.org": ["172.66.132.196", "172.66.140.62"],
    "upld.e-hentai.org": ["89.149.221.236", "95.211.208.236"],
    "ehgt.org": [
        "109.236.85.28",
        "62.112.8.21",
        "89.39.106.43",
        "2a00:7c80:0:123::3a85",
        "2a00:7c80:0:12d::38a1",
        "2a00:7c80:0:13b::37a4",
    ],
    "exhentai.org": [
        "178.175.128.251",
        "178.175.128.252",
        "178.175.128.253",
        "178.175.128.254",
        "178.175.129.251",
        "178.175.129.252",
        "178.175.129.253",
        "178.175.129.254",
        "178.175.132.19",
        "178.175.132.20",
        "178.175.132.21",
        "178.175.132.22",
    ],
    "upld.exhentai.org": ["178.175.132.22", "178.175.129.254", "178.175.128.254"],
    "s.exhentai.org": [
        "178.175.129.253",
        "178.175.129.254",
        "178.175.128.253",
        "178.175.128.254",
        "178.175.132.21",
        "178.175.132.22",
    ],
}
_ORIGINAL_GETADDRINFO = socket.getaddrinfo
_HOSTS_INSTALLED = False
_PRINT_LOCK = threading.Lock()
_DEVNULL_STREAM: Optional[object] = None
NETWORK_ERRORS = (HTTPError, URLError, TimeoutError, OSError, HTTPException)
RECOVERABLE_ERRORS = NETWORK_ERRORS + (ValueError,)


class DownloadIntegrityError(OSError):
    pass


class DownloadStageError(OSError):
    pass


@dataclass
class Gallery:
    gid: int
    token: str
    title: str
    url: str


@dataclass
class GalleryRunResult:
    gallery: Gallery
    ok: bool
    error: Optional[str] = None
    download_mode: Optional[str] = None
    archive_path: Optional[str] = None
    archive_cost: Optional[str] = None
    archive_size: Optional[str] = None
    skipped: bool = False


@dataclass
class GalleryDetail:
    gid: int
    token: str
    title: str
    pages: int
    page_tokens: Dict[int, str]
    preview_pages: int


@dataclass
class ArchiveOption:
    kind: str
    url: str
    dltype: str
    dlcheck: str
    cost: str = ""
    size: str = ""


@dataclass
class ArchiveInfo:
    url: str
    funds: str = ""
    original: Optional[ArchiveOption] = None
    resample: Optional[ArchiveOption] = None

    def option(self, mode: str) -> ArchiveOption:
        if mode == "archive-original":
            selected = self.original
        elif mode == "archive-resample":
            selected = self.resample
        else:
            raise ValueError(f"Unsupported archive mode: {mode}")
        if selected is None:
            label = "original" if mode == "archive-original" else "resample"
            raise ValueError(f"Archive {label} option is not available.")
        return selected


@dataclass
class ArchiveDownloadResult:
    path: Path
    cost: str = ""
    size: str = ""
    skipped: bool = False


@dataclass
class PageInfo:
    image_url: str
    show_key: Optional[str] = None
    skip_hath_key: Optional[str] = None
    origin_image_url: Optional[str] = None


@dataclass
class FetchResult:
    body: str
    final_url: str
    headers: object


class AnchorParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.anchors: List[Dict[str, object]] = []
        self._stack: List[Dict[str, object]] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        if tag.lower() == "a":
            attrs_dict = {key.lower(): value or "" for key, value in attrs}
            self._stack.append({"attrs": attrs_dict, "text": []})

    def handle_data(self, data: str) -> None:
        if self._stack:
            self._stack[-1]["text"].append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or not self._stack:
            return
        anchor = self._stack.pop()
        text = normalize_space("".join(anchor["text"]))
        attrs = anchor["attrs"]
        href = attrs.get("href", "")
        self.anchors.append({"attrs": attrs, "href": href, "text": text})


def normalize_space(value: str) -> str:
    return SPACE_RE.sub(" ", unescape(value)).strip()


def strip_tags(value: str) -> str:
    return normalize_space(TAG_RE.sub(" ", value))


def parse_html_attrs(value: str) -> Dict[str, str]:
    attrs: Dict[str, str] = {}
    for match in HTML_ATTR_RE.finditer(value):
        raw = match.group(2) if match.group(2) is not None else match.group(3)
        if raw is None:
            raw = match.group(4) or ""
        attrs[match.group(1).lower()] = unescape(raw)
    return attrs


def compact_archive_text(value: str) -> str:
    text = strip_tags(value)
    # The archiver page has submit button labels in the middle of cost/size blocks.
    text = re.sub(r"Download\s+(?:Original|Resample)\s+Archive", " ", text, flags=re.IGNORECASE)
    return normalize_space(text)


def parse_archive_context_value(context: str, label: str) -> str:
    text = compact_archive_text(context)
    pattern = re.compile(
        rf"{re.escape(label)}\s*:?\s*(.+?)(?=\s+(?:Download Cost|Estimated Size|Funds?)\s*:|$)",
        re.IGNORECASE,
    )
    match = pattern.search(text)
    return normalize_space(match.group(1)) if match else ""


def parse_archive_info(html: str, base_url: str) -> ArchiveInfo:
    forms = list(ARCHIVER_FORM_RE.finditer(html))
    info = ArchiveInfo(url=base_url, funds=parse_archive_context_value(html[:3000], "Funds"))
    for index, form_match in enumerate(forms):
        form_attrs = parse_html_attrs(form_match.group("attrs"))
        action = form_attrs.get("action")
        if not action:
            continue

        inputs: Dict[str, str] = {}
        for input_match in ARCHIVER_INPUT_RE.finditer(form_match.group("body")):
            attrs = parse_html_attrs(input_match.group("attrs"))
            name = attrs.get("name")
            if name:
                inputs[name.lower()] = attrs.get("value", "")

        dltype = inputs.get("dltype", "").strip()
        dlcheck = inputs.get("dlcheck", "").strip()
        if not dltype or not dlcheck:
            continue
        if dltype == "org":
            kind = "original"
        elif dltype == "res":
            kind = "resample"
        else:
            continue

        previous_end = forms[index - 1].end() if index else max(0, form_match.start() - 1500)
        next_start = forms[index + 1].start() if index + 1 < len(forms) else min(len(html), form_match.end() + 1500)
        cost_context = html[previous_end : form_match.start()] + form_match.group("body")
        size_context = form_match.group("body") + html[form_match.end() : next_start]
        option = ArchiveOption(
            kind=kind,
            url=urljoin(base_url, action),
            dltype=dltype,
            dlcheck=dlcheck,
            cost=parse_archive_context_value(cost_context, "Download Cost"),
            size=parse_archive_context_value(size_context, "Estimated Size"),
        )
        if kind == "original":
            info.original = option
        elif kind == "resample":
            info.resample = option
    return info


def parse_archive_continue_url(html: str, base_url: str) -> Optional[str]:
    match = ARCHIVER_CONTINUE_RE.search(html)
    if not match:
        return None
    return urljoin(base_url, unescape(match.group(1)).strip())


def parse_archive_final_download_url(html: str, base_url: str) -> Optional[str]:
    for anchor in parse_anchors(html):
        text = normalize_space(str(anchor.get("text") or ""))
        href = str(anchor.get("href") or "")
        if href and "Click Here To Start Downloading".lower() in text.lower():
            return urljoin(base_url, href)
    match = ARCHIVER_DOWNLOAD_LINK_RE.search(html)
    if match:
        return urljoin(base_url, unescape(match.group(1)).strip())
    return None


def sanitize_filename(value: str, default: str = "untitled") -> str:
    value = strip_tags(value)
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", value)
    value = value.rstrip(" .")
    if not value:
        return default
    return value[:160]


def current_time_text() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def sanitize_state_name(value: str, default: str = "default") -> str:
    value = sanitize_filename(value, default)
    value = SPACE_RE.sub("_", value).strip("._")
    return (value or default)[:100]


def resolve_job_name(args: argparse.Namespace) -> str:
    explicit = getattr(args, "job_name", None)
    if explicit and str(explicit).strip():
        return str(explicit).strip()
    for key in ("search", "uploader", "tag"):
        values = source_values(args, key)
        if values:
            return ";".join(values)[:80]
    urls = source_values(args, "url")
    if urls:
        match = GALLERY_RE.search(urls[0])
        if match:
            return f"gallery-{match.group('gid')}"
        return "url"
    return "default"


def state_dir_for(output: Path) -> Path:
    return output / ".eh_batch_state"


def latest_state_path(output: Path, job_name: str) -> Path:
    return state_dir_for(output) / f"{sanitize_state_name(job_name)}-latest.json"


def failures_text_path(output: Path, job_name: str) -> Path:
    return state_dir_for(output) / f"{sanitize_state_name(job_name)}-failures.txt"


def history_path(output: Path, job_name: str) -> Path:
    return state_dir_for(output) / f"{sanitize_state_name(job_name)}-history.jsonl"


def gallery_to_dict(gallery: Gallery) -> Dict[str, object]:
    return {
        "gid": gallery.gid,
        "token": gallery.token,
        "title": gallery.title,
        "url": gallery.url,
    }


def gallery_from_dict(data: object) -> Gallery:
    if not isinstance(data, dict):
        raise ValueError("Invalid gallery record")
    return Gallery(
        gid=int(data["gid"]),
        token=str(data["token"]),
        title=str(data.get("title") or data["gid"]),
        url=str(data["url"]),
    )


def successful_result_to_dict(result: GalleryRunResult) -> Dict[str, object]:
    data = gallery_to_dict(result.gallery)
    if result.download_mode:
        data["download_mode"] = result.download_mode
    if result.archive_path:
        data["archive_path"] = result.archive_path
    if result.archive_cost:
        data["archive_cost"] = result.archive_cost
    if result.archive_size:
        data["archive_size"] = result.archive_size
    if result.skipped:
        data["skipped"] = result.skipped
    return data


def write_json_atomic(path: Path, data: Dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".part")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def cleanup_parts_dir(path: Path) -> None:
    try:
        for child in path.iterdir():
            if child.is_file():
                child.unlink()
        path.rmdir()
    except OSError:
        pass


def append_query(url: str, **items: object) -> str:
    parsed = urlparse(url)
    query = parsed.query
    extra = urlencode({key: value for key, value in items.items() if value is not None})
    if query and extra:
        query = query + "&" + extra
    elif extra:
        query = extra
    return urlunparse(parsed._replace(query=query))


def parse_cookie_source(raw_cookie: Optional[str], cookie_file: Optional[Path]) -> str:
    if raw_cookie:
        return raw_cookie.strip()

    env_cookie = os.environ.get("EH_COOKIES")
    if env_cookie:
        return env_cookie.strip()

    cookie_path = cookie_file or (Path(os.environ["EH_COOKIE_FILE"]) if os.environ.get("EH_COOKIE_FILE") else None)
    if not cookie_path:
        return ""

    text = cookie_path.read_text(encoding="utf-8").strip()
    if not text:
        return ""

    if "\t" in text:
        pairs = []
        for line in text.splitlines():
            if not line or line.startswith("#"):
                continue
            fields = line.split("\t")
            if len(fields) >= 7:
                pairs.append(f"{fields[5]}={fields[6]}")
        return "; ".join(pairs)

    if text.startswith("{"):
        data = json.loads(text)
        if isinstance(data, dict):
            return "; ".join(f"{key}={value}" for key, value in data.items())

    return text


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="replace")


def fallback_output_stream() -> object:
    global _DEVNULL_STREAM
    for stream in (sys.stdout, sys.__stdout__, sys.stderr, sys.__stderr__):
        if stream is not None:
            return stream
    if _DEVNULL_STREAM is None:
        _DEVNULL_STREAM = open(os.devnull, "w", encoding="utf-8")
    return _DEVNULL_STREAM


def safe_print(*values: object, file: Optional[object] = None) -> None:
    stream = file if file is not None else fallback_output_stream()
    with _PRINT_LOCK:
        print(*values, file=stream, flush=True)


def emit_progress(args: argparse.Namespace, **items: object) -> None:
    if not getattr(args, "progress_json", False):
        return
    data = {key: value for key, value in items.items() if value is not None}
    safe_print(PROGRESS_PREFIX + json.dumps(data, ensure_ascii=False, separators=(",", ":")))


def format_bytes(value: Optional[int]) -> str:
    if value is None:
        return "?"
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    amount = float(value)
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(amount)} {unit}"
            return f"{amount:.2f} {unit}"
        amount /= 1024


class ProgressThrottle:
    def __init__(
        self,
        args: argparse.Namespace,
        gid: int,
        title: str,
        mode: str,
        interval: float = 0.5,
        connections: Optional[int] = None,
    ) -> None:
        self.args = args
        self.gid = gid
        self.title = title
        self.mode = mode
        self.interval = interval
        self.connections = connections
        self.last_emit = 0.0

    def __call__(self, bytes_done: int, bytes_total: Optional[int]) -> None:
        now = time.monotonic()
        if bytes_total is not None and bytes_done >= bytes_total:
            should_emit = True
        else:
            should_emit = now - self.last_emit >= self.interval
        if not should_emit:
            return
        self.last_emit = now
        percent = None if not bytes_total else round(bytes_done * 100.0 / bytes_total, 1)
        emit_progress(
            self.args,
            event="progress",
            gid=self.gid,
            title=self.title,
            mode=self.mode,
            status="Downloading",
            percent=percent,
            bytes_done=bytes_done,
            bytes_total=bytes_total,
            connections=self.connections,
        )


def install_builtin_hosts(enabled: bool) -> None:
    global _HOSTS_INSTALLED
    if not enabled or _HOSTS_INSTALLED:
        return

    def getaddrinfo_with_builtin_hosts(host: str, port: object, *args: object, **kwargs: object) -> list:
        normalized_host = host.lower().rstrip(".") if isinstance(host, str) else host
        ips = BUILTIN_HOSTS.get(normalized_host)
        if not ips:
            return _ORIGINAL_GETADDRINFO(host, port, *args, **kwargs)

        shuffled = list(ips)
        random.shuffle(shuffled)
        result = []
        for ip in shuffled:
            try:
                result.extend(_ORIGINAL_GETADDRINFO(ip, port, *args, **kwargs))
            except socket.gaierror:
                continue
        if result:
            return result
        return _ORIGINAL_GETADDRINFO(host, port, *args, **kwargs)

    socket.getaddrinfo = getaddrinfo_with_builtin_hosts
    _HOSTS_INSTALLED = True


def build_proxy_handler(proxy_mode: str, proxy_url: Optional[str]) -> ProxyHandler:
    if proxy_mode == "direct":
        return ProxyHandler({})
    if proxy_mode == "http":
        if not proxy_url:
            raise ValueError("HTTP proxy mode requires --proxy-url.")
        if "://" not in proxy_url:
            proxy_url = "http://" + proxy_url
        return ProxyHandler({"http": proxy_url, "https": proxy_url})
    return ProxyHandler()


def normalize_category_name(name: str) -> str:
    normalized = SPACE_RE.sub(" ", name.strip().lower())
    return CATEGORY_ALIASES.get(normalized, normalized)


def split_category_values(values: Optional[Iterable[str]]) -> List[str]:
    result: List[str] = []
    for value in values or []:
        for item in re.split(r"[,;]", value):
            item = item.strip()
            if item:
                result.append(item)
    return result


def split_multi_values(values: object, separators: str = r"[;\r\n]+") -> List[str]:
    if values in (None, ""):
        return []
    if isinstance(values, (list, tuple)):
        raw_values = [str(value) for value in values]
    else:
        raw_values = [str(values)]
    result: List[str] = []
    for value in raw_values:
        for item in re.split(separators, value):
            item = item.strip()
            if item:
                result.append(item)
    return result


def source_values(args: argparse.Namespace, key: str) -> List[str]:
    return split_multi_values(getattr(args, key, None))


def format_tag_query(prefix: str, value: str, exact: bool = True) -> str:
    """Build an E-Hentai search token from a tag namespace and value."""
    namespace = prefix.strip()
    tag_value = value.strip()
    if not namespace or not tag_value:
        raise ValueError("Tag prefix and value are required.")
    tag_value = tag_value.replace('\\', '\\\\').replace('"', '\\"')
    if exact and not tag_value.endswith("$"):
        tag_value += "$"
    if any(character.isspace() for character in tag_value):
        return f'{namespace}:"{tag_value}"'
    return f"{namespace}:{tag_value}"


def source_keys_with_values(args: argparse.Namespace) -> List[str]:
    return [key for key in ("url", "search", "uploader", "tag") if source_values(args, key)]


def included_category_mask(category_values: Optional[Iterable[str]]) -> Optional[int]:
    names = split_category_values(category_values)
    if not names:
        return None

    mask = 0
    unknown = []
    for name in names:
        normalized = normalize_category_name(name)
        bit = CATEGORY_BITS.get(normalized)
        if bit is None:
            unknown.append(name)
        else:
            mask |= bit
    if unknown:
        choices = ", ".join(CATEGORY_BITS)
        raise ValueError(f"Unknown category: {', '.join(unknown)}. Available categories: {choices}.")
    if mask == 0:
        raise ValueError("At least one category must be selected.")
    return mask


def f_cats_from_included_categories(category_values: Optional[Iterable[str]]) -> Optional[int]:
    mask = included_category_mask(category_values)
    if mask is None:
        return None
    return (~mask) & ALL_CATEGORY_BITS


def resolve_f_cats(args: argparse.Namespace) -> Optional[int]:
    raw_f_cats = getattr(args, "f_cats", None)
    category_values = getattr(args, "categories", None)
    if raw_f_cats is not None and category_values:
        raise ValueError("Use either --f-cats or --category/--categories, not both.")
    if raw_f_cats is not None:
        return raw_f_cats

    values = split_category_values(category_values)
    if len(values) == 1 and values[0].isdigit():
        return int(values[0])
    return f_cats_from_included_categories(values)


def is_retryable_error(exc: BaseException) -> bool:
    if isinstance(exc, HTTPError):
        return exc.code in {408, 429, 500, 502, 503, 504}
    return isinstance(exc, (URLError, TimeoutError, OSError, HTTPException))


def is_http_status(exc: BaseException, statuses: Iterable[int]) -> bool:
    status_set = set(statuses)
    current: Optional[BaseException] = exc
    while current is not None:
        if isinstance(current, HTTPError) and current.code in status_set:
            return True
        current = current.__cause__
    return False


def sleep_before_retry(attempt: int, retry_backoff: float, message: str) -> None:
    delay = retry_backoff * (2 ** attempt)
    if delay > 0:
        safe_print(f"retry {attempt + 1}: {message}; sleeping {delay:.1f}s", file=sys.stderr)
        time.sleep(delay)
    else:
        safe_print(f"retry {attempt + 1}: {message}", file=sys.stderr)


def decode_response_body(raw: bytes, headers: object) -> str:
    candidates: List[str] = []
    charset = None
    get_content_charset = getattr(headers, "get_content_charset", None)
    if get_content_charset:
        charset = get_content_charset()
    if charset:
        candidates.append(charset)

    meta_match = META_CHARSET_RE.search(raw[:4096])
    if meta_match:
        candidates.append(meta_match.group(1).decode("ascii", errors="ignore"))

    candidates.extend(["utf-8", "gb18030", "big5", "shift_jis", "cp932", "latin-1"])

    best_text = ""
    best_score = None
    seen = set()
    for encoding in candidates:
        normalized = encoding.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        try:
            text = raw.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            try:
                text = raw.decode(encoding, errors="replace")
            except LookupError:
                continue
        score = text.count("\ufffd") * 1000
        score += sum(1 for char in text if ord(char) < 32 and char not in "\r\n\t")
        if best_score is None or score < best_score:
            best_text = text
            best_score = score
            if score == 0 and normalized == "utf-8":
                break
    return best_text


def parse_anchors(html: str) -> List[Dict[str, object]]:
    parser = AnchorParser()
    parser.feed(html)
    return parser.anchors


def parse_gallery_links(html: str, base_url: str) -> List[Gallery]:
    galleries: List[Gallery] = []
    seen = set()
    for anchor in parse_anchors(html):
        href = urljoin(base_url, str(anchor.get("href") or ""))
        match = GALLERY_RE.search(href)
        if not match:
            continue
        gid = int(match.group("gid"))
        if gid in seen:
            continue
        seen.add(gid)
        title = normalize_space(str(anchor.get("text") or "")) or str(gid)
        galleries.append(Gallery(gid=gid, token=match.group("token"), title=title, url=href))
    return galleries


def parse_next_list_url(html: str, base_url: str) -> Optional[str]:
    for anchor in parse_anchors(html):
        attrs = anchor.get("attrs") or {}
        href = str(anchor.get("href") or "")
        text = normalize_space(str(anchor.get("text") or ""))
        anchor_id = str(attrs.get("id", "")).lower()
        if anchor_id in {"unext", "next"} and href:
            return urljoin(base_url, href)
        if text in {">", "Next", "Next >", "›"} and href:
            return urljoin(base_url, href)
    return None


def parse_page_tokens(html: str, base_url: str) -> Dict[int, str]:
    result: Dict[int, str] = {}
    for match in PAGE_LINK_RE.finditer(html):
        page = int(match.group("page")) - 1
        if page >= 0:
            result[page] = match.group("ptoken")
    return result


def parse_preview_pages(html: str) -> int:
    pages = [int(value) for value in re.findall(r"[?&]p=(\d+)", html)]
    return max(pages) + 1 if pages else 1


def parse_detail_html(html: str, url: str) -> GalleryDetail:
    detail_match = DETAIL_RE.search(html)
    url_match = GALLERY_RE.search(url)
    if detail_match:
        gid = int(detail_match.group("gid"))
        token = detail_match.group("token")
    elif url_match:
        gid = int(url_match.group("gid"))
        token = url_match.group("token")
    else:
        raise ValueError("Could not parse gallery gid/token")

    title_match = TITLE_RE.search(html)
    title = strip_tags(title_match.group(1)) if title_match else str(gid)

    pages_match = PAGES_RE.search(html)
    pages = int(pages_match.group(1).replace(",", "")) if pages_match else 0
    page_tokens = parse_page_tokens(html, url)
    return GalleryDetail(
        gid=gid,
        token=token,
        title=title,
        pages=pages,
        page_tokens=page_tokens,
        preview_pages=parse_preview_pages(html),
    )


def parse_page_html(html: str) -> PageInfo:
    image_url = ""
    for pattern in IMAGE_RE_LIST:
        match = pattern.search(html)
        if match:
            image_url = unescape(match.group(1)).strip()
            break
    show_key_match = SHOW_KEY_RE.search(html)
    skip_match = SKIP_HATH_RE.search(html)
    origin_url = None
    origin_match = ORIGIN_RE.search(html)
    if origin_match:
        origin_url = unescape(origin_match.group(1)).strip()
    other_match = OTHER_IMAGE_RE.search(html)
    other_image_url = unescape(other_match.group(1)).strip() if other_match else None
    if not image_url and other_image_url:
        image_url = other_image_url

    if not image_url:
        raise ValueError("Could not parse page image URL")

    return PageInfo(
        image_url=image_url,
        show_key=show_key_match.group(1) if show_key_match else None,
        skip_hath_key=unescape(skip_match.group(1)).strip() if skip_match else None,
        origin_image_url=origin_url,
    )


def parse_page_api_json(body: str) -> PageInfo:
    data = json.loads(body)
    if data.get("error"):
        raise ValueError(str(data["error"]))
    html = "\n".join(str(data.get(key) or "") for key in ("i3", "i6", "i7"))
    info = parse_page_html(html)
    return PageInfo(
        image_url=info.image_url,
        show_key=None,
        skip_hath_key=info.skip_hath_key,
        origin_image_url=info.origin_image_url,
    )


class EhClient:
    def __init__(
        self,
        site: str,
        cookie_header: str,
        timeout: float,
        retries: int,
        retry_backoff: float,
        proxy_mode: str,
        proxy_url: Optional[str],
    ) -> None:
        self.site = site
        self.host = "https://exhentai.org/" if site == "ex" else "https://e-hentai.org/"
        self.api_url = urljoin(self.host, "api.php")
        self.timeout = timeout
        self.retries = max(0, retries)
        self.retry_backoff = max(0.0, retry_backoff)
        self.cookie_header = cookie_header
        self.proxy_mode = proxy_mode
        self.proxy_url = proxy_url
        self.opener = build_opener(build_proxy_handler(proxy_mode, proxy_url))
        self.last_archive_connections = 1
        self.last_archive_fallback_reason = ""
        self.last_archive_probe_reason = ""

    @classmethod
    def from_args(cls, args: argparse.Namespace, cookie_header: str) -> "EhClient":
        return cls(
            args.site,
            cookie_header,
            timeout=args.timeout,
            retries=args.retries,
            retry_backoff=args.retry_backoff,
            proxy_mode=args.proxy_mode,
            proxy_url=args.proxy_url,
        )

    def clone(self) -> "EhClient":
        return EhClient(
            self.site,
            self.cookie_header,
            timeout=self.timeout,
            retries=self.retries,
            retry_backoff=self.retry_backoff,
            proxy_mode=self.proxy_mode,
            proxy_url=self.proxy_url,
        )

    def make_headers(self, referer: Optional[str] = None, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.8,zh-CN;q=0.6",
        }
        if self.cookie_header:
            headers["Cookie"] = self.cookie_header
        if referer:
            headers["Referer"] = referer
        if extra:
            headers.update(extra)
        return headers

    def fetch_text(
        self,
        url: str,
        referer: Optional[str] = None,
        data: Optional[bytes] = None,
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> FetchResult:
        last_error: Optional[BaseException] = None
        for attempt in range(self.retries + 1):
            req = Request(url, data=data, headers=self.make_headers(referer, extra_headers))
            try:
                with self.opener.open(req, timeout=self.timeout) as response:
                    status = getattr(response, "status", 200)
                    raw = response.read()
                    if status >= 500:
                        raise HTTPError(url, status, "server error", response.headers, None)
                    body = decode_response_body(raw, response.headers)
                    return FetchResult(body=body, final_url=response.geturl(), headers=response.headers)
            except NETWORK_ERRORS as exc:
                last_error = exc
                if attempt >= self.retries or not is_retryable_error(exc):
                    raise
                sleep_before_retry(attempt, self.retry_backoff, f"text request failed: {url}: {exc}")
        assert last_error is not None
        raise last_error

    def download_file(self, url: str, referer: str, target_base: Path, overwrite: bool) -> Path:
        last_error: Optional[BaseException] = None
        for attempt in range(self.retries + 1):
            req = Request(url, headers=self.make_headers(referer, {"Accept": "*/*"}))
            temp: Optional[Path] = None
            try:
                with self.opener.open(req, timeout=self.timeout) as response:
                    status = getattr(response, "status", 200)
                    if status >= 400:
                        raise HTTPError(url, status, "image download failed", response.headers, None)
                    extension = choose_extension(response.headers.get("Content-Type"), response.geturl())
                    target = target_base.with_suffix(extension)
                    if target.exists() and not overwrite and validate_image_file(target):
                        return target
                    if target.exists() and not overwrite:
                        target.unlink()
                    target.parent.mkdir(parents=True, exist_ok=True)
                    temp = target.with_suffix(target.suffix + ".part")
                    expected_size = parse_content_length(response.headers.get("Content-Length"))
                    received_size = 0
                    with temp.open("wb") as handle:
                        if expected_size is not None and expected_size <= SMALL_FILE_BUFFER_LIMIT:
                            content = response.read()
                            handle.write(content)
                            received_size = len(content)
                        else:
                            while True:
                                chunk = response.read(NETWORK_READ_CHUNK_SIZE)
                                if not chunk:
                                    break
                                handle.write(chunk)
                                received_size += len(chunk)
                    if expected_size is not None and received_size < expected_size:
                        raise DownloadIntegrityError(
                            f"incomplete image: got {received_size} bytes, expected {expected_size} bytes"
                        )
                    if not validate_image_file(temp):
                        raise DownloadIntegrityError(f"incomplete or invalid image file: {temp}")
                    temp.replace(target)
                    return target
            except NETWORK_ERRORS as exc:
                last_error = exc
                if temp and temp.exists():
                    try:
                        temp.unlink()
                    except OSError:
                        pass
                if attempt >= self.retries or not is_retryable_error(exc):
                    raise
                sleep_before_retry(attempt, self.retry_backoff, f"image request failed: {url}: {exc}")
        assert last_error is not None
        raise last_error

    def list_url_from_args(self, args: argparse.Namespace) -> str:
        urls = self.list_urls_from_args(args)
        return urls[0] if urls else self.host

    def list_urls_from_args(self, args: argparse.Namespace) -> List[str]:
        f_cats = resolve_f_cats(args)

        def with_categories(url: str) -> str:
            return append_query(url, f_cats=f_cats) if f_cats is not None else url

        urls = [with_categories(url) for url in source_values(args, "url")]

        searches = source_values(args, "search")
        if searches:
            urls.extend(with_categories(append_query(self.host, f_search=value)) for value in searches)

        uploaders = source_values(args, "uploader")
        if uploaders:
            urls.extend(
                with_categories(urljoin(self.host, "uploader/" + quote(uploader))) for uploader in uploaders
            )

        tags = source_values(args, "tag")
        if tags:
            urls.extend(with_categories(urljoin(self.host, "tag/" + quote(tag))) for tag in tags)

        return urls or [with_categories(self.host)]

    def iter_galleries(self, start_url: str, max_list_pages: int) -> Iterable[Gallery]:
        url = start_url
        seen = set()
        for page_no in range(max_list_pages):
            result = self.fetch_text(url)
            galleries = parse_gallery_links(result.body, result.final_url)
            for gallery in galleries:
                if gallery.gid in seen:
                    continue
                seen.add(gallery.gid)
                yield gallery
            next_url = parse_next_list_url(result.body, result.final_url)
            if not next_url or next_url == url:
                break
            url = next_url

    def collect_detail(self, gallery: Gallery) -> GalleryDetail:
        first = self.fetch_text(gallery.url, referer=self.host)
        detail = parse_detail_html(first.body, first.final_url)
        if gallery.title and detail.title == str(detail.gid):
            detail.title = gallery.title

        if detail.pages and len(detail.page_tokens) >= detail.pages:
            return detail

        expected_preview_pages = detail.preview_pages
        if detail.page_tokens and detail.pages:
            per_page = max(detail.page_tokens.keys()) + 1
            if per_page > 0:
                expected_preview_pages = max(expected_preview_pages, (detail.pages + per_page - 1) // per_page)

        for preview_index in range(1, expected_preview_pages):
            page_url = append_query(gallery.url, p=preview_index)
            page = self.fetch_text(page_url, referer=gallery.url)
            detail.page_tokens.update(parse_page_tokens(page.body, page.final_url))
            if detail.pages and len(detail.page_tokens) >= detail.pages:
                break

        if not detail.pages:
            detail.pages = max(detail.page_tokens.keys(), default=-1) + 1
        return detail

    def collect_basic_detail(self, gallery: Gallery) -> GalleryDetail:
        first = self.fetch_text(gallery.url, referer=self.host)
        detail = parse_detail_html(first.body, first.final_url)
        if gallery.title and detail.title == str(detail.gid):
            detail.title = gallery.title
        return detail

    def page_url(self, gid: int, index: int, ptoken: str) -> str:
        return urljoin(self.host, f"s/{ptoken}/{gid}-{index + 1}")

    def detail_url(self, gid: int, token: str) -> str:
        return urljoin(self.host, f"g/{gid}/{token}/")

    def archive_url(self, gid: int, token: str) -> str:
        return urljoin(self.host, f"archiver.php?gid={gid}&token={token}")

    def fetch_archive_info(self, detail: GalleryDetail) -> ArchiveInfo:
        archive_url = self.archive_url(detail.gid, detail.token)
        result = self.fetch_text(archive_url, referer=self.detail_url(detail.gid, detail.token))
        return parse_archive_info(result.body, result.final_url)

    def request_archive_download_url(self, archive_info: ArchiveInfo, option: ArchiveOption) -> str:
        payload = urlencode({"dltype": option.dltype, "dlcheck": option.dlcheck}).encode("utf-8")
        result = self.fetch_text(
            option.url,
            referer=archive_info.url,
            data=payload,
            extra_headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Origin": self.host.rstrip("/"),
            },
        )
        continue_url = parse_archive_continue_url(result.body, result.final_url)
        if not continue_url:
            direct_url = parse_archive_final_download_url(result.body, result.final_url)
            if direct_url:
                return direct_url
            raise ValueError("Could not parse archive continue URL.")

        continue_result = self.fetch_text(continue_url, referer=option.url)
        download_url = parse_archive_final_download_url(continue_result.body, continue_result.final_url)
        if not download_url:
            raise ValueError("Could not parse archive download URL.")
        return download_url

    def fetch_page_info(
        self,
        gid: int,
        token: str,
        index: int,
        ptoken: str,
        previous_ptoken: Optional[str],
        show_key: Optional[str],
        prefer_api: bool,
    ) -> Tuple[PageInfo, Optional[str]]:
        page_url = self.page_url(gid, index, ptoken)
        referer = self.detail_url(gid, token)

        if prefer_api and show_key:
            payload = json.dumps(
                {"method": "showpage", "gid": gid, "page": index + 1, "imgkey": ptoken, "showkey": show_key}
            ).encode("utf-8")
            api_referer = self.page_url(gid, index - 1, previous_ptoken) if previous_ptoken and index > 0 else None
            try:
                result = self.fetch_text(
                    self.api_url,
                    referer=api_referer,
                    data=payload,
                    extra_headers={"Content-Type": "application/json", "Origin": self.host.rstrip("/")},
                )
                return parse_page_api_json(result.body), show_key
            except Exception:
                show_key = None

        html = self.fetch_text(page_url, referer=referer).body
        info = parse_page_html(html)
        return info, info.show_key or show_key

    def resolve_original_url(self, info: PageInfo, referer: str) -> Optional[str]:
        if not info.origin_image_url:
            return None
        url = info.origin_image_url
        if info.skip_hath_key:
            url = append_query(url, nl=info.skip_hath_key)
        last_error: Optional[BaseException] = None
        for attempt in range(self.retries + 1):
            req = Request(url, headers=self.make_headers(referer, {"Accept": "*/*"}), method="GET")
            try:
                with self.opener.open(req, timeout=self.timeout) as response:
                    status = getattr(response, "status", 200)
                    if status >= 500:
                        raise HTTPError(url, status, "server error", response.headers, None)
                    return response.geturl()
            except NETWORK_ERRORS as exc:
                last_error = exc
                if attempt >= self.retries or not is_retryable_error(exc):
                    raise
                sleep_before_retry(attempt, self.retry_backoff, f"original redirect failed: {url}: {exc}")
        assert last_error is not None
        raise last_error

    def download_archive_file(
        self,
        url: str,
        referer: str,
        target: Path,
        overwrite: bool,
        connections: int = 1,
        progress_callback: Optional[Callable[[int, Optional[int]], None]] = None,
        connection_callback: Optional[Callable[[int, str], None]] = None,
    ) -> Tuple[Path, bool]:
        self.last_archive_connections = 1
        self.last_archive_fallback_reason = ""
        if target.exists() and not overwrite:
            if validate_zip_file(target):
                return target, True
            target.unlink()

        fell_back_to_single = False
        if connections > 1:
            try:
                return self.download_archive_file_segmented(
                    url,
                    referer,
                    target,
                    connections=connections,
                    progress_callback=progress_callback,
                    connection_callback=connection_callback,
                )
            except RECOVERABLE_ERRORS as exc:
                fell_back_to_single = True
                self.last_archive_connections = 1
                self.last_archive_fallback_reason = str(exc)
                safe_print(
                    f"archive segmented download unavailable; requested={connections}, actual=1; "
                    f"falling back to single connection: {exc}",
                    file=sys.stderr,
                )
                if connection_callback:
                    connection_callback(1, str(exc))

        if connection_callback and not fell_back_to_single:
            connection_callback(1, "")
        return self.download_archive_file_single(url, referer, target, progress_callback=progress_callback)

    def probe_archive_range_size(self, url: str, referer: str) -> Optional[int]:
        self.last_archive_probe_reason = ""
        last_error: Optional[BaseException] = None
        for attempt in range(self.retries + 1):
            req = Request(
                url,
                headers=self.make_headers(
                    referer,
                    {
                        "Accept": "application/zip,application/octet-stream,*/*",
                        "Range": "bytes=0-0",
                    },
                ),
            )
            try:
                with self.opener.open(req, timeout=self.timeout) as response:
                    status = getattr(response, "status", 200)
                    if status >= 400:
                        raise HTTPError(url, status, "archive range probe failed", response.headers, None)
                    if status != 206:
                        self.last_archive_probe_reason = (
                            f"range probe returned HTTP {status}; "
                            f"Accept-Ranges={response.headers.get('Accept-Ranges', '?')}; "
                            f"Content-Range={response.headers.get('Content-Range', '?')}"
                        )
                        return None
                    total = parse_content_range_total(response.headers.get("Content-Range"))
                    response.read(1)
                    if not total:
                        self.last_archive_probe_reason = (
                            "range probe returned 206 without a valid Content-Range "
                            f"({response.headers.get('Content-Range', '?')})"
                        )
                    return total
            except NETWORK_ERRORS as exc:
                last_error = exc
                if attempt >= self.retries or not is_retryable_error(exc):
                    raise
                sleep_before_retry(attempt, self.retry_backoff, f"archive range probe failed: {url}: {exc}")
        assert last_error is not None
        raise last_error

    def download_archive_file_segmented(
        self,
        url: str,
        referer: str,
        target: Path,
        connections: int,
        progress_callback: Optional[Callable[[int, Optional[int]], None]] = None,
        connection_callback: Optional[Callable[[int, str], None]] = None,
    ) -> Tuple[Path, bool]:
        total_size = self.probe_archive_range_size(url, referer)
        if not total_size:
            raise DownloadIntegrityError(self.last_archive_probe_reason or "server did not advertise HTTP Range support")

        ranges = split_ranges(total_size, connections)
        if len(ranges) <= 1:
            raise DownloadIntegrityError("archive is too small for segmented download")
        self.last_archive_connections = len(ranges)
        if connection_callback:
            connection_callback(len(ranges), "")

        target.parent.mkdir(parents=True, exist_ok=True)
        parts_dir = target.parent / f"{target.name}.parts"
        parts_dir.mkdir(parents=True, exist_ok=True)
        parts = [parts_dir / f"{index:04d}.part" for index in range(len(ranges))]
        temp = target.with_suffix(target.suffix + ".part")
        progress_lock = threading.Lock()
        part_progress = [0 for _part in ranges]

        def set_part_progress(index: int, value: int) -> None:
            with progress_lock:
                part_progress[index] = max(0, value)
                current = sum(part_progress)
            if progress_callback:
                progress_callback(current, total_size)

        def download_part(index: int, start: int, end: int, part_path: Path) -> None:
            expected_size = end - start + 1
            if part_path.exists() and part_path.stat().st_size == expected_size:
                return
            if part_path.exists():
                part_path.unlink()
            part_temp = part_path.with_suffix(part_path.suffix + ".tmp")
            last_error: Optional[BaseException] = None
            for attempt in range(self.retries + 1):
                req = Request(
                    url,
                    headers=self.make_headers(
                        referer,
                        {
                            "Accept": "application/zip,application/octet-stream,*/*",
                            "Range": f"bytes={start}-{end}",
                        },
                    ),
                )
                try:
                    with self.opener.open(req, timeout=self.timeout) as response:
                        status = getattr(response, "status", 200)
                        if status >= 400:
                            raise HTTPError(url, status, "archive part download failed", response.headers, None)
                        if status != 206:
                            raise DownloadIntegrityError("server did not honor HTTP Range request")
                        received_part = 0
                        with part_temp.open("wb") as handle:
                            while True:
                                chunk = response.read(NETWORK_READ_CHUNK_SIZE)
                                if not chunk:
                                    break
                                handle.write(chunk)
                                received_part += len(chunk)
                                set_part_progress(index, received_part)
                        if received_part != expected_size:
                            raise DownloadIntegrityError(
                                f"incomplete archive part {index + 1}: got {received_part} bytes, "
                                f"expected {expected_size} bytes"
                            )
                        part_temp.replace(part_path)
                        set_part_progress(index, expected_size)
                        return
                except NETWORK_ERRORS as exc:
                    last_error = exc
                    set_part_progress(index, 0)
                    if part_temp.exists():
                        try:
                            part_temp.unlink()
                        except OSError:
                            pass
                    if attempt >= self.retries or not is_retryable_error(exc):
                        raise
                    sleep_before_retry(
                        attempt,
                        self.retry_backoff,
                        f"archive part {index + 1}/{len(ranges)} failed: {url}: {exc}",
                    )
            assert last_error is not None
            raise last_error

        pending = []
        for index, (start, end) in enumerate(ranges):
            part_path = parts[index]
            expected_size = end - start + 1
            if part_path.exists() and part_path.stat().st_size == expected_size:
                part_progress[index] = expected_size
            else:
                pending.append((index, start, end, part_path))
        if progress_callback:
            progress_callback(sum(part_progress), total_size)

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(ranges)) as executor:
            futures = [executor.submit(download_part, *item) for item in pending]
            for future in concurrent.futures.as_completed(futures):
                future.result()

        try:
            with temp.open("wb") as output:
                for part_path in parts:
                    with part_path.open("rb") as part:
                        while True:
                            chunk = part.read(NETWORK_READ_CHUNK_SIZE)
                            if not chunk:
                                break
                            output.write(chunk)
            if temp.stat().st_size != total_size:
                raise DownloadIntegrityError(
                    f"incomplete archive after merge: got {temp.stat().st_size} bytes, expected {total_size} bytes"
                )
            if not validate_zip_file(temp):
                raise DownloadIntegrityError(f"incomplete or invalid archive file: {temp}")
            temp.replace(target)
            cleanup_parts_dir(parts_dir)
            if progress_callback:
                progress_callback(total_size, total_size)
            return target, False
        except OSError:
            if temp.exists():
                try:
                    temp.unlink()
                except OSError:
                    pass
            raise

    def download_archive_file_single(
        self,
        url: str,
        referer: str,
        target: Path,
        progress_callback: Optional[Callable[[int, Optional[int]], None]] = None,
    ) -> Tuple[Path, bool]:
        if target.exists():
            target.unlink()

        last_error: Optional[BaseException] = None
        for attempt in range(self.retries + 1):
            req = Request(
                url,
                headers=self.make_headers(
                    referer,
                    {"Accept": "application/zip,application/octet-stream,*/*"},
                ),
            )
            temp: Optional[Path] = None
            try:
                with self.opener.open(req, timeout=self.timeout) as response:
                    status = getattr(response, "status", 200)
                    if status >= 400:
                        raise HTTPError(url, status, "archive download failed", response.headers, None)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    temp = target.with_suffix(target.suffix + ".part")
                    expected_size = parse_content_length(response.headers.get("Content-Length"))
                    received_size = 0
                    if progress_callback:
                        progress_callback(0, expected_size)
                    with temp.open("wb") as handle:
                        while True:
                            chunk = response.read(NETWORK_READ_CHUNK_SIZE)
                            if not chunk:
                                break
                            handle.write(chunk)
                            received_size += len(chunk)
                            if progress_callback:
                                progress_callback(received_size, expected_size)
                    if expected_size is not None and received_size < expected_size:
                        raise DownloadIntegrityError(
                            f"incomplete archive: got {received_size} bytes, expected {expected_size} bytes"
                        )
                    if not validate_zip_file(temp):
                        raise DownloadIntegrityError(f"incomplete or invalid archive file: {temp}")
                    temp.replace(target)
                    if progress_callback:
                        progress_callback(received_size, expected_size)
                    return target, False
            except NETWORK_ERRORS as exc:
                last_error = exc
                if temp and temp.exists():
                    try:
                        temp.unlink()
                    except OSError:
                        pass
                if attempt >= self.retries or not is_retryable_error(exc):
                    raise
                sleep_before_retry(attempt, self.retry_backoff, f"archive request failed: {url}: {exc}")
        assert last_error is not None
        raise last_error


def choose_extension(content_type: Optional[str], url: str) -> str:
    if content_type:
        mime = content_type.split(";", 1)[0].strip().lower()
        extension = mimetypes.guess_extension(mime) or ""
        if extension == ".jpe":
            extension = ".jpg"
        if extension in SUPPORTED_EXTENSIONS:
            return extension
    path_extension = Path(urlparse(url).path).suffix.lower()
    if path_extension in SUPPORTED_EXTENSIONS:
        return path_extension
    return ".jpg"


def parse_content_length(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if parsed >= 0 else None


def parse_content_range_total(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    match = CONTENT_RANGE_RE.search(value)
    if not match:
        return None
    total = match.group(3)
    return int(total) if total.isdigit() else None


def split_ranges(total_size: int, connections: int, min_part_size: int = ARCHIVE_MIN_PART_SIZE) -> List[Tuple[int, int]]:
    if total_size <= 0:
        return []
    max_parts = max(1, (total_size + min_part_size - 1) // min_part_size)
    parts = max(1, min(connections, max_parts))
    ranges: List[Tuple[int, int]] = []
    base_size = total_size // parts
    remainder = total_size % parts
    start = 0
    for index in range(parts):
        size = base_size + (1 if index < remainder else 0)
        end = start + size - 1
        ranges.append((start, end))
        start = end + 1
    return ranges


def validate_image_file(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            header = handle.read(32)
            if header.startswith(b"\x89PNG\r\n\x1a\n"):
                handle.seek(8)
                return validate_png_file(handle)
            if header.startswith(b"\xff\xd8"):
                return validate_jpeg_file(handle)
            if header.startswith((b"GIF87a", b"GIF89a")):
                return validate_gif_file(handle)
            if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
                return validate_webp_file(path, header)
            return False
    except OSError:
        return False


def validate_zip_file(path: Path) -> bool:
    try:
        if path.stat().st_size <= 0:
            return False
        with zipfile.ZipFile(path) as archive:
            if not archive.infolist():
                return False
            return archive.testzip() is None
    except (OSError, RuntimeError, zipfile.BadZipFile):
        return False


def validate_png_file(handle: object) -> bool:
    while True:
        chunk_header = handle.read(8)
        if len(chunk_header) != 8:
            return False
        chunk_size = struct.unpack(">I", chunk_header[:4])[0]
        chunk_type = chunk_header[4:8]
        checksum = zlib.crc32(chunk_type)
        remaining = chunk_size
        while remaining:
            data = handle.read(min(1024 * 128, remaining))
            if not data:
                return False
            checksum = zlib.crc32(data, checksum)
            remaining -= len(data)
        crc_bytes = handle.read(4)
        if len(crc_bytes) != 4:
            return False
        expected_crc = struct.unpack(">I", crc_bytes)[0]
        if (checksum & 0xFFFFFFFF) != expected_crc:
            return False
        if chunk_type == b"IEND":
            return handle.read(1) == b""


def validate_jpeg_file(handle: object) -> bool:
    try:
        handle.seek(-2, os.SEEK_END)
    except OSError:
        return False
    return handle.read(2) == b"\xff\xd9"


def validate_gif_file(handle: object) -> bool:
    try:
        handle.seek(-1, os.SEEK_END)
    except OSError:
        return False
    return handle.read(1) == b"\x3b"


def validate_webp_file(path: Path, header: bytes) -> bool:
    if len(header) < 12:
        return False
    declared_size = struct.unpack("<I", header[4:8])[0] + 8
    try:
        actual_size = path.stat().st_size
    except OSError:
        return False
    return actual_size >= declared_size


def detail_cache_path(output: Path, gid: int, token: str) -> Path:
    return output / ".eh_batch_cache" / f"{gid}-{sanitize_filename(token, 'token')}.json"


def load_detail_cache(output: Path, gallery: Gallery) -> Optional[GalleryDetail]:
    path = detail_cache_path(output, gallery.gid, gallery.token)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if int(data.get("gid", -1)) != gallery.gid or str(data.get("token") or "") != gallery.token:
            return None
        pages = int(data.get("pages") or 0)
        if pages <= 0:
            return None
        raw_tokens = data.get("page_tokens")
        if not isinstance(raw_tokens, dict):
            return None
        page_tokens = {int(key): str(value) for key, value in raw_tokens.items() if str(value)}
        if len(page_tokens) < pages:
            return None
        return GalleryDetail(
            gid=gallery.gid,
            token=gallery.token,
            title=str(data.get("title") or gallery.title or gallery.gid),
            pages=pages,
            page_tokens=page_tokens,
            preview_pages=int(data.get("preview_pages") or 1),
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def save_detail_cache(output: Path, detail: GalleryDetail) -> None:
    path = detail_cache_path(output, detail.gid, detail.token)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "version": 1,
        "gid": detail.gid,
        "token": detail.token,
        "title": detail.title,
        "pages": detail.pages,
        "preview_pages": detail.preview_pages,
        "page_tokens": {str(index): token for index, token in sorted(detail.page_tokens.items())},
    }
    temp = path.with_suffix(path.suffix + ".part")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def find_existing_image(target_base: Path, cleanup_invalid: bool = False) -> Optional[Path]:
    for extension in SUPPORTED_EXTENSIONS:
        path = target_base.with_suffix(extension)
        if not path.exists():
            continue
        if validate_image_file(path):
            return path
        if cleanup_invalid:
            try:
                path.unlink()
            except OSError:
                pass
    return None


def image_exists(target_base: Path, cleanup_invalid: bool = False) -> bool:
    return find_existing_image(target_base, cleanup_invalid) is not None


def archive_metadata_path(output: Path, gid: int, token: str) -> Path:
    return state_dir_for(output) / "archive-metadata" / f"{gid}-{sanitize_filename(token, 'token')}.json"


def write_archive_metadata(
    output: Path,
    detail: GalleryDetail,
    mode: str,
    option: ArchiveOption,
    path: Path,
    download_url: Optional[str],
    skipped: bool,
) -> None:
    data = {
        "version": 1,
        "download_mode": mode,
        "gid": detail.gid,
        "token": detail.token,
        "title": detail.title,
        "archive_path": str(path),
        "archive_cost": option.cost,
        "archive_size": option.size,
        "archive_option": option.kind,
        "archive_form_url": option.url,
        "download_url": download_url,
        "skipped": skipped,
        "updated_at": current_time_text(),
    }
    write_json_atomic(archive_metadata_path(output, detail.gid, detail.token), data)


def matches_gallery(gallery: Gallery, args: argparse.Namespace) -> bool:
    title = gallery.title or ""
    title_match = getattr(args, "title_match", "all")
    checks: List[bool] = []
    contains_values = split_multi_values(getattr(args, "title_contains", None))
    checks.extend(value.lower() in title.lower() for value in contains_values)
    regex_values = split_multi_values(getattr(args, "title_regex", None))
    checks.extend(re.search(pattern, title, re.IGNORECASE) is not None for pattern in regex_values)
    if not checks:
        return True
    return any(checks) if title_match == "any" else all(checks)


def download_gallery_archive(client: EhClient, gallery: Gallery, args: argparse.Namespace) -> ArchiveDownloadResult:
    output = Path(args.output)
    mode = getattr(args, "download_mode", "archive-original")
    emit_progress(
        args,
        event="gallery",
        gid=gallery.gid,
        title=gallery.title,
        mode=mode,
        status="Preparing",
        percent=0,
    )
    try:
        detail = client.collect_basic_detail(gallery)
    except RECOVERABLE_ERRORS as exc:
        raise DownloadStageError(f"gallery detail: {gallery.url}: {exc}") from exc

    basename = sanitize_filename(f"{detail.gid}-{detail.title}", str(detail.gid))
    archive_suffix = ".zip" if mode == "archive-original" else ".resample.zip"
    target = output / f"{basename}{archive_suffix}"
    emit_progress(
        args,
        event="gallery",
        gid=detail.gid,
        title=detail.title,
        mode=mode,
        status="Checking",
        percent=0,
    )

    if target.exists() and not args.overwrite and validate_zip_file(target):
        kind = "original" if mode == "archive-original" else "resample"
        try:
            archive_info = client.fetch_archive_info(detail)
            option = archive_info.option(mode)
        except RECOVERABLE_ERRORS as exc:
            option = ArchiveOption(kind=kind, url="", dltype="", dlcheck="")
            safe_print(f"[{detail.gid}] archive metadata unavailable while skipping existing file: {exc}", file=sys.stderr)
        safe_print(
            f"[{detail.gid}] archive {option.kind} exists, skipped "
            f"(cost={option.cost or '?'}, size={option.size or '?'}) -> {target}"
        )
        write_archive_metadata(output, detail, mode, option, target, None, skipped=True)
        emit_progress(
            args,
            event="gallery",
            gid=detail.gid,
            title=detail.title,
            mode=mode,
            status="Skipped",
            percent=100,
            size=option.size,
            cost=option.cost,
            path=str(target),
        )
        return ArchiveDownloadResult(path=target, cost=option.cost, size=option.size, skipped=True)

    try:
        emit_progress(args, event="gallery", gid=detail.gid, title=detail.title, mode=mode, status="Archiver")
        archive_info = client.fetch_archive_info(detail)
        option = archive_info.option(mode)
        connections = max(1, int(getattr(args, "archive_connections", 1)))
        safe_print(
            f"[{detail.gid}] archive {option.kind} cost={option.cost or '?'} "
            f"size={option.size or '?'} requested_connections={connections}"
        )
        emit_progress(
            args,
            event="gallery",
            gid=detail.gid,
            title=detail.title,
            mode=mode,
            status="Requesting",
            percent=0,
            size=option.size,
            cost=option.cost,
            connections=connections,
        )
        download_url = client.request_archive_download_url(archive_info, option)
        progress = ProgressThrottle(args, detail.gid, detail.title, mode, connections=connections)

        def on_archive_connections(actual: int, reason: str = "") -> None:
            progress.connections = actual
            if reason:
                safe_print(f"[{detail.gid}] archive connection fallback: {reason}", file=sys.stderr)
            emit_progress(
                args,
                event="gallery",
                gid=detail.gid,
                title=detail.title,
                mode=mode,
                status="Downloading",
                percent=0,
                size=option.size,
                cost=option.cost,
                connections=actual,
            )

        saved, skipped = client.download_archive_file(
            download_url,
            referer=archive_info.url,
            target=target,
            overwrite=args.overwrite,
            connections=connections,
            progress_callback=progress,
            connection_callback=on_archive_connections,
        )
    except RECOVERABLE_ERRORS as exc:
        raise DownloadStageError(f"archive download: {gallery.url}: {exc}") from exc

    write_archive_metadata(output, detail, mode, option, saved, download_url, skipped=skipped)
    if skipped:
        safe_print(f"[{detail.gid}] archive exists, skipped -> {saved}")
    else:
        safe_print(f"[{detail.gid}] archive saved -> {saved}")
    emit_progress(
        args,
        event="gallery",
        gid=detail.gid,
        title=detail.title,
        mode=mode,
        status="Skipped" if skipped else "Done",
        percent=100,
        size=option.size,
        cost=option.cost,
        connections=client.last_archive_connections,
        path=str(saved),
    )
    return ArchiveDownloadResult(path=saved, cost=option.cost, size=option.size, skipped=skipped)


def download_gallery(client: EhClient, gallery: Gallery, args: argparse.Namespace) -> None:
    output = Path(args.output)
    emit_progress(
        args,
        event="gallery",
        gid=gallery.gid,
        title=gallery.title,
        mode=getattr(args, "download_mode", "pages"),
        status="Preparing",
        percent=0,
    )
    cached_detail = load_detail_cache(output, gallery)
    detail_from_cache = cached_detail is not None
    if cached_detail is None:
        try:
            detail = client.collect_detail(gallery)
        except RECOVERABLE_ERRORS as exc:
            raise DownloadStageError(f"gallery detail: {gallery.url}: {exc}") from exc
        save_detail_cache(output, detail)
    else:
        detail = cached_detail

    dirname = sanitize_filename(f"{detail.gid}-{detail.title}", str(detail.gid))
    gallery_dir = output / dirname
    gallery_dir.mkdir(parents=True, exist_ok=True)

    def write_metadata(current_detail: GalleryDetail) -> None:
        metadata = {
            "gid": current_detail.gid,
            "token": current_detail.token,
            "title": current_detail.title,
            "url": client.detail_url(current_detail.gid, current_detail.token),
            "pages": current_detail.pages,
            "page_tokens": {str(index): token for index, token in sorted(current_detail.page_tokens.items())},
        }
        (gallery_dir / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

    write_metadata(detail)

    downloaded = 0
    max_pages = min(detail.pages, args.max_image_pages) if args.max_image_pages else detail.pages
    pending_indices = []

    for index in range(max_pages):
        ptoken = detail.page_tokens.get(index)
        if not ptoken:
            raise ValueError(f"Missing page token for page {index + 1}")
        target_base = gallery_dir / f"{index + 1:08d}"
        if not args.overwrite and image_exists(target_base, cleanup_invalid=True):
            continue
        pending_indices.append(index)
    already_done = max_pages - len(pending_indices)

    def emit_page_progress(status: str, done_pages: int, error: Optional[str] = None) -> None:
        percent = round(done_pages * 100.0 / max_pages, 1) if max_pages else 100
        emit_progress(
            args,
            event="gallery",
            gid=detail.gid,
            title=detail.title,
            mode=getattr(args, "download_mode", "pages"),
            status=status,
            percent=percent,
            done=done_pages,
            total=max_pages,
            error=error,
        )

    emit_page_progress("Checking" if already_done else "Downloading", already_done)

    detail_lock = threading.Lock()
    detail_refreshed = False

    def current_detail() -> GalleryDetail:
        with detail_lock:
            return detail

    def refresh_detail_after_page_404(page_client: EhClient, index: int, exc: BaseException) -> bool:
        nonlocal detail, detail_refreshed
        if not detail_from_cache or not is_http_status(exc, {404}):
            return False
        with detail_lock:
            if detail_refreshed:
                return True
            safe_print(
                f"[{detail.gid}] {index + 1}/{max_pages} page token returned 404; refreshing gallery detail cache",
                file=sys.stderr,
            )
            try:
                refreshed = page_client.collect_detail(gallery)
            except RECOVERABLE_ERRORS as refresh_exc:
                safe_print(
                    f"[{detail.gid}] {index + 1}/{max_pages} detail refresh failed: {refresh_exc}",
                    file=sys.stderr,
                )
                return False
            detail = refreshed
            detail_refreshed = True
            save_detail_cache(output, detail)
            write_metadata(detail)
            return True

    def fetch_page_info_for_index(
        page_client: EhClient,
        index: int,
        show_key_hint: Optional[str],
    ) -> Tuple[PageInfo, Optional[str], GalleryDetail, str]:
        current = current_detail()
        ptoken = current.page_tokens.get(index)
        if not ptoken:
            raise ValueError(f"Missing page token for page {index + 1}")
        previous_ptoken = current.page_tokens.get(index - 1) if index > 0 else None
        page_url = page_client.page_url(current.gid, index, ptoken)
        try:
            info, next_show_key = page_client.fetch_page_info(
                current.gid,
                current.token,
                index,
                ptoken,
                previous_ptoken,
                show_key_hint,
                prefer_api=not args.html_pages,
            )
        except RECOVERABLE_ERRORS as exc:
            if refresh_detail_after_page_404(page_client, index, exc):
                current = current_detail()
                ptoken = current.page_tokens.get(index)
                if not ptoken:
                    raise DownloadStageError(f"page info {index + 1}: missing page token after refresh") from exc
                previous_ptoken = current.page_tokens.get(index - 1) if index > 0 else None
                page_url = page_client.page_url(current.gid, index, ptoken)
                try:
                    info, next_show_key = page_client.fetch_page_info(
                        current.gid,
                        current.token,
                        index,
                        ptoken,
                        previous_ptoken,
                        show_key_hint,
                        prefer_api=not args.html_pages,
                    )
                except RECOVERABLE_ERRORS as retry_exc:
                    raise DownloadStageError(f"page info {index + 1}: {page_url}: {retry_exc}") from retry_exc
            else:
                raise DownloadStageError(f"page info {index + 1}: {page_url}: {exc}") from exc
        return info, next_show_key, current, page_url

    def download_one(page_client: EhClient, index: int, show_key_hint: Optional[str]) -> Optional[str]:
        target_base = gallery_dir / f"{index + 1:08d}"
        if not args.overwrite and image_exists(target_base, cleanup_invalid=True):
            return show_key_hint
        info, next_show_key, page_detail, referer = fetch_page_info_for_index(page_client, index, show_key_hint)
        image_url = info.image_url
        saved: Optional[Path] = None
        if args.original and info.origin_image_url:
            try:
                original_url = page_client.resolve_original_url(info, referer)
            except RECOVERABLE_ERRORS as exc:
                safe_print(
                    f"[{page_detail.gid}] {index + 1}/{max_pages} original unavailable; "
                    f"falling back to displayed image: {exc}",
                    file=sys.stderr,
                )
            else:
                if original_url:
                    try:
                        saved = page_client.download_file(original_url, referer, target_base, overwrite=args.overwrite)
                    except RECOVERABLE_ERRORS as exc:
                        safe_print(
                            f"[{page_detail.gid}] {index + 1}/{max_pages} original download failed; "
                            f"falling back to displayed image: {exc}",
                            file=sys.stderr,
                        )
        if saved is None:
            try:
                saved = page_client.download_file(image_url, referer, target_base, overwrite=args.overwrite)
            except RECOVERABLE_ERRORS as exc:
                raise DownloadStageError(f"image download {index + 1}: {image_url}: {exc}") from exc
        safe_print(f"[{page_detail.gid}] {index + 1}/{max_pages} -> {saved}")
        if args.delay > 0:
            time.sleep(args.delay)
        return next_show_key

    page_workers = max(1, min(args.page_workers, len(pending_indices) or 1))
    show_key: Optional[str] = None
    if page_workers == 1:
        for index in pending_indices:
            show_key = download_one(client, index, show_key)
            downloaded += 1
            emit_page_progress("Downloading", already_done + downloaded)
    else:
        if pending_indices and not args.html_pages:
            first_index = pending_indices.pop(0)
            show_key = download_one(client, first_index, None)
            downloaded += 1
            emit_page_progress("Downloading", already_done + downloaded)

        worker_state = threading.local()

        def get_page_client() -> EhClient:
            page_client = getattr(worker_state, "client", None)
            if page_client is None:
                page_client = client.clone()
                worker_state.client = page_client
            return page_client

        def run_worker(index: int) -> Optional[str]:
            return download_one(get_page_client(), index, show_key)

        with concurrent.futures.ThreadPoolExecutor(max_workers=page_workers) as executor:
            future_map = {executor.submit(run_worker, index): index for index in pending_indices}
            for future in concurrent.futures.as_completed(future_map):
                future.result()
                downloaded += 1
                emit_page_progress("Downloading", already_done + downloaded)

    safe_print(f"[{detail.gid}] done, downloaded {downloaded} file(s)")
    emit_page_progress("Done", max_pages)


def print_batch_summary(total: int, successes: int, failed_gids: List[int]) -> None:
    if failed_gids:
        failed_text = ", ".join(str(gid) for gid in failed_gids)
        safe_print(
            f"[batch] completed with {len(failed_gids)}/{total} failed gallery/galleries: {failed_text}",
            file=sys.stderr,
        )
        return
    safe_print(f"[batch] completed successfully: {successes}/{total} gallery/galleries")


def source_description(args: argparse.Namespace) -> str:
    if getattr(args, "retry_failed", False):
        return f"retry-failed:{resolve_job_name(args)}"
    for key in ("search", "uploader", "tag", "url"):
        values = source_values(args, key)
        if values:
            return f"{key}:{';'.join(values)}"
    return "unknown"


def write_run_state(
    output: Path,
    job_name: str,
    started_at: str,
    source: str,
    results: List[GalleryRunResult],
) -> None:
    finished_at = current_time_text()
    failed_results = [result for result in results if not result.ok]
    success_results = [result for result in results if result.ok]
    state = {
        "version": 1,
        "job_name": job_name,
        "started_at": started_at,
        "finished_at": finished_at,
        "source": source,
        "total": len(results),
        "successes": len(success_results),
        "failures": len(failed_results),
        "failed_galleries": [
            {
                **gallery_to_dict(result.gallery),
                "error": result.error or "unknown error",
            }
            for result in failed_results
        ],
        "successful_galleries": [successful_result_to_dict(result) for result in success_results],
    }
    write_json_atomic(latest_state_path(output, job_name), state)

    failure_lines = [
        f"# job: {job_name}",
        f"# started_at: {started_at}",
        f"# finished_at: {finished_at}",
        f"# failures: {len(failed_results)}/{len(results)}",
    ]
    if failed_results:
        failure_lines.append("# gid\ttitle\turl\terror")
        for result in failed_results:
            gallery = result.gallery
            failure_lines.append(f"{gallery.gid}\t{gallery.title}\t{gallery.url}\t{result.error or 'unknown error'}")
    else:
        failure_lines.append("No failed galleries.")
    failures_text_path(output, job_name).write_text("\n".join(failure_lines) + "\n", encoding="utf-8")

    history_path(output, job_name).parent.mkdir(parents=True, exist_ok=True)
    with history_path(output, job_name).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(state, ensure_ascii=False, separators=(",", ":")) + "\n")


def load_failed_galleries(output: Path, job_name: str) -> List[Gallery]:
    path = latest_state_path(output, job_name)
    if not path.exists():
        raise ValueError(f"No failure state found for job '{job_name}': {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    failed = data.get("failed_galleries") if isinstance(data, dict) else None
    if not isinstance(failed, list):
        raise ValueError(f"Invalid failure state file: {path}")
    return [gallery_from_dict(item) for item in failed]


def print_failure_report(args: argparse.Namespace) -> int:
    output = Path(args.output)
    paths = [latest_state_path(output, args.job_name)] if args.job_name else sorted(state_dir_for(output).glob("*-latest.json"))
    if not paths:
        safe_print(f"No state files found in {state_dir_for(output)}")
        return 0
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            safe_print(f"{path}: failed to read: {exc}", file=sys.stderr)
            continue
        failed = data.get("failed_galleries") if isinstance(data, dict) else []
        if not isinstance(failed, list):
            failed = []
        safe_print(
            f"{data.get('job_name', path.stem)}\t{data.get('failures', len(failed))}/"
            f"{data.get('total', '?')} failed\t{path}"
        )
        for item in failed:
            gallery = gallery_from_dict(item)
            error = item.get("error", "") if isinstance(item, dict) else ""
            safe_print(f"  {gallery.gid}\t{gallery.title}\t{gallery.url}\t{error}")
    return 0


def run(args: argparse.Namespace) -> int:
    install_builtin_hosts(args.hosts == "builtin")
    output = Path(args.output)
    job_name = resolve_job_name(args)
    started_at = current_time_text()
    cookie_header = parse_cookie_source(args.cookies, Path(args.cookie_file) if args.cookie_file else None)
    client = EhClient.from_args(args, cookie_header)
    if args.retry_failed:
        galleries = load_failed_galleries(output, job_name)
        safe_print(f"[batch] retrying {len(galleries)} failed gallery/galleries for job '{job_name}'")
    else:
        galleries = []
        source_urls = client.list_urls_from_args(args)
        source_match = getattr(args, "source_match", "any")
        if source_match == "all" and len(source_urls) > 1:
            source_results: List[Dict[int, Gallery]] = []
            for start_url in source_urls:
                current: Dict[int, Gallery] = {}
                for gallery in client.iter_galleries(start_url, args.max_list_pages):
                    current.setdefault(gallery.gid, gallery)
                source_results.append(current)
            common_gids = set(source_results[0])
            for current in source_results[1:]:
                common_gids.intersection_update(current)
            candidates = [gallery for gid, gallery in source_results[0].items() if gid in common_gids]
        else:
            candidates = []
            seen_gids = set()
            for start_url in source_urls:
                for gallery in client.iter_galleries(start_url, args.max_list_pages):
                    if gallery.gid in seen_gids:
                        continue
                    seen_gids.add(gallery.gid)
                    candidates.append(gallery)
        galleries = [gallery for gallery in candidates if matches_gallery(gallery, args)]
    if args.max_galleries:
        galleries = galleries[: args.max_galleries]

    if args.dry_run:
        for gallery in galleries:
            emit_progress(
                args,
                event="gallery",
                gid=gallery.gid,
                title=gallery.title,
                mode=getattr(args, "download_mode", "pages"),
                status="Waiting",
                percent=0,
            )
            safe_print(f"{gallery.gid}\t{gallery.title}\t{gallery.url}")
        safe_print(f"{len(galleries)} gallery/galleries matched")
        return 0

    if not galleries:
        if args.retry_failed:
            safe_print(f"No failed galleries found for job '{job_name}'")
            return 0
        safe_print("No matching galleries found", file=sys.stderr)
        return 1

    def run_one(gallery: Gallery) -> GalleryRunResult:
        worker_client = EhClient.from_args(args, cookie_header)
        safe_print(f"[{gallery.gid}] start {gallery.title}")
        mode = getattr(args, "download_mode", "pages")
        try:
            if mode in {"archive-original", "archive-resample"}:
                archive_result = download_gallery_archive(worker_client, gallery, args)
                return GalleryRunResult(
                    gallery=gallery,
                    ok=True,
                    download_mode=mode,
                    archive_path=str(archive_result.path),
                    archive_cost=archive_result.cost,
                    archive_size=archive_result.size,
                    skipped=archive_result.skipped,
                )
            download_gallery(worker_client, gallery, args)
            return GalleryRunResult(gallery=gallery, ok=True, download_mode=mode)
        except RECOVERABLE_ERRORS as exc:
            safe_print(f"[{gallery.gid}] failed: {exc}", file=sys.stderr)
            emit_progress(
                args,
                event="gallery",
                gid=gallery.gid,
                title=gallery.title,
                mode=mode,
                status="Error",
                error=str(exc),
            )
            return GalleryRunResult(gallery=gallery, ok=False, error=str(exc), download_mode=mode)

    workers = max(1, min(args.gallery_workers, len(galleries)))
    results: List[GalleryRunResult] = []
    if workers == 1:
        successes = 0
        failed_gids: List[int] = []
        for gallery in galleries:
            result = run_one(gallery)
            results.append(result)
            if not result.ok:
                failed_gids.append(gallery.gid)
                if not args.keep_going:
                    print_batch_summary(len(galleries), successes, failed_gids)
                    write_run_state(output, job_name, started_at, source_description(args), results)
                    return 1
            else:
                successes += 1
        print_batch_summary(len(galleries), successes, failed_gids)
        write_run_state(output, job_name, started_at, source_description(args), results)
        return 1 if failed_gids else 0

    successes = 0
    failed_gids: List[int] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        future_map = {executor.submit(run_one, gallery): gallery for gallery in galleries}
        for future in concurrent.futures.as_completed(future_map):
            gallery = future_map[future]
            result = future.result()
            results.append(result)
            if not result.ok:
                failed_gids.append(gallery.gid)
                if not args.keep_going:
                    executor.shutdown(cancel_futures=True)
                    print_batch_summary(len(galleries), successes, failed_gids)
                    write_run_state(output, job_name, started_at, source_description(args), results)
                    return 1
            else:
                successes += 1
    print_batch_summary(len(galleries), successes, failed_gids)
    write_run_state(output, job_name, started_at, source_description(args), results)
    return 1 if failed_gids else 0


def self_test() -> int:
    list_html = """
    <html><body>
      <a href="https://e-hentai.org/g/100/abcdef1234/">First Gallery</a>
      <a href="https://e-hentai.org/g/101/bcdefa2345/"><span>Second Gallery</span></a>
      <a id="unext" href="/?page=1">&gt;</a>
    </body></html>
    """
    galleries = parse_gallery_links(list_html, "https://e-hentai.org/")
    assert [gallery.gid for gallery in galleries] == [100, 101]
    assert galleries[1].title == "Second Gallery"
    assert parse_next_list_url(list_html, "https://e-hentai.org/") == "https://e-hentai.org/?page=1"

    detail_html = """
    <script>var gid = 100; var token = "abcdef1234"; var apiuid = 1; var apikey = "deadbeef";</script>
    <h1 id="gn">Sample Title</h1>
    <tr><td>Length:</td><td>2 pages</td></tr>
    <a href="https://e-hentai.org/s/ptoken1/100-1"><img alt="1"></a>
    <a href="https://e-hentai.org/s/ptoken2/100-2"><img alt="2"></a>
    """
    detail = parse_detail_html(detail_html, "https://e-hentai.org/g/100/abcdef1234/")
    assert detail.title == "Sample Title"
    assert detail.pages == 2
    assert detail.page_tokens == {0: "ptoken1", 1: "ptoken2"}

    page_html = """
    <script>var showkey="show123";</script>
    <img id="img" src="https://ehgt.org/full/001.jpg" style="max-width:100%">
    <a onclick="return nl('skip123')">skip</a>
    <a href="https://e-hentai.org/fullimg.php?gid=100&page=1">Download original</a>
    """
    info = parse_page_html(page_html)
    assert info.image_url.endswith("001.jpg")
    assert info.show_key == "show123"
    assert info.skip_hath_key == "skip123"
    assert "fullimg" in (info.origin_image_url or "")

    api_json = json.dumps({"i3": '<img id="img" src="https://ehgt.org/full/002.png" style="">', "i6": "", "i7": None})
    assert parse_page_api_json(api_json).image_url.endswith("002.png")

    archive_html = """
    <div>Funds: 9,999 GP</div>
    <p>Download Cost: <strong>Free!</strong></p>
    <form action="/archiver.php?gid=100&amp;token=abcdef1234&amp;or=abc" method="post">
      <input type="hidden" name="dltype" value="org">
      <input type="submit" name="dlcheck" value="Download Original Archive">
    </form>
    <p>Estimated Size: <strong>18.46 MiB</strong></p>
    <p>Download Cost: <strong>20 GP</strong></p>
    <form action="/archiver.php?gid=100&amp;token=abcdef1234&amp;or=def" method="post">
      <input type="hidden" name="dltype" value="res">
      <input type="submit" name="dlcheck" value="Download Resample Archive">
    </form>
    <p>Estimated Size: <strong>8.00 MiB</strong></p>
    """
    archive_info = parse_archive_info(archive_html, "https://e-hentai.org/archiver.php?gid=100&token=abcdef1234")
    assert archive_info.original is not None and archive_info.original.cost == "Free!"
    assert archive_info.original.size == "18.46 MiB"
    assert archive_info.resample is not None and archive_info.resample.cost == "20 GP"
    assert archive_info.resample.size == "8.00 MiB"
    assert (
        parse_archive_continue_url('document.location = "/archiver.php?gid=100&next=1";', "https://e-hentai.org/")
        == "https://e-hentai.org/archiver.php?gid=100&next=1"
    )
    assert (
        parse_archive_final_download_url(
            '<a href="/archive.zip">Click Here To Start Downloading</a>',
            "https://e-hentai.org/archiver.php",
        )
        == "https://e-hentai.org/archive.zip"
    )
    assert parse_content_range_total("bytes 0-0/12345") == 12345
    assert split_ranges(10, 3, min_part_size=1) == [(0, 3), (4, 6), (7, 9)]

    gb18030_html = '<a href="https://e-hentai.org/g/102/cdefab3456/">[中文翻译] [白杨汉化组]</a>'.encode("gb18030")
    decoded = decode_response_body(gb18030_html, {})
    assert "[中文翻译]" in decoded
    assert matches_gallery(
        parse_gallery_links(decoded, "https://e-hentai.org/")[0],
        argparse.Namespace(title_contains="中文翻译", title_regex=None),
    )
    safe_print("self-test OK")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Batch download galleries from E-Hentai/ExHentai.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {APP_VERSION}")
    source = parser
    source.add_argument("--url", action="append", help="Existing gallery list/search URL. Repeat or separate with semicolons for OR.")
    source.add_argument("--search", action="append", help="Search keywords. Repeat or separate with semicolons for OR/AND.")
    source.add_argument("--uploader", action="append", help="Download from uploader listing(s). Repeat or separate with semicolons.")
    source.add_argument("--tag", action="append", help="Download from tag listing(s). Repeat or separate with semicolons.")
    parser.add_argument(
        "--source-match",
        choices=["any", "all"],
        default="any",
        help="How multiple source values are combined. any=OR, all=AND where meaningful.",
    )
    parser.add_argument("--site", choices=["e", "ex"], default="e", help="Target site. Default: e.")
    parser.add_argument(
        "--category",
        "--categories",
        action="append",
        dest="categories",
        help="Included gallery category name(s), repeatable or comma-separated. Example: doujinshi,manga,non-h.",
    )
    parser.add_argument("--f-cats", type=int, help="Raw f_cats value for search/list pages.")
    parser.add_argument("--title-contains", action="append", help="Only keep galleries whose title contains this text. Repeat or use semicolons.")
    parser.add_argument("--title-regex", action="append", help="Only keep galleries whose title matches this regex. Repeat or use semicolons.")
    parser.add_argument(
        "--title-match",
        choices=["any", "all"],
        default="all",
        help="How multiple title filters are combined. any=OR, all=AND.",
    )
    parser.add_argument("--max-list-pages", type=int, default=1, help="Maximum listing pages to scan.")
    parser.add_argument("--max-galleries", type=int, default=0, help="Maximum galleries to download. 0 means no cap.")
    parser.add_argument("--max-image-pages", type=int, default=0, help="Maximum image pages per gallery. 0 means all.")
    parser.add_argument("--output", default="eh_downloads", help="Output directory.")
    parser.add_argument("--cookies", help="Raw Cookie header. Prefer EH_COOKIES or --cookie-file.")
    parser.add_argument("--cookie-file", help="Cookie file: raw Cookie header, JSON object, or Netscape cookies.txt.")
    parser.add_argument("--timeout", type=float, default=60.0, help="HTTP timeout in seconds.")
    parser.add_argument("--delay", type=float, default=0.0, help="Delay between image downloads.")
    parser.add_argument("--retries", type=int, default=3, help="Retry count for failed HTTP requests.")
    parser.add_argument("--retry-backoff", type=float, default=2.0, help="Initial retry backoff in seconds.")
    parser.add_argument(
        "--download-mode",
        choices=["pages", "archive-original", "archive-resample"],
        default="pages",
        help="Download mode. pages saves individual images; archive-original/resample saves zip archives.",
    )
    parser.add_argument(
        "--archive-connections",
        type=int,
        default=DEFAULT_ARCHIVE_CONNECTIONS,
        help="Concurrent HTTP Range connections for archive zip downloads. Falls back to single connection if unsupported.",
    )
    parser.add_argument(
        "--gallery-workers",
        type=int,
        default=1,
        help="Number of galleries to download concurrently. Keep low to avoid rate limits.",
    )
    parser.add_argument(
        "--page-workers",
        type=int,
        default=3,
        help="Number of image pages to download concurrently inside one gallery.",
    )
    parser.add_argument(
        "--hosts",
        choices=["system", "builtin"],
        default="system",
        help="DNS mode. builtin uses EhViewer's built-in host/IP map.",
    )
    parser.add_argument(
        "--proxy-mode",
        choices=["system", "direct", "http"],
        default="system",
        help="Proxy mode. system uses OS/env proxy, direct disables proxies, http uses --proxy-url.",
    )
    parser.add_argument("--proxy-url", help="HTTP proxy URL, for example http://127.0.0.1:7890.")
    parser.add_argument("--html-pages", action="store_true", help="Fetch every image page as HTML instead of using api.php.")
    parser.add_argument("--original", action="store_true", help="Try original image URLs. This may spend GP.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing image files.")
    parser.add_argument("--keep-going", action="store_true", help="Continue after a gallery fails.")
    parser.add_argument("--dry-run", action="store_true", help="Only list matching galleries.")
    parser.add_argument("--job-name", help="Persistent job name for state and retry files.")
    parser.add_argument("--retry-failed", action="store_true", help="Retry failed galleries from the latest job state.")
    parser.add_argument("--list-failures", action="store_true", help="List latest recorded failures and exit.")
    parser.add_argument("--task-file", "--config-file", dest="task_file", help="JSON task/config file with multiple saved jobs.")
    parser.add_argument("--task-name", "--config-name", action="append", dest="task_names", help="Only run matching task/config name(s).")
    parser.add_argument("--run-tasks", "--run-configs", action="store_true", dest="run_tasks", help="Run enabled tasks from --task-file once.")
    parser.add_argument(
        "--schedule-tasks",
        "--schedule-configs",
        action="store_true",
        dest="schedule_tasks",
        help="Keep running enabled tasks from --task-file on intervals.",
    )
    parser.add_argument("--progress-json", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--self-test", action="store_true", help="Run offline parser tests.")
    return parser


CONFIG_VALUE_FLAGS = {
    "source_match": "--source-match",
    "site": "--site",
    "f_cats": "--f-cats",
    "title_contains": "--title-contains",
    "title_regex": "--title-regex",
    "title_match": "--title-match",
    "max_list_pages": "--max-list-pages",
    "max_galleries": "--max-galleries",
    "max_image_pages": "--max-image-pages",
    "output": "--output",
    "cookie_file": "--cookie-file",
    "timeout": "--timeout",
    "delay": "--delay",
    "retries": "--retries",
    "retry_backoff": "--retry-backoff",
    "download_mode": "--download-mode",
    "archive_connections": "--archive-connections",
    "gallery_workers": "--gallery-workers",
    "page_workers": "--page-workers",
    "hosts": "--hosts",
    "proxy_mode": "--proxy-mode",
    "proxy_url": "--proxy-url",
    "job_name": "--job-name",
}
CONFIG_BOOL_FLAGS = {
    "html_pages": "--html-pages",
    "original": "--original",
    "overwrite": "--overwrite",
    "keep_going": "--keep-going",
    "dry_run": "--dry-run",
    "list_failures": "--list-failures",
}
CONFIG_SOURCE_TYPES = {
    "search": "--search",
    "keywords": "--search",
    "uploader": "--uploader",
    "tag": "--tag",
    "list url": "--url",
    "list-url": "--url",
    "url": "--url",
}


def config_args_from_object(config: Dict[str, object]) -> List[str]:
    args: List[str] = []

    raw_conditions = config.get("server_conditions")
    if isinstance(raw_conditions, list) and raw_conditions:
        combined_search_terms: List[str] = []
        for index, condition in enumerate(raw_conditions, start=1):
            if not isinstance(condition, dict):
                raise ValueError(f"server_conditions item #{index} must be an object.")
            source_type = str(condition.get("type") or condition.get("source_type") or "").strip().lower()
            source_flag = CONFIG_SOURCE_TYPES.get(source_type)
            if not source_flag:
                raise ValueError(f"Unknown server condition type: {source_type}")
            value = condition.get("value")
            values = split_multi_values(value)
            if source_type in {"search", "keywords"}:
                combined_search_terms.extend(values)
            elif source_type == "tag":
                prefix = str(condition.get("prefix") or "").strip()
                raw_tag_value = str(condition.get("tag_value") or "").strip()
                if prefix and raw_tag_value:
                    exact = str(condition.get("exact", "1")).lower() not in {"0", "false", "no"}
                    combined_search_terms.append(format_tag_query(prefix, raw_tag_value, exact))
                else:
                    # A Tag condition is an E-Hentai search expression. Keep
                    # bare legacy values here as search terms as well, so a
                    # config can use any documented namespace or operator.
                    combined_search_terms.extend(values)
            else:
                for item in values:
                    args.extend([source_flag, item])
        if combined_search_terms:
            args.extend(["--search", " ".join(combined_search_terms)])
    else:
        source_type = str(config.get("source_type") or "").strip().lower()
        source_value = config.get("source_value")
        if source_type and source_value not in (None, ""):
            source_flag = CONFIG_SOURCE_TYPES.get(source_type)
            if not source_flag:
                raise ValueError(f"Unknown config source_type: {source_type}")
            for value in split_multi_values(source_value):
                args.extend([source_flag, value])
        else:
            for key in ("search", "uploader", "tag", "url"):
                value = config.get(key)
                if value not in (None, ""):
                    for item in split_multi_values(value):
                        args.extend([CONFIG_SOURCE_TYPES[key], item])
                    break

    categories = config.get("categories")
    if isinstance(categories, list):
        for category in categories:
            args.extend(["--category", str(category)])
    elif isinstance(categories, str) and categories.strip():
        args.extend(["--category", categories.strip()])

    raw_local_filters = config.get("local_filters")
    local_filter_keys = set()
    if isinstance(raw_local_filters, list) and raw_local_filters:
        for index, condition in enumerate(raw_local_filters, start=1):
            if not isinstance(condition, dict):
                raise ValueError(f"local_filters item #{index} must be an object.")
            filter_type = str(condition.get("type") or "").strip().lower()
            filter_flag = {"title contains": "--title-contains", "title regex": "--title-regex"}.get(filter_type)
            if not filter_flag:
                raise ValueError(f"Unknown local filter type: {filter_type}")
            for value in split_multi_values(condition.get("value")):
                args.extend([filter_flag, value])
            local_filter_keys.add("title_contains" if filter_type == "title contains" else "title_regex")

    for key, flag in CONFIG_VALUE_FLAGS.items():
        if key in local_filter_keys or (isinstance(raw_conditions, list) and raw_conditions and key == "source_match"):
            continue
        value = config.get(key)
        if value not in (None, ""):
            if isinstance(value, list):
                for item in value:
                    if item not in (None, ""):
                        args.extend([flag, str(item)])
            else:
                args.extend([flag, str(value)])

    for key, flag in CONFIG_BOOL_FLAGS.items():
        if bool(config.get(key, False)):
            args.append(flag)

    raw_args = config.get("args")
    if raw_args is not None:
        if not isinstance(raw_args, list) or not all(isinstance(item, (str, int, float, bool)) for item in raw_args):
            raise ValueError("Config args must be a JSON array of strings/numbers/bools.")
        args.extend(str(item) for item in raw_args)
    return args


def load_task_definitions(path: Path) -> List[Dict[str, object]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Failed to read task file {path}: {exc}") from exc
    defaults: Dict[str, object] = {}
    if isinstance(data, dict):
        raw_defaults = data.get("defaults")
        if isinstance(raw_defaults, dict):
            defaults = raw_defaults
        tasks = data.get("configs") or data.get("tasks")
    else:
        tasks = data
    if not isinstance(tasks, list):
        raise ValueError("Task/config file must be a JSON array or an object with a 'tasks' or 'configs' array.")
    normalized: List[Dict[str, object]] = []
    for index, task in enumerate(tasks, start=1):
        if not isinstance(task, dict):
            raise ValueError(f"Task #{index} must be an object.")
        name = str(task.get("name") or f"task-{index}").strip()
        try:
            task_args = config_args_from_object(defaults) + config_args_from_object(task)
        except ValueError as exc:
            raise ValueError(f"Task '{name}' is invalid: {exc}") from exc
        if not task_args:
            raise ValueError(f"Task '{name}' must define args or typed config fields.")
        normalized.append(
            {
                "name": name,
                "enabled": bool(task.get("enabled", True)),
                "interval_minutes": float(task.get("interval_minutes", 60)),
                "args": task_args,
            }
        )
    return normalized


def run_task_definition(task: Dict[str, object]) -> int:
    name = str(task["name"])
    task_args = list(task["args"])  # type: ignore[arg-type]
    if "--job-name" not in task_args:
        task_args.extend(["--job-name", name])
    parser = build_parser()
    args = parser.parse_args(task_args)
    if args.self_test or args.run_tasks or args.schedule_tasks:
        raise ValueError(f"Task '{name}' contains nested control arguments.")
    safe_print(f"[task:{name}] start")
    code = print_failure_report(args) if args.list_failures else run(args)
    safe_print(f"[task:{name}] exit code {code}")
    return code


def run_task_file(path: Path, selected_names: Optional[List[str]], schedule: bool) -> int:
    tasks = load_task_definitions(path)
    selected = set(selected_names or [])
    tasks = [task for task in tasks if task["enabled"] and (not selected or str(task["name"]) in selected)]
    if not tasks:
        safe_print("No enabled tasks matched.")
        return 0

    if not schedule:
        failures = 0
        for task in tasks:
            if run_task_definition(task) != 0:
                failures += 1
        return 1 if failures else 0

    next_run = {str(task["name"]): 0.0 for task in tasks}
    while True:
        now = time.time()
        for task in tasks:
            name = str(task["name"])
            if now < next_run[name]:
                continue
            code = run_task_definition(task)
            interval = max(1.0, float(task["interval_minutes"])) * 60.0
            next_run[name] = time.time() + interval
            safe_print(f"[task:{name}] next run in {interval / 60.0:g} minute(s), last code {code}")
        sleep_for = max(1.0, min(next_run.values()) - time.time())
        time.sleep(min(sleep_for, 60.0))


def main(argv: Optional[List[str]] = None) -> int:
    configure_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    if args.task_file and (args.run_tasks or args.schedule_tasks):
        try:
            return run_task_file(Path(args.task_file), args.task_names, schedule=args.schedule_tasks)
        except RECOVERABLE_ERRORS as exc:
            safe_print(f"Error: {exc}", file=sys.stderr)
            return 1
    if args.list_failures:
        return print_failure_report(args)
    try:
        return run(args)
    except RECOVERABLE_ERRORS as exc:
        safe_print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
