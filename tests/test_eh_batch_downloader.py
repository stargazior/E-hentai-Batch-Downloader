import argparse
import io
import json
import struct
import tempfile
import unittest
import zipfile
import zlib
from http.client import IncompleteRead
from pathlib import Path

import eh_batch_downloader as downloader


def png_chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def tiny_png() -> bytes:
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00\x00\x00\x00")
    return b"\x89PNG\r\n\x1a\n" + png_chunk(b"IHDR", ihdr) + png_chunk(b"IDAT", idat) + png_chunk(b"IEND", b"")


def write_tiny_zip(path: Path) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("00000001.jpg", b"not a real image but a valid zip member")


def make_stored_zip_bytes(size: int = 3 * 1024 * 1024) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("payload.bin", b"x" * size)
    return buffer.getvalue()


class FakeBinaryResponse:
    def __init__(self, url: str, status: int, body: bytes, headers: dict[str, str]) -> None:
        self.url = url
        self.status = status
        self.headers = headers
        self._body = io.BytesIO(body)
        self.read_sizes: list[int] = []

    def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        return self._body.read(size)

    def geturl(self) -> str:
        return self.url

    def __enter__(self) -> "FakeBinaryResponse":
        return self

    def __exit__(self, *_args) -> None:
        return None


class FakeRangeOpener:
    def __init__(self, body: bytes, supports_range: bool = True) -> None:
        self.body = body
        self.supports_range = supports_range
        self.range_downloads = 0
        self.full_downloads = 0
        self.responses: list[FakeBinaryResponse] = []

    def open(self, request, timeout=None):
        range_header = request.get_header("Range")
        if range_header and self.supports_range:
            start_text, end_text = range_header.replace("bytes=", "").split("-", 1)
            start = int(start_text)
            end = int(end_text)
            chunk = self.body[start : end + 1]
            if start == 0 and end == 0:
                headers = {"Content-Range": f"bytes 0-0/{len(self.body)}", "Content-Length": "1"}
                response = FakeBinaryResponse(request.full_url, 206, chunk, headers)
                self.responses.append(response)
                return response
            self.range_downloads += 1
            headers = {
                "Content-Range": f"bytes {start}-{end}/{len(self.body)}",
                "Content-Length": str(len(chunk)),
            }
            response = FakeBinaryResponse(request.full_url, 206, chunk, headers)
            self.responses.append(response)
            return response
        self.full_downloads += 1
        response = FakeBinaryResponse(request.full_url, 200, self.body, {"Content-Length": str(len(self.body))})
        self.responses.append(response)
        return response


class DownloaderParserTests(unittest.TestCase):
    def test_gallery_list_and_next_page_parsing(self) -> None:
        html = """
        <a href="/g/100/abcdef1234/">First</a>
        <a href="https://e-hentai.org/g/101/bcdefa2345/"><span>Second Gallery</span></a>
        <a id="unext" href="/?page=1">&gt;</a>
        """

        galleries = downloader.parse_gallery_links(html, "https://e-hentai.org/")

        self.assertEqual([gallery.gid for gallery in galleries], [100, 101])
        self.assertEqual(galleries[1].title, "Second Gallery")
        self.assertEqual(
            downloader.parse_next_list_url(html, "https://e-hentai.org/"),
            "https://e-hentai.org/?page=1",
        )

    def test_detail_and_page_parsing(self) -> None:
        detail_html = """
        <script>var gid = 100; var token = "abcdef1234";</script>
        <h1 id="gn">Sample Title</h1>
        <tr><td>Length:</td><td>2 pages</td></tr>
        <a href="https://e-hentai.org/s/ptoken1/100-1"><img alt="1"></a>
        <a href="https://e-hentai.org/s/ptoken2/100-2"><img alt="2"></a>
        """

        detail = downloader.parse_detail_html(detail_html, "https://e-hentai.org/g/100/abcdef1234/")

        self.assertEqual(detail.gid, 100)
        self.assertEqual(detail.token, "abcdef1234")
        self.assertEqual(detail.title, "Sample Title")
        self.assertEqual(detail.pages, 2)
        self.assertEqual(detail.page_tokens, {0: "ptoken1", 1: "ptoken2"})

        page_html = """
        <script>var showkey="show123";</script>
        <img id="img" src="https://ehgt.org/full/001.webp" style="max-width:100%">
        <a onclick="return nl('skip123')">skip</a>
        <a href="https://e-hentai.org/fullimg.php?gid=100&page=1">Download original</a>
        """
        info = downloader.parse_page_html(page_html)

        self.assertEqual(info.image_url, "https://ehgt.org/full/001.webp")
        self.assertEqual(info.show_key, "show123")
        self.assertEqual(info.skip_hath_key, "skip123")
        self.assertIn("fullimg", info.origin_image_url or "")

    def test_prompt_copy_url_is_not_treated_as_original_image(self) -> None:
        page_html = """
        <script>var showkey="show123";</script>
        <img id="img" src="https://ehgt.org/full/001.webp" style="max-width:100%">
        <a onclick="return nl('skip123')">skip</a>
        <a href="#" onclick="prompt('Copy the URL below.', 'https://e-hentai.org/r/bad/100-1/001.png')">
        """

        info = downloader.parse_page_html(page_html)

        self.assertEqual(info.image_url, "https://ehgt.org/full/001.webp")
        self.assertIsNone(info.origin_image_url)

    def test_archive_info_parsing_separates_original_and_resample(self) -> None:
        html = """
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

        archive_info = downloader.parse_archive_info(
            html,
            "https://e-hentai.org/archiver.php?gid=100&token=abcdef1234",
        )

        self.assertIsNotNone(archive_info.original)
        self.assertIsNotNone(archive_info.resample)
        assert archive_info.original is not None
        assert archive_info.resample is not None
        self.assertEqual(archive_info.original.cost, "Free!")
        self.assertEqual(archive_info.original.size, "18.46 MiB")
        self.assertEqual(archive_info.resample.cost, "20 GP")
        self.assertEqual(archive_info.resample.size, "8.00 MiB")
        self.assertEqual(
            archive_info.original.url,
            "https://e-hentai.org/archiver.php?gid=100&token=abcdef1234&or=abc",
        )

    def test_archive_redirect_and_download_link_parsing(self) -> None:
        self.assertEqual(
            downloader.parse_archive_continue_url(
                'document.location = "/archiver.php?gid=100&next=1";',
                "https://e-hentai.org/archiver.php",
            ),
            "https://e-hentai.org/archiver.php?gid=100&next=1",
        )
        self.assertEqual(
            downloader.parse_archive_final_download_url(
                '<a href="/archive.zip">Click Here To Start Downloading</a>',
                "https://e-hentai.org/archiver.php",
            ),
            "https://e-hentai.org/archive.zip",
        )

    def test_response_decode_preserves_chinese_title(self) -> None:
        raw = '<a href="/g/102/cdefab3456/">[中文翻译] [白杨汉化组]</a>'.encode("gb18030")

        decoded = downloader.decode_response_body(raw, {})

        self.assertIn("[中文翻译]", decoded)
        gallery = downloader.parse_gallery_links(decoded, "https://e-hentai.org/")[0]
        self.assertTrue(
            downloader.matches_gallery(
                gallery,
                argparse.Namespace(title_contains="中文翻译", title_regex=None),
            )
        )

    def test_title_regex_examples(self) -> None:
        gallery = downloader.Gallery(
            gid=4078149,
            token="9e1a06a282",
            title="(C86) [Uminoie Hamanasu] SPiCE!! (Touhou Project) [中文翻译] [白杨汉化组]",
            url="https://e-hentai.org/g/4078149/9e1a06a282/",
        )

        self.assertTrue(
            downloader.matches_gallery(
                gallery,
                argparse.Namespace(title_contains=None, title_regex=r"中文翻译.*白杨汉化组"),
            )
        )
        self.assertTrue(
            downloader.matches_gallery(
                gallery,
                argparse.Namespace(title_contains=None, title_regex=r"(?=.*Touhou)(?=.*中文翻译)"),
            )
        )
        self.assertFalse(
            downloader.matches_gallery(
                gallery,
                argparse.Namespace(title_contains=None, title_regex=r"^\(Reitaisai"),
            )
        )

    def test_title_filters_support_any_and_all(self) -> None:
        gallery = downloader.Gallery(
            gid=4078149,
            token="9e1a06a282",
            title="(C86) [Uminoie Hamanasu] SPiCE!! (Touhou Project) [中文翻译] [白杨汉化组]",
            url="https://e-hentai.org/g/4078149/9e1a06a282/",
        )

        self.assertTrue(
            downloader.matches_gallery(
                gallery,
                argparse.Namespace(title_contains=["中文翻译", "白杨汉化组"], title_regex=None, title_match="all"),
            )
        )
        self.assertFalse(
            downloader.matches_gallery(
                gallery,
                argparse.Namespace(title_contains=["不存在", "白杨汉化组"], title_regex=None, title_match="all"),
            )
        )
        self.assertTrue(
            downloader.matches_gallery(
                gallery,
                argparse.Namespace(title_contains=["不存在", "白杨汉化组"], title_regex=None, title_match="any"),
            )
        )
        self.assertTrue(
            downloader.matches_gallery(
                gallery,
                argparse.Namespace(title_contains=["白杨汉化组"], title_regex=[r"Touhou"], title_match="all"),
            )
        )
        self.assertTrue(
            downloader.matches_gallery(
                gallery,
                argparse.Namespace(title_contains=["不存在"], title_regex=[r"Touhou"], title_match="any"),
            )
        )

    def test_mixed_server_conditions_are_supported(self) -> None:
        args = downloader.build_parser().parse_args(
            ["--uploader", "alice", "--tag", "language:chinese$", "--source-match", "all"]
        )
        urls = downloader.EhClient.from_args(args, "").list_urls_from_args(args)

        self.assertEqual(len(urls), 2)
        self.assertIn("/uploader/alice", urls[0])
        self.assertIn("/tag/language%3Achinese%24", urls[1])

    def test_cookie_file_formats(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            raw_cookie = temp / "raw.txt"
            raw_cookie.write_text("ipb_member_id=1; ipb_pass_hash=abc", encoding="utf-8")
            self.assertEqual(
                downloader.parse_cookie_source(None, raw_cookie),
                "ipb_member_id=1; ipb_pass_hash=abc",
            )

            json_cookie = temp / "cookies.json"
            json_cookie.write_text(json.dumps({"a": "1", "b": "2"}), encoding="utf-8")
            self.assertEqual(downloader.parse_cookie_source(None, json_cookie), "a=1; b=2")

            netscape_cookie = temp / "cookies.txt"
            netscape_cookie.write_text(
                "# Netscape HTTP Cookie File\n.e-hentai.org\tTRUE\t/\tTRUE\t0\tigneous\tmystery\n",
                encoding="utf-8",
            )
            self.assertEqual(downloader.parse_cookie_source(None, netscape_cookie), "igneous=mystery")

    def test_cli_defaults_for_safe_test_mode(self) -> None:
        args = downloader.build_parser().parse_args(["--search", "touhou"])

        self.assertEqual(args.hosts, "system")
        self.assertEqual(args.proxy_mode, "system")
        self.assertEqual(args.retries, 3)
        self.assertEqual(args.delay, 0.0)
        self.assertEqual(args.gallery_workers, 1)
        self.assertEqual(args.page_workers, 3)
        self.assertEqual(args.download_mode, "pages")
        self.assertEqual(args.archive_connections, downloader.DEFAULT_ARCHIVE_CONNECTIONS)

    def test_content_range_and_split_ranges(self) -> None:
        self.assertEqual(downloader.parse_content_range_total("bytes 0-0/12345"), 12345)
        self.assertIsNone(downloader.parse_content_range_total("bytes 0-0/*"))
        self.assertEqual(downloader.split_ranges(10, 3, min_part_size=1), [(0, 3), (4, 6), (7, 9)])

    def test_incomplete_read_is_retryable_network_error(self) -> None:
        exc = IncompleteRead(b"partial", 10)

        self.assertTrue(isinstance(exc, downloader.NETWORK_ERRORS))
        self.assertTrue(downloader.is_retryable_error(exc))

    def test_category_names_are_converted_to_f_cats_exclusion_mask(self) -> None:
        args = downloader.build_parser().parse_args(
            [
                "--search",
                'language:chinese$ parody:"touhou project$"',
                "--category",
                "doujinshi",
                "--category",
                "manga",
                "--category",
                "non-h",
            ]
        )

        self.assertEqual(downloader.resolve_f_cats(args), 761)

        client = downloader.EhClient.from_args(args, "")
        url = client.list_url_from_args(args)
        self.assertIn("f_cats=761", url)
        self.assertIn("language%3Achinese%24", url)

    def test_multiple_uploaders_are_union_source_urls(self) -> None:
        args = downloader.build_parser().parse_args(["--uploader", "alice", "--uploader", "bob"])
        client = downloader.EhClient.from_args(args, "")

        urls = client.list_urls_from_args(args)

        self.assertEqual(len(urls), 2)
        self.assertIn("/uploader/alice", urls[0])
        self.assertIn("/uploader/bob", urls[1])

    def test_multiple_uploader_all_returns_source_urls_for_intersection(self) -> None:
        args = downloader.build_parser().parse_args(
            ["--uploader", "alice", "--uploader", "bob", "--source-match", "all"]
        )
        client = downloader.EhClient.from_args(args, "")

        urls = client.list_urls_from_args(args)

        self.assertEqual(len(urls), 2)
        self.assertIn("/uploader/alice", urls[0])
        self.assertIn("/uploader/bob", urls[1])

    def test_multiple_tags_can_be_intersected_as_all_sources(self) -> None:
        args = downloader.build_parser().parse_args(
            ["--tag", "language:chinese$", "--tag", 'parody:"touhou project$"', "--source-match", "all"]
        )
        client = downloader.EhClient.from_args(args, "")

        urls = client.list_urls_from_args(args)

        self.assertEqual(len(urls), 2)
        self.assertIn("/tag/language%3Achinese%24", urls[0])
        self.assertIn("/tag/parody%3A%22touhou%20project%24%22", urls[1])

    def test_category_names_can_be_comma_separated_or_raw_f_cats(self) -> None:
        comma_args = downloader.build_parser().parse_args(["--search", "touhou", "--category", "doujinshi,manga,non h"])
        self.assertEqual(downloader.resolve_f_cats(comma_args), 761)

        raw_args = downloader.build_parser().parse_args(["--search", "touhou", "--category", "761"])
        self.assertEqual(downloader.resolve_f_cats(raw_args), 761)

    def test_format_tag_query_matches_e_hentai_search_syntax(self) -> None:
        self.assertEqual(
            downloader.format_tag_query("parody", "touhou project", exact=True),
            'parody:"touhou project$"',
        )
        self.assertEqual(downloader.format_tag_query("language", "chinese", exact=True), "language:chinese$")

    def test_search_and_typed_tags_are_combined_into_one_search(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_file = Path(temp_dir) / "configs.json"
            config_file.write_text(
                json.dumps(
                    {
                        "configs": [
                            {
                                "name": "touhou-chinese",
                                "server_conditions": [
                                    {"type": "Search", "value": "language:chinese$"},
                                    {
                                        "type": "Tag",
                                        "value": 'parody:"touhou project$"',
                                        "prefix": "parody",
                                        "tag_value": "touhou project",
                                        "exact": "1",
                                    },
                                ],
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            tasks = downloader.load_task_definitions(config_file)
            args = tasks[0]["args"]

            self.assertEqual(args[args.index("--search") + 1], 'language:chinese$ parody:"touhou project$"')
            self.assertNotIn("--tag", args)

    def test_raw_tag_condition_without_legacy_metadata_stays_in_search(self) -> None:
        args = downloader.config_args_from_object(
            {"server_conditions": [{"type": "Tag", "value": "title:\"comic aun\" -title:2007"}]}
        )

        self.assertEqual(args, ["--search", 'title:"comic aun" -title:2007'])

    def test_detail_cache_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir)
            gallery = downloader.Gallery(100, "abcdef1234", "Cached Gallery", "https://e-hentai.org/g/100/abcdef1234/")
            detail = downloader.GalleryDetail(
                gid=100,
                token="abcdef1234",
                title="Cached Gallery",
                pages=2,
                page_tokens={0: "ptoken1", 1: "ptoken2"},
                preview_pages=1,
            )

            downloader.save_detail_cache(output, detail)
            cached = downloader.load_detail_cache(output, gallery)

            self.assertIsNotNone(cached)
            assert cached is not None
            self.assertEqual(cached.pages, 2)
            self.assertEqual(cached.page_tokens, {0: "ptoken1", 1: "ptoken2"})

    def test_image_validation_rejects_truncated_png_and_cleans_it_up(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target_base = Path(temp_dir) / "00000001"
            good = target_base.with_suffix(".png")
            good.write_bytes(tiny_png())
            self.assertTrue(downloader.validate_image_file(good))
            self.assertTrue(downloader.image_exists(target_base))

            broken = target_base.with_suffix(".png")
            broken.write_bytes(tiny_png()[:-8])
            self.assertFalse(downloader.validate_image_file(broken))
            self.assertFalse(downloader.image_exists(target_base, cleanup_invalid=True))
            self.assertFalse(broken.exists())

    def test_batch_summary_reports_failed_gallery_ids(self) -> None:
        lines = []
        original_safe_print = downloader.safe_print

        def capture(*values, **_kwargs) -> None:
            lines.append(" ".join(str(value) for value in values))

        try:
            downloader.safe_print = capture
            downloader.print_batch_summary(total=3, successes=2, failed_gids=[101])
        finally:
            downloader.safe_print = original_safe_print

        self.assertEqual(lines, ["[batch] completed with 1/3 failed gallery/galleries: 101"])

    def test_sanitize_filename_removes_windows_invalid_chars(self) -> None:
        self.assertEqual(
            downloader.sanitize_filename("3520844-Shinpan | 审判 (Touhou Project)"),
            "3520844-Shinpan  审判 (Touhou Project)",
        )

    def test_run_state_records_and_loads_failed_galleries(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir)
            failed_gallery = downloader.Gallery(
                gid=101,
                token="bcdefa2345",
                title="Failed Gallery",
                url="https://e-hentai.org/g/101/bcdefa2345/",
            )
            results = [
                downloader.GalleryRunResult(
                    gallery=downloader.Gallery(
                        gid=100,
                        token="abcdef1234",
                        title="OK Gallery",
                        url="https://e-hentai.org/g/100/abcdef1234/",
                    ),
                    ok=True,
                ),
                downloader.GalleryRunResult(gallery=failed_gallery, ok=False, error="network down"),
            ]

            downloader.write_run_state(output, "touhou", "2026-07-28T00:00:00+08:00", "search:touhou", results)

            loaded = downloader.load_failed_galleries(output, "touhou")
            self.assertEqual([gallery.gid for gallery in loaded], [101])
            self.assertTrue((output / ".eh_batch_state" / "touhou-failures.txt").exists())

    def test_archive_mode_skips_existing_valid_zip_and_records_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir)
            target = output / "100-Archive Title.zip"
            write_tiny_zip(target)
            gallery = downloader.Gallery(100, "abcdef1234", "Archive Title", "https://e-hentai.org/g/100/abcdef1234/")
            args = argparse.Namespace(output=str(output), overwrite=False, download_mode="archive-original")

            class FakeClient:
                def __init__(self) -> None:
                    self.requested_download = False

                def collect_basic_detail(self, _gallery):
                    return downloader.GalleryDetail(100, "abcdef1234", "Archive Title", 1, {}, 1)

                def fetch_archive_info(self, _detail):
                    return downloader.ArchiveInfo(
                        url="https://e-hentai.org/archiver.php?gid=100&token=abcdef1234",
                        original=downloader.ArchiveOption(
                            kind="original",
                            url="https://e-hentai.org/archiver.php?gid=100&token=abcdef1234&or=abc",
                            dltype="org",
                            dlcheck="Download Original Archive",
                            cost="Free!",
                            size="1 KiB",
                        ),
                    )

                def request_archive_download_url(self, _archive_info, _option):
                    self.requested_download = True
                    return "https://ehgt.org/archive.zip"

                def download_archive_file(self, *_args, **_kwargs):
                    raise AssertionError("existing valid archive should be skipped")

            client = FakeClient()

            result = downloader.download_gallery_archive(client, gallery, args)

            self.assertTrue(result.skipped)
            self.assertFalse(client.requested_download)
            metadata_path = output / ".eh_batch_state" / "archive-metadata" / "100-abcdef1234.json"
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            self.assertEqual(metadata["archive_cost"], "Free!")
            self.assertTrue(metadata["skipped"])

    def test_archive_segmented_download_uses_range_and_validates_zip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir)
            target = output / "archive.zip"
            body = make_stored_zip_bytes()
            client = downloader.EhClient("e", "", 30, 0, 0, "direct", None)
            opener = FakeRangeOpener(body, supports_range=True)
            client.opener = opener
            progress_events = []

            saved, skipped = client.download_archive_file(
                "https://ehgt.org/archive.zip",
                "https://e-hentai.org/archiver.php",
                target,
                overwrite=False,
                connections=4,
                progress_callback=lambda done, total: progress_events.append((done, total)),
            )

            self.assertEqual(saved, target)
            self.assertFalse(skipped)
            self.assertTrue(downloader.validate_zip_file(target))
            self.assertGreaterEqual(opener.range_downloads, 2)
            self.assertEqual(opener.full_downloads, 0)
            self.assertEqual(progress_events[-1], (len(body), len(body)))
            self.assertEqual(client.last_archive_connections, 4)

    def test_archive_segmented_download_falls_back_to_single_stream(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "archive.zip"
            body = make_stored_zip_bytes()
            client = downloader.EhClient("e", "", 30, 0, 0, "direct", None)
            opener = FakeRangeOpener(body, supports_range=False)
            client.opener = opener

            saved, skipped = client.download_archive_file(
                "https://ehgt.org/archive.zip",
                "https://e-hentai.org/archiver.php",
                target,
                overwrite=False,
                connections=4,
            )

            self.assertEqual(saved, target)
            self.assertFalse(skipped)
            self.assertTrue(downloader.validate_zip_file(target))
            self.assertEqual(opener.full_downloads, 2)
            self.assertEqual(client.last_archive_connections, 1)
            self.assertIn("HTTP 200", client.last_archive_fallback_reason)
            self.assertTrue(opener.responses[-1].read_sizes)
            self.assertTrue(
                all(size == downloader.NETWORK_READ_CHUNK_SIZE for size in opener.responses[-1].read_sizes)
            )

    def test_small_image_download_reads_entire_body_when_length_is_known(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir)
            target_base = output / "image"
            body = tiny_png()
            client = downloader.EhClient("e", "", 30, 0, 0, "direct", None)
            opener = FakeRangeOpener(body, supports_range=False)
            client.opener = opener

            saved = client.download_file(
                "https://ehgt.org/image.png",
                "https://e-hentai.org/s/abc/100-1",
                target_base,
                overwrite=False,
            )

            self.assertEqual(saved, target_base.with_suffix(".png"))
            self.assertEqual(opener.responses[-1].read_sizes, [-1])

    def test_original_resolve_404_falls_back_to_displayed_image(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir)
            gallery = downloader.Gallery(100, "abcdef1234", "Original Fallback", "https://e-hentai.org/g/100/abcdef1234/")
            args = argparse.Namespace(
                output=str(output),
                max_image_pages=0,
                overwrite=False,
                page_workers=1,
                html_pages=True,
                original=True,
                delay=0.0,
            )

            class FakeClient:
                def __init__(self) -> None:
                    self.downloaded_urls = []

                def collect_detail(self, _gallery):
                    return downloader.GalleryDetail(100, "abcdef1234", "Original Fallback", 1, {0: "ptoken"}, 1)

                def detail_url(self, gid, token):
                    return f"https://e-hentai.org/g/{gid}/{token}/"

                def page_url(self, gid, index, ptoken):
                    return f"https://e-hentai.org/s/{ptoken}/{gid}-{index + 1}"

                def fetch_page_info(self, *_args, **_kwargs):
                    return (
                        downloader.PageInfo(
                            image_url="https://ehgt.org/normal.png",
                            origin_image_url="https://e-hentai.org/fullimg.php?gid=100&page=1",
                        ),
                        None,
                    )

                def resolve_original_url(self, info, _referer):
                    raise downloader.HTTPError(info.origin_image_url or "", 404, "Not Found", None, None)

                def download_file(self, url, _referer, target_base, overwrite):
                    self.downloaded_urls.append(url)
                    path = target_base.with_suffix(".png")
                    path.write_bytes(tiny_png())
                    return path

            client = FakeClient()

            downloader.download_gallery(client, gallery, args)

            self.assertEqual(client.downloaded_urls, ["https://ehgt.org/normal.png"])

    def test_original_download_404_falls_back_to_displayed_image(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir)
            gallery = downloader.Gallery(100, "abcdef1234", "Original Download Fallback", "https://e-hentai.org/g/100/abcdef1234/")
            args = argparse.Namespace(
                output=str(output),
                max_image_pages=0,
                overwrite=False,
                page_workers=1,
                html_pages=True,
                original=True,
                delay=0.0,
            )

            class FakeClient:
                def __init__(self) -> None:
                    self.downloaded_urls = []

                def collect_detail(self, _gallery):
                    return downloader.GalleryDetail(100, "abcdef1234", "Original Download Fallback", 1, {0: "ptoken"}, 1)

                def detail_url(self, gid, token):
                    return f"https://e-hentai.org/g/{gid}/{token}/"

                def page_url(self, gid, index, ptoken):
                    return f"https://e-hentai.org/s/{ptoken}/{gid}-{index + 1}"

                def fetch_page_info(self, *_args, **_kwargs):
                    return (
                        downloader.PageInfo(
                            image_url="https://ehgt.org/normal.png",
                            origin_image_url="https://e-hentai.org/fullimg.php?gid=100&page=1",
                        ),
                        None,
                    )

                def resolve_original_url(self, _info, _referer):
                    return "https://ehgt.org/original.png"

                def download_file(self, url, _referer, target_base, overwrite):
                    self.downloaded_urls.append(url)
                    if url.endswith("original.png"):
                        raise downloader.HTTPError(url, 404, "Not Found", None, None)
                    path = target_base.with_suffix(".png")
                    path.write_bytes(tiny_png())
                    return path

            client = FakeClient()

            downloader.download_gallery(client, gallery, args)

            self.assertEqual(client.downloaded_urls, ["https://ehgt.org/original.png", "https://ehgt.org/normal.png"])

    def test_cached_page_token_404_refreshes_detail_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir)
            gallery = downloader.Gallery(100, "abcdef1234", "Cached Token Refresh", "https://e-hentai.org/g/100/abcdef1234/")
            stale = downloader.GalleryDetail(100, "abcdef1234", "Cached Token Refresh", 1, {0: "stale"}, 1)
            downloader.save_detail_cache(output, stale)
            args = argparse.Namespace(
                output=str(output),
                max_image_pages=0,
                overwrite=False,
                page_workers=1,
                html_pages=True,
                original=False,
                delay=0.0,
            )

            class FakeClient:
                def __init__(self) -> None:
                    self.collect_detail_calls = 0
                    self.page_tokens = []

                def collect_detail(self, _gallery):
                    self.collect_detail_calls += 1
                    return downloader.GalleryDetail(100, "abcdef1234", "Cached Token Refresh", 1, {0: "fresh"}, 1)

                def detail_url(self, gid, token):
                    return f"https://e-hentai.org/g/{gid}/{token}/"

                def page_url(self, gid, index, ptoken):
                    return f"https://e-hentai.org/s/{ptoken}/{gid}-{index + 1}"

                def fetch_page_info(self, gid, _token, index, ptoken, *_args, **_kwargs):
                    self.page_tokens.append(ptoken)
                    if ptoken == "stale":
                        raise downloader.HTTPError(self.page_url(gid, index, ptoken), 404, "Not Found", None, None)
                    return downloader.PageInfo(image_url="https://ehgt.org/normal.png"), None

                def download_file(self, url, _referer, target_base, overwrite):
                    path = target_base.with_suffix(".png")
                    path.write_bytes(tiny_png())
                    return path

            client = FakeClient()

            downloader.download_gallery(client, gallery, args)

            self.assertEqual(client.collect_detail_calls, 1)
            self.assertEqual(client.page_tokens, ["stale", "fresh"])

    def test_task_file_accepts_multiple_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            task_file = Path(temp_dir) / "tasks.json"
            task_file.write_text(
                json.dumps(
                    {
                        "tasks": [
                            {
                                "name": "touhou",
                                "enabled": True,
                                "interval_minutes": 30,
                                "args": ["--search", "touhou", "--output", "F:\\eh_downloads"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            tasks = downloader.load_task_definitions(task_file)

            self.assertEqual(tasks[0]["name"], "touhou")
            self.assertEqual(tasks[0]["args"], ["--search", "touhou", "--output", "F:\\eh_downloads"])

    def test_config_file_accepts_typed_configs_and_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_file = Path(temp_dir) / "configs.json"
            config_file.write_text(
                json.dumps(
                    {
                        "defaults": {
                            "site": "e",
                            "output": "F:\\eh_downloads",
                            "download_mode": "archive-original",
                            "archive_connections": 8,
                            "cookie_file": "F:\\WORK\\codex\\eh_cookies.txt",
                            "keep_going": True,
                        },
                        "configs": [
                            {
                                "name": "uploader-a",
                                "source_type": "Uploader",
                                "source_value": ["someone", "another"],
                                "source_match": "any",
                                "categories": ["doujinshi", "manga"],
                                "title_contains": ["白杨汉化组", "中文"],
                                "title_match": "any",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            tasks = downloader.load_task_definitions(config_file)

            self.assertEqual(tasks[0]["name"], "uploader-a")
            args = tasks[0]["args"]
            self.assertIn("--uploader", args)
            self.assertIn("someone", args)
            self.assertIn("another", args)
            self.assertIn("--archive-connections", args)
            self.assertIn("8", args)
            parsed = downloader.build_parser().parse_args(args)
            self.assertEqual(parsed.download_mode, "archive-original")
            self.assertEqual(parsed.archive_connections, 8)
            self.assertEqual(parsed.categories, ["doujinshi", "manga"])
            self.assertEqual(parsed.source_match, "any")
            self.assertEqual(parsed.title_match, "any")
            self.assertEqual(parsed.title_contains, ["白杨汉化组", "中文"])


if __name__ == "__main__":
    unittest.main()
