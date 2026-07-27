import argparse
import json
import struct
import tempfile
import unittest
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

    def test_category_names_can_be_comma_separated_or_raw_f_cats(self) -> None:
        comma_args = downloader.build_parser().parse_args(["--search", "touhou", "--category", "doujinshi,manga,non h"])
        self.assertEqual(downloader.resolve_f_cats(comma_args), 761)

        raw_args = downloader.build_parser().parse_args(["--search", "touhou", "--category", "761"])
        self.assertEqual(downloader.resolve_f_cats(raw_args), 761)

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


if __name__ == "__main__":
    unittest.main()
