#!/usr/bin/env python3
"""
Tkinter GUI wrapper for eh_batch_downloader.py.

The GUI deliberately launches the downloader in a child Python process. That
keeps the interface responsive and lets the Stop button terminate an active run.
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
import json
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import eh_batch_downloader

IS_FROZEN = bool(getattr(sys, "frozen", False))
APP_DIR = Path(sys.executable).resolve().parent if IS_FROZEN else Path(__file__).resolve().parent
GUI_SCRIPT = APP_DIR / "eh_batch_gui.py"
CHILD_FLAG = "--gui-child"
SETTINGS_PATH = Path(os.environ.get("APPDATA") or Path.home()) / "EHBatchDownloader" / "settings.json"
CATEGORY_CHOICES = (
    ("Doujinshi", "doujinshi"),
    ("Manga", "manga"),
    ("Artist CG", "artistcg"),
    ("Game CG", "gamecg"),
    ("Western", "western"),
    ("Non-H", "non-h"),
    ("Image Set", "imageset"),
    ("Cosplay", "cosplay"),
    ("Asian Porn", "asianporn"),
    ("Misc", "misc"),
)


class BatchDownloaderGui(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"E-Hentai Batch Downloader {eh_batch_downloader.APP_VERSION}")
        self.geometry("980x720")
        self.minsize(860, 600)

        self.log_queue: "queue.Queue[str]" = queue.Queue()
        self.process: subprocess.Popen[str] | None = None
        self.reader_thread: threading.Thread | None = None

        self.source_type = tk.StringVar(value="Search")
        self.source_value = tk.StringVar()
        self.site = tk.StringVar(value="E-Hentai")
        self.title_contains = tk.StringVar()
        self.title_regex = tk.StringVar()
        self.max_list_pages = tk.StringVar(value="1")
        self.max_galleries = tk.StringVar(value="0")
        self.max_image_pages = tk.StringVar(value="0")
        self.output_dir = tk.StringVar(value=default_output_dir())
        self.cookie_file = tk.StringVar()
        self.cookie_text = tk.StringVar()
        self.timeout = tk.StringVar(value="60")
        self.delay = tk.StringVar(value="0")
        self.retries = tk.StringVar(value="3")
        self.retry_backoff = tk.StringVar(value="2")
        self.gallery_workers = tk.StringVar(value="1")
        self.page_workers = tk.StringVar(value="3")
        self.hosts = tk.StringVar(value="System DNS")
        self.proxy_mode = tk.StringVar(value="System Proxy")
        self.proxy_url = tk.StringVar()
        self.original = tk.BooleanVar(value=False)
        self.html_pages = tk.BooleanVar(value=False)
        self.overwrite = tk.BooleanVar(value=False)
        self.keep_going = tk.BooleanVar(value=True)
        self.category_vars = {value: tk.BooleanVar(value=True) for _label, value in CATEGORY_CHOICES}

        self._load_settings()
        self._build_ui()
        self._poll_log_queue()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(5, weight=1)

        main = ttk.Frame(self, padding=12)
        main.grid(row=0, column=0, sticky="nsew")
        main.columnconfigure(1, weight=1)
        main.columnconfigure(3, weight=1)

        ttk.Label(main, text="Source").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=4)
        source_box = ttk.Combobox(
            main,
            textvariable=self.source_type,
            values=("Search", "Uploader", "Tag", "List URL"),
            state="readonly",
            width=14,
        )
        source_box.grid(row=0, column=1, sticky="w", pady=4)
        source_box.bind("<<ComboboxSelected>>", lambda _event: self._update_source_hint())

        self.source_label = ttk.Label(main, text="Keywords")
        self.source_label.grid(row=1, column=0, sticky="w", padx=(0, 8), pady=4)
        ttk.Entry(main, textvariable=self.source_value).grid(row=1, column=1, columnspan=3, sticky="ew", pady=4)

        ttk.Label(main, text="Site").grid(row=0, column=2, sticky="e", padx=(16, 8), pady=4)
        ttk.Combobox(
            main,
            textvariable=self.site,
            values=("E-Hentai", "ExHentai"),
            state="readonly",
            width=14,
        ).grid(row=0, column=3, sticky="w", pady=4)

        ttk.Label(main, text="Title Contains").grid(row=2, column=0, sticky="w", padx=(0, 8), pady=4)
        ttk.Entry(main, textvariable=self.title_contains).grid(row=2, column=1, sticky="ew", pady=4)
        ttk.Label(main, text="Title Regex").grid(row=2, column=2, sticky="e", padx=(16, 8), pady=4)
        ttk.Entry(main, textvariable=self.title_regex).grid(row=2, column=3, sticky="ew", pady=4)

        categories = ttk.LabelFrame(main, text="Categories", padding=(8, 6))
        categories.grid(row=3, column=0, columnspan=4, sticky="ew", pady=(6, 0))
        for index, (label, value) in enumerate(CATEGORY_CHOICES):
            ttk.Checkbutton(categories, text=label, variable=self.category_vars[value]).grid(
                row=index // 5,
                column=index % 5,
                sticky="w",
                padx=(0, 12),
                pady=2,
            )
        category_buttons = ttk.Frame(categories)
        category_buttons.grid(row=2, column=0, columnspan=5, sticky="w", pady=(4, 0))
        ttk.Button(category_buttons, text="All", command=lambda: self._set_all_categories(True)).grid(
            row=0,
            column=0,
            padx=(0, 8),
        )
        ttk.Button(category_buttons, text="Doujinshi/Manga/Non-H", command=self._select_common_story_categories).grid(
            row=0,
            column=1,
            padx=(0, 8),
        )

        limits = ttk.LabelFrame(self, text="Limits", padding=12)
        limits.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 8))
        for index in range(6):
            limits.columnconfigure(index, weight=1)

        ttk.Label(limits, text="List Pages").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=4)
        ttk.Entry(limits, textvariable=self.max_list_pages, width=8).grid(row=0, column=1, sticky="w", pady=4)
        ttk.Label(limits, text="Galleries").grid(row=0, column=2, sticky="w", padx=(12, 8), pady=4)
        ttk.Entry(limits, textvariable=self.max_galleries, width=8).grid(row=0, column=3, sticky="w", pady=4)
        ttk.Label(limits, text="Image Pages").grid(row=0, column=4, sticky="w", padx=(12, 8), pady=4)
        ttk.Entry(limits, textvariable=self.max_image_pages, width=8).grid(row=0, column=5, sticky="w", pady=4)

        ttk.Label(limits, text="Delay").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=4)
        ttk.Entry(limits, textvariable=self.delay, width=8).grid(row=1, column=1, sticky="w", pady=4)
        ttk.Label(limits, text="Timeout").grid(row=1, column=2, sticky="w", padx=(12, 8), pady=4)
        ttk.Entry(limits, textvariable=self.timeout, width=8).grid(row=1, column=3, sticky="w", pady=4)
        ttk.Label(limits, text="Retries").grid(row=1, column=4, sticky="w", padx=(12, 8), pady=4)
        ttk.Entry(limits, textvariable=self.retries, width=8).grid(row=1, column=5, sticky="w", pady=4)

        ttk.Label(limits, text="Backoff").grid(row=2, column=0, sticky="w", padx=(0, 8), pady=4)
        ttk.Entry(limits, textvariable=self.retry_backoff, width=8).grid(row=2, column=1, sticky="w", pady=4)
        ttk.Label(limits, text="Gallery Workers").grid(row=2, column=2, sticky="w", padx=(12, 8), pady=4)
        ttk.Entry(limits, textvariable=self.gallery_workers, width=8).grid(row=2, column=3, sticky="w", pady=4)
        ttk.Label(limits, text="Page Workers").grid(row=2, column=4, sticky="w", padx=(12, 8), pady=4)
        ttk.Entry(limits, textvariable=self.page_workers, width=8).grid(row=2, column=5, sticky="w", pady=4)

        options = ttk.Frame(limits)
        options.grid(row=3, column=0, columnspan=6, sticky="w", pady=4)
        ttk.Checkbutton(options, text="Original", variable=self.original).grid(row=0, column=0, padx=(0, 10))
        ttk.Checkbutton(options, text="HTML Pages", variable=self.html_pages).grid(row=0, column=1, padx=(0, 10))
        ttk.Checkbutton(options, text="Overwrite", variable=self.overwrite).grid(row=0, column=2, padx=(0, 10))
        ttk.Checkbutton(options, text="Keep Going", variable=self.keep_going).grid(row=0, column=3)

        network = ttk.LabelFrame(self, text="Network", padding=12)
        network.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 8))
        network.columnconfigure(5, weight=1)

        ttk.Label(network, text="Hosts").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=4)
        ttk.Combobox(
            network,
            textvariable=self.hosts,
            values=("System DNS", "EhViewer Built-in Hosts"),
            state="readonly",
            width=22,
        ).grid(row=0, column=1, sticky="w", pady=4)

        ttk.Label(network, text="Proxy").grid(row=0, column=2, sticky="w", padx=(16, 8), pady=4)
        proxy_box = ttk.Combobox(
            network,
            textvariable=self.proxy_mode,
            values=("System Proxy", "Direct", "HTTP Proxy"),
            state="readonly",
            width=16,
        )
        proxy_box.grid(row=0, column=3, sticky="w", pady=4)
        proxy_box.bind("<<ComboboxSelected>>", lambda _event: self._update_proxy_hint())

        self.proxy_url_label = ttk.Label(network, text="Proxy URL")
        self.proxy_url_label.grid(row=0, column=4, sticky="w", padx=(16, 8), pady=4)
        self.proxy_url_entry = ttk.Entry(network, textvariable=self.proxy_url)
        self.proxy_url_entry.grid(row=0, column=5, sticky="ew", pady=4)

        paths = ttk.LabelFrame(self, text="Paths And Login", padding=12)
        paths.grid(row=3, column=0, sticky="ew", padx=12, pady=(0, 8))
        paths.columnconfigure(1, weight=1)

        ttk.Label(paths, text="Output").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=4)
        ttk.Entry(paths, textvariable=self.output_dir).grid(row=0, column=1, sticky="ew", pady=4)
        ttk.Button(paths, text="Browse", command=self._choose_output).grid(row=0, column=2, padx=(8, 0), pady=4)

        ttk.Label(paths, text="Cookie File").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=4)
        ttk.Entry(paths, textvariable=self.cookie_file).grid(row=1, column=1, sticky="ew", pady=4)
        ttk.Button(paths, text="Browse", command=self._choose_cookie_file).grid(row=1, column=2, padx=(8, 0), pady=4)

        ttk.Label(paths, text="Cookie Header").grid(row=2, column=0, sticky="w", padx=(0, 8), pady=4)
        ttk.Entry(paths, textvariable=self.cookie_text, show="*").grid(row=2, column=1, sticky="ew", pady=4)

        buttons = ttk.Frame(self, padding=(12, 0, 12, 8))
        buttons.grid(row=4, column=0, sticky="ew")
        buttons.columnconfigure(5, weight=1)

        self.preview_button = ttk.Button(buttons, text="Preview", command=lambda: self._start_process(dry_run=True))
        self.preview_button.grid(row=0, column=0, padx=(0, 8))
        self.start_button = ttk.Button(buttons, text="Start Download", command=lambda: self._start_process(dry_run=False))
        self.start_button.grid(row=0, column=1, padx=(0, 8))
        self.stop_button = ttk.Button(buttons, text="Stop", command=self._stop_process, state="disabled")
        self.stop_button.grid(row=0, column=2, padx=(0, 8))
        ttk.Button(buttons, text="Clear Log", command=self._clear_log).grid(row=0, column=3, padx=(0, 8))
        ttk.Button(buttons, text="Self Test", command=self._run_self_test).grid(row=0, column=4, padx=(0, 8))

        log_frame = ttk.LabelFrame(self, text="Log", padding=8)
        log_frame.grid(row=5, column=0, sticky="nsew", padx=12, pady=(0, 12))
        self.rowconfigure(5, weight=1)
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)

        self.log_text = tk.Text(log_frame, wrap="word", height=16)
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scroll.set)

        self._update_source_hint()
        self._update_proxy_hint()

    def _update_source_hint(self) -> None:
        labels = {
            "Search": "Keywords",
            "Uploader": "Uploader",
            "Tag": "Tag",
            "List URL": "URL",
        }
        self.source_label.configure(text=labels.get(self.source_type.get(), "Value"))

    def _update_proxy_hint(self) -> None:
        uses_http_proxy = self.proxy_mode.get() == "HTTP Proxy"
        state = "normal" if uses_http_proxy else "disabled"
        self.proxy_url_entry.configure(state=state)

    def _choose_output(self) -> None:
        directory = filedialog.askdirectory(initialdir=self.output_dir.get() or str(APP_DIR))
        if directory:
            self.output_dir.set(directory)

    def _choose_cookie_file(self) -> None:
        filename = filedialog.askopenfilename(
            title="Select cookie file",
            filetypes=(("Cookie or text files", "*.txt *.json *.cookies"), ("All files", "*.*")),
        )
        if filename:
            self.cookie_file.set(filename)

    def _run_self_test(self) -> None:
        self._start_process(dry_run=False, extra_args=["--self-test"])

    def _build_command(self, dry_run: bool, extra_args: list[str] | None = None) -> tuple[list[str], dict[str, str]]:
        if not is_frozen_app() and not GUI_SCRIPT.exists():
            raise ValueError(f"GUI script not found: {GUI_SCRIPT}")

        cmd = build_child_command()
        env = self._build_child_env()
        if extra_args:
            cmd.extend(extra_args)
            return cmd, env

        source_type = self.source_type.get()
        source_value = self.source_value.get().strip()
        if source_type == "Search":
            if source_value:
                cmd.extend(["--search", source_value])
        elif source_type == "Uploader":
            if not source_value:
                raise ValueError("Uploader is required.")
            cmd.extend(["--uploader", source_value])
        elif source_type == "Tag":
            if not source_value:
                raise ValueError("Tag is required.")
            cmd.extend(["--tag", source_value])
        else:
            if not source_value:
                raise ValueError("List URL is required.")
            cmd.extend(["--url", source_value])

        cmd.extend(["--site", "ex" if self.site.get() == "ExHentai" else "e"])
        cmd.extend(["--max-list-pages", str(parse_positive_int(self.max_list_pages.get(), "List Pages", minimum=1))])
        cmd.extend(["--max-galleries", str(parse_positive_int(self.max_galleries.get(), "Galleries", minimum=0))])
        cmd.extend(["--max-image-pages", str(parse_positive_int(self.max_image_pages.get(), "Image Pages", minimum=0))])
        cmd.extend(["--timeout", str(parse_positive_float(self.timeout.get(), "Timeout", minimum=1.0))])
        cmd.extend(["--delay", str(parse_positive_float(self.delay.get(), "Delay", minimum=0.0))])
        cmd.extend(["--retries", str(parse_positive_int(self.retries.get(), "Retries", minimum=0))])
        cmd.extend(["--retry-backoff", str(parse_positive_float(self.retry_backoff.get(), "Backoff", minimum=0.0))])
        cmd.extend(["--gallery-workers", str(parse_positive_int(self.gallery_workers.get(), "Gallery Workers", minimum=1))])
        cmd.extend(["--page-workers", str(parse_positive_int(self.page_workers.get(), "Page Workers", minimum=1))])
        cmd.extend(["--output", normalize_output_dir(self.output_dir.get())])
        for category in self._selected_categories():
            cmd.extend(["--category", category])

        cmd.extend(["--hosts", "builtin" if self.hosts.get() == "EhViewer Built-in Hosts" else "system"])
        proxy_mode = self.proxy_mode.get()
        if proxy_mode == "Direct":
            cmd.extend(["--proxy-mode", "direct"])
        elif proxy_mode == "HTTP Proxy":
            proxy_url = self.proxy_url.get().strip()
            if not proxy_url:
                raise ValueError("Proxy URL is required when Proxy is HTTP Proxy.")
            cmd.extend(["--proxy-mode", "http", "--proxy-url", proxy_url])
        else:
            cmd.extend(["--proxy-mode", "system"])

        title_contains = self.title_contains.get().strip()
        if title_contains:
            cmd.extend(["--title-contains", title_contains])
        title_regex = self.title_regex.get().strip()
        if title_regex:
            cmd.extend(["--title-regex", title_regex])

        cookie_file = self.cookie_file.get().strip()
        if cookie_file:
            cmd.extend(["--cookie-file", cookie_file])
        if self.original.get():
            cmd.append("--original")
        if self.html_pages.get():
            cmd.append("--html-pages")
        if self.overwrite.get():
            cmd.append("--overwrite")
        if self.keep_going.get():
            cmd.append("--keep-going")
        if dry_run:
            cmd.append("--dry-run")

        cookie_text = self.cookie_text.get().strip()
        if cookie_text:
            env["EH_COOKIES"] = cookie_text
        return cmd, env

    def _build_child_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        return env

    def _selected_categories(self) -> list[str]:
        selected = [value for _label, value in CATEGORY_CHOICES if self.category_vars[value].get()]
        if not selected:
            raise ValueError("Select at least one category.")
        if len(selected) == len(CATEGORY_CHOICES):
            return []
        return selected

    def _set_all_categories(self, checked: bool) -> None:
        for variable in self.category_vars.values():
            variable.set(checked)

    def _select_common_story_categories(self) -> None:
        selected = {"doujinshi", "manga", "non-h"}
        for value, variable in self.category_vars.items():
            variable.set(value in selected)

    def _settings_data(self) -> dict[str, object]:
        return {
            "source_type": self.source_type.get(),
            "source_value": self.source_value.get(),
            "site": self.site.get(),
            "title_contains": self.title_contains.get(),
            "title_regex": self.title_regex.get(),
            "max_list_pages": self.max_list_pages.get(),
            "max_galleries": self.max_galleries.get(),
            "max_image_pages": self.max_image_pages.get(),
            "output_dir": self.output_dir.get(),
            "cookie_file": self.cookie_file.get(),
            "timeout": self.timeout.get(),
            "delay": self.delay.get(),
            "retries": self.retries.get(),
            "retry_backoff": self.retry_backoff.get(),
            "gallery_workers": self.gallery_workers.get(),
            "page_workers": self.page_workers.get(),
            "hosts": self.hosts.get(),
            "proxy_mode": self.proxy_mode.get(),
            "proxy_url": self.proxy_url.get(),
            "original": self.original.get(),
            "html_pages": self.html_pages.get(),
            "overwrite": self.overwrite.get(),
            "keep_going": self.keep_going.get(),
            "categories": self._selected_categories_for_settings(),
        }

    def _selected_categories_for_settings(self) -> list[str]:
        return [value for _label, value in CATEGORY_CHOICES if self.category_vars[value].get()]

    def _load_settings(self) -> None:
        try:
            data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(data, dict):
            return

        string_targets = {
            "source_type": self.source_type,
            "source_value": self.source_value,
            "site": self.site,
            "title_contains": self.title_contains,
            "title_regex": self.title_regex,
            "max_list_pages": self.max_list_pages,
            "max_galleries": self.max_galleries,
            "max_image_pages": self.max_image_pages,
            "output_dir": self.output_dir,
            "cookie_file": self.cookie_file,
            "timeout": self.timeout,
            "delay": self.delay,
            "retries": self.retries,
            "retry_backoff": self.retry_backoff,
            "gallery_workers": self.gallery_workers,
            "page_workers": self.page_workers,
            "hosts": self.hosts,
            "proxy_mode": self.proxy_mode,
            "proxy_url": self.proxy_url,
        }
        for key, variable in string_targets.items():
            value = data.get(key)
            if isinstance(value, str):
                if key == "output_dir":
                    value = normalize_output_dir(value)
                variable.set(value)

        bool_targets = {
            "original": self.original,
            "html_pages": self.html_pages,
            "overwrite": self.overwrite,
            "keep_going": self.keep_going,
        }
        for key, variable in bool_targets.items():
            value = data.get(key)
            if isinstance(value, bool):
                variable.set(value)

        categories = data.get("categories")
        if isinstance(categories, list):
            selected = {str(value) for value in categories}
            valid = {value for _label, value in CATEGORY_CHOICES}
            if selected and selected.issubset(valid):
                for value, variable in self.category_vars.items():
                    variable.set(value in selected)

    def _save_settings(self) -> None:
        try:
            SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
            temp = SETTINGS_PATH.with_suffix(SETTINGS_PATH.suffix + ".part")
            temp.write_text(json.dumps(self._settings_data(), ensure_ascii=False, indent=2), encoding="utf-8")
            temp.replace(SETTINGS_PATH)
        except OSError as exc:
            self._append_log(f"\nCould not save settings: {exc}\n")

    def _on_close(self) -> None:
        self._save_settings()
        if self.process and self.process.poll() is None:
            self.process.terminate()
        self.destroy()

    def _start_process(self, dry_run: bool, extra_args: list[str] | None = None) -> None:
        if self.process and self.process.poll() is None:
            messagebox.showinfo("Running", "A task is already running.")
            return
        try:
            cmd, env = self._build_command(dry_run=dry_run, extra_args=extra_args)
        except ValueError as exc:
            messagebox.showerror("Invalid Input", str(exc))
            return

        if not extra_args:
            self._save_settings()
        self._set_running(True)
        self._append_log("> " + safe_command_for_log(cmd) + "\n")
        try:
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=str(APP_DIR),
                env=env,
            )
        except OSError as exc:
            self._set_running(False)
            messagebox.showerror("Launch Failed", str(exc))
            return

        self.reader_thread = threading.Thread(target=self._read_process_output, daemon=True)
        self.reader_thread.start()

    def _read_process_output(self) -> None:
        process = self.process
        if not process or process.stdout is None:
            return
        for line in process.stdout:
            self.log_queue.put(line)
        return_code = process.wait()
        self.log_queue.put(f"\nProcess exited with code {return_code}\n")
        self.log_queue.put("__PROCESS_DONE__")

    def _stop_process(self) -> None:
        if not self.process or self.process.poll() is not None:
            return
        self._append_log("\nStopping process...\n")
        self.process.terminate()

    def _set_running(self, running: bool) -> None:
        state = "disabled" if running else "normal"
        self.preview_button.configure(state=state)
        self.start_button.configure(state=state)
        self.stop_button.configure(state="normal" if running else "disabled")

    def _poll_log_queue(self) -> None:
        try:
            while True:
                item = self.log_queue.get_nowait()
                if item == "__PROCESS_DONE__":
                    self._set_running(False)
                else:
                    self._append_log(item)
        except queue.Empty:
            pass
        self.after(100, self._poll_log_queue)

    def _append_log(self, text: str) -> None:
        self.log_text.insert("end", text)
        self.log_text.see("end")

    def _clear_log(self) -> None:
        self.log_text.delete("1.0", "end")


def parse_positive_int(value: str, field: str, minimum: int) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be an integer.") from exc
    if parsed < minimum:
        raise ValueError(f"{field} must be >= {minimum}.")
    return parsed


def parse_positive_float(value: str, field: str, minimum: float) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be a number.") from exc
    if parsed < minimum:
        raise ValueError(f"{field} must be >= {minimum}.")
    return parsed


def safe_command_for_log(cmd: list[str]) -> str:
    return " ".join(quote_arg(part) for part in cmd)


def quote_arg(value: str) -> str:
    if re_match_safe_arg(value):
        return value
    return '"' + value.replace('"', '\\"') + '"'


def re_match_safe_arg(value: str) -> bool:
    return all(ch.isalnum() or ch in "-_./:\\" for ch in value)


def is_frozen_app() -> bool:
    return IS_FROZEN


def default_output_dir(app_dir: Path = APP_DIR) -> str:
    return str(app_dir / "eh_downloads")


def normalize_output_dir(value: str, app_dir: Path = APP_DIR, frozen: bool = IS_FROZEN) -> str:
    text = value.strip()
    if not text:
        return default_output_dir(app_dir)
    path = Path(text)
    if frozen and path.name.lower() == "eh_downloads" and any(part.lower() == "_internal" for part in path.parts):
        return default_output_dir(app_dir)
    return text


def build_child_command() -> list[str]:
    if is_frozen_app():
        return [sys.executable, CHILD_FLAG]
    return [sys.executable, "-u", str(GUI_SCRIPT), CHILD_FLAG]


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == CHILD_FLAG:
        return eh_batch_downloader.main(args[1:])

    app = BatchDownloaderGui()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
