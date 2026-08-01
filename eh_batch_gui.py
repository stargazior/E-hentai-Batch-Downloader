#!/usr/bin/env python3
"""
Tkinter GUI wrapper for eh_batch_downloader.py.

The GUI deliberately launches the downloader in a child Python process. That
keeps the interface responsive and lets the Stop button terminate an active run.
"""

from __future__ import annotations

import os
import queue
import re
import subprocess
import sys
import threading
from datetime import datetime, timedelta
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
        self.geometry("1280x860")
        self.minsize(1060, 680)

        self.log_queue: "queue.Queue[str]" = queue.Queue()
        self.process: subprocess.Popen[str] | None = None
        self.reader_thread: threading.Thread | None = None
        self.progress_items: dict[str, str] = {}
        self.config_tasks: dict[str, list[str]] = {}
        self.config_document: dict[str, object] = {"version": 1, "configs": []}
        self.task_records: dict[str, dict[str, object]] = {}
        self.running_task_name: str | None = None
        self.running_dry_run = False
        self._loading_task = False
        self._suppress_task_selection = False
        self._programmatic_task_selection: str | None = None
        self.server_conditions: list[dict[str, str]] = []
        self.local_filters: list[dict[str, str]] = []

        self.source_type = tk.StringVar(value="Search")
        self.source_value = tk.StringVar()
        self.source_values_text: tk.Text | None = None
        self.config_file = tk.StringVar(value=default_config_file())
        self.config_name = tk.StringVar()
        self.site = tk.StringVar(value="E-Hentai")
        self.title_contains = tk.StringVar()
        self.title_regex = tk.StringVar()
        self.title_match = tk.StringVar(value="All (AND)")
        self.condition_type = tk.StringVar(value="Search")
        self.condition_value = tk.StringVar()
        self.filter_type = tk.StringVar(value="Title Contains")
        self.filter_value = tk.StringVar()
        self.task_enabled = tk.BooleanVar(value=True)
        self.interval_minutes = tk.StringVar(value="360")
        self.next_run = tk.StringVar(value="On demand")
        self.task_status = tk.StringVar(value="New")
        self.log_enabled = tk.BooleanVar(value=False)
        self.max_list_pages = tk.StringVar(value="1")
        self.max_galleries = tk.StringVar(value="0")
        self.max_image_pages = tk.StringVar(value="0")
        self.job_name = tk.StringVar(value="default")
        self.output_dir = tk.StringVar(value=default_output_dir())
        self.cookie_file = tk.StringVar()
        self.cookie_text = tk.StringVar()
        self.timeout = tk.StringVar(value="60")
        self.delay = tk.StringVar(value="0")
        self.retries = tk.StringVar(value="3")
        self.retry_backoff = tk.StringVar(value="2")
        self.gallery_workers = tk.StringVar(value="1")
        self.page_workers = tk.StringVar(value="3")
        self.archive_connections = tk.StringVar(value=str(eh_batch_downloader.DEFAULT_ARCHIVE_CONNECTIONS))
        self.hosts = tk.StringVar(value="System DNS")
        self.proxy_mode = tk.StringVar(value="System Proxy")
        self.proxy_url = tk.StringVar()
        self.download_mode = tk.StringVar(value="Archive Original")
        self.original = tk.BooleanVar(value=False)
        self.html_pages = tk.BooleanVar(value=False)
        self.overwrite = tk.BooleanVar(value=False)
        self.keep_going = tk.BooleanVar(value=True)
        self.category_vars = {value: tk.BooleanVar(value=True) for _label, value in CATEGORY_CHOICES}

        self._load_settings()
        self._build_ui()
        self._show_window()
        self._poll_log_queue()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _show_window(self) -> None:
        """Restore a usable on-screen window even after a display/layout change."""
        self.update_idletasks()
        screen_width = max(640, int(self.winfo_screenwidth()))
        screen_height = max(480, int(self.winfo_screenheight()))
        width = min(1280, screen_width - 40)
        height = min(860, screen_height - 80)
        x = max(20, (screen_width - width) // 2)
        y = max(20, (screen_height - height) // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")
        self.deiconify()
        self.wm_state("normal")
        self.lift()
        self.focus_force()
        self.attributes("-topmost", True)
        self.after(350, lambda: self.attributes("-topmost", False))

    def _build_ui(self) -> None:
        style = ttk.Style(self)
        for theme in ("vista", "clam", "default"):
            try:
                style.theme_use(theme)
                break
            except tk.TclError:
                continue
        style.configure("Treeview", rowheight=28)
        style.configure("Title.TLabel", font=("Segoe UI", 16, "bold"))
        style.configure("Muted.TLabel", foreground="#667085")
        style.configure("Card.TLabelframe", padding=10)
        style.configure("Card.TLabelframe.Label", font=("Segoe UI", 10, "bold"))

        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        header = ttk.Frame(self, padding=(16, 14, 16, 8))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(2, weight=1)
        ttk.Label(header, text="EH Batch Downloader", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(header, text=f"v{eh_batch_downloader.APP_VERSION}  |  task workspace", style="Muted.TLabel").grid(
            row=1, column=0, sticky="w", pady=(2, 0)
        )
        ttk.Label(header, text="Task file").grid(row=0, column=1, sticky="e", padx=(20, 8))
        task_file_entry = ttk.Entry(header, textvariable=self.config_file, state="readonly")
        task_file_entry.grid(row=0, column=2, sticky="ew")
        ttk.Button(header, text="Reload", command=lambda: self._reload_configs(show_errors=True)).grid(
            row=0, column=3, padx=(8, 0)
        )

        actions = ttk.Frame(self, padding=(16, 0, 16, 10))
        actions.grid(row=1, column=0, sticky="ew")
        self.new_task_button = ttk.Button(actions, text="New Task", command=self._new_task)
        self.new_task_button.grid(row=0, column=0, padx=(0, 8))
        self.save_task_button = ttk.Button(actions, text="Save Task", command=self._save_current_config)
        self.save_task_button.grid(row=0, column=1, padx=(0, 8))
        self.delete_task_button = ttk.Button(actions, text="Delete Task", command=self._delete_current_task)
        self.delete_task_button.grid(row=0, column=2, padx=(0, 8))
        self.run_task_button = ttk.Button(actions, text="Run Now", command=self._run_selected_task)
        self.run_task_button.grid(row=0, column=3, padx=(0, 8))
        self.preview_button = ttk.Button(actions, text="Preview", command=lambda: self._start_process(dry_run=True))
        self.preview_button.grid(row=0, column=4, padx=(0, 8))
        self.start_button = ttk.Button(actions, text="Start Download", command=lambda: self._start_process(dry_run=False))
        self.start_button.grid(row=0, column=5, padx=(0, 8))
        self.retry_button = ttk.Button(
            actions, text="Retry Failed", command=lambda: self._start_process(dry_run=False, retry_failed=True)
        )
        self.retry_button.grid(row=0, column=6, padx=(0, 8))
        self.stop_button = ttk.Button(actions, text="Stop", command=self._stop_process, state="disabled")
        self.stop_button.grid(row=0, column=7, padx=(0, 8))
        ttk.Button(actions, text="Self Test", command=self._run_self_test).grid(row=0, column=8, padx=(0, 8))

        panes = ttk.PanedWindow(self, orient="horizontal")
        panes.grid(row=2, column=0, sticky="nsew", padx=16, pady=(0, 16))
        self.main_panes = panes

        left_panel = ttk.Frame(panes, padding=(0, 0, 12, 0))
        left_panel.rowconfigure(0, weight=1)
        left_panel.columnconfigure(0, weight=1)
        left_notebook = ttk.Notebook(left_panel)
        left_notebook.grid(row=0, column=0, sticky="nsew")

        task_panel = ttk.Frame(left_notebook, padding=(0, 0, 8, 0))
        task_panel.rowconfigure(1, weight=1)
        task_panel.columnconfigure(0, weight=1)
        ttk.Label(task_panel, text="Tasks", style="Title.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 8))
        task_columns = ("name", "output", "next", "status")
        self.task_tree = ttk.Treeview(task_panel, columns=task_columns, show="headings", selectmode="browse")
        self.task_tree.tag_configure("scheduled", foreground="#475467")
        self.task_tree.tag_configure("running", foreground="#b54708")
        self.task_tree.tag_configure("completed", foreground="#067647")
        self.task_tree.tag_configure("failed", foreground="#b42318")
        for column, label, width in (
            ("name", "Task", 190),
            ("output", "Storage", 220),
            ("next", "Next run", 150),
            ("status", "Status", 100),
        ):
            self.task_tree.heading(column, text=label)
            self.task_tree.column(column, width=width, anchor="w", stretch=column in {"name", "output"})
        self.task_tree.grid(row=1, column=0, sticky="nsew")
        self.task_tree.bind("<<TreeviewSelect>>", self._on_task_selected)
        task_scroll = ttk.Scrollbar(task_panel, orient="vertical", command=self.task_tree.yview)
        task_scroll.grid(row=1, column=1, sticky="ns")
        self.task_tree.configure(yscrollcommand=task_scroll.set)
        task_hint = ttk.Label(
            task_panel,
            text="Each task has its own search rules, filters, storage and schedule.",
            style="Muted.TLabel",
            wraplength=320,
        )
        task_hint.grid(row=2, column=0, sticky="w", pady=(10, 0))

        transfers_tab = ttk.Frame(left_notebook, padding=8)
        log_tab = ttk.Frame(left_notebook, padding=8)
        left_notebook.add(task_panel, text="Tasks")
        left_notebook.add(transfers_tab, text="Transfers")
        left_notebook.add(log_tab, text="Log")

        detail = ttk.Frame(panes, padding=(12, 0, 0, 0))
        detail.rowconfigure(1, weight=1)
        detail.columnconfigure(0, weight=1)
        panes.add(left_panel, weight=1)
        panes.add(detail, weight=4)
        self.after_idle(self._set_initial_pane_position)

        detail_header = ttk.Frame(detail)
        detail_header.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        detail_header.columnconfigure(1, weight=1)
        ttk.Label(detail_header, text="Task name").grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Entry(detail_header, textvariable=self.config_name).grid(row=0, column=1, sticky="ew")
        ttk.Checkbutton(detail_header, text="Enabled", variable=self.task_enabled).grid(row=0, column=2, padx=(14, 8))
        ttk.Label(detail_header, text="Every").grid(row=0, column=3, padx=(8, 4))
        ttk.Entry(detail_header, textvariable=self.interval_minutes, width=8).grid(row=0, column=4)
        ttk.Label(detail_header, text="min").grid(row=0, column=5, padx=(4, 14))
        ttk.Label(detail_header, textvariable=self.next_run, style="Muted.TLabel").grid(row=0, column=6, sticky="e")
        ttk.Label(detail_header, textvariable=self.task_status).grid(row=1, column=1, sticky="w", pady=(5, 0))

        notebook = ttk.Notebook(detail)
        notebook.grid(row=1, column=0, sticky="nsew")
        search_tab = ttk.Frame(notebook, padding=12)
        download_tab = ttk.Frame(notebook, padding=12)
        network_tab = ttk.Frame(notebook, padding=12)
        login_tab = ttk.Frame(notebook, padding=12)
        for tab, label in (
            (search_tab, "EH Search + Local Filters"),
            (download_tab, "Download + Limits"),
            (network_tab, "Network"),
            (login_tab, "Login"),
        ):
            notebook.add(tab, text=label)

        search_tab.columnconfigure(0, weight=1)
        search_tab.rowconfigure(0, weight=1)
        search_tab.rowconfigure(1, weight=1)
        self._build_condition_editor(search_tab)
        self._build_filter_editor(search_tab)

        categories = ttk.LabelFrame(search_tab, text="Category filter", padding=8, style="Card.TLabelframe")
        categories.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        for index, (label, value) in enumerate(CATEGORY_CHOICES):
            ttk.Checkbutton(categories, text=label, variable=self.category_vars[value]).grid(
                row=index // 5, column=index % 5, sticky="w", padx=(0, 12), pady=2
            )
        ttk.Button(categories, text="All", command=lambda: self._set_all_categories(True)).grid(
            row=2, column=0, sticky="w", pady=(4, 0)
        )
        ttk.Button(categories, text="Doujinshi / Manga / Non-H", command=self._select_common_story_categories).grid(
            row=2, column=1, columnspan=2, sticky="w", pady=(4, 0)
        )

        download_tab.columnconfigure(1, weight=1)
        ttk.Label(download_tab, text="Output folder").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=5)
        ttk.Entry(download_tab, textvariable=self.output_dir).grid(row=0, column=1, sticky="ew", pady=5)
        ttk.Button(download_tab, text="Browse", command=self._choose_output).grid(row=0, column=2, padx=(8, 0))
        ttk.Label(download_tab, text="Site").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=5)
        ttk.Combobox(download_tab, textvariable=self.site, values=("E-Hentai", "ExHentai"), state="readonly", width=18).grid(
            row=1, column=1, sticky="w", pady=5
        )
        ttk.Label(download_tab, text="Mode").grid(row=2, column=0, sticky="w", padx=(0, 8), pady=5)
        ttk.Combobox(
            download_tab,
            textvariable=self.download_mode,
            values=("Archive Original", "Archive Resample", "Image Pages"),
            state="readonly",
            width=20,
        ).grid(row=2, column=1, sticky="w", pady=5)
        checks = ttk.Frame(download_tab)
        checks.grid(row=3, column=1, sticky="w", pady=5)
        ttk.Checkbutton(checks, text="Original", variable=self.original).grid(row=0, column=0, padx=(0, 12))
        ttk.Checkbutton(checks, text="HTML Pages", variable=self.html_pages).grid(row=0, column=1, padx=(0, 12))
        ttk.Checkbutton(checks, text="Overwrite", variable=self.overwrite).grid(row=0, column=2, padx=(0, 12))
        ttk.Checkbutton(checks, text="Keep going", variable=self.keep_going).grid(row=0, column=3)
        limits = ttk.LabelFrame(download_tab, text="Advanced limits", padding=10, style="Card.TLabelframe")
        limits.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(16, 0))
        for index in range(6):
            limits.columnconfigure(index, weight=1)
        for row, values in enumerate(
            (
                (("List pages", self.max_list_pages), ("Galleries", self.max_galleries), ("Image pages", self.max_image_pages)),
                (("Delay", self.delay), ("Timeout", self.timeout), ("Retries", self.retries)),
                (("Backoff", self.retry_backoff), ("Gallery workers", self.gallery_workers), ("Page workers", self.page_workers)),
            )
        ):
            for group, (label, variable) in enumerate(values):
                column = group * 2
                ttk.Label(limits, text=label).grid(row=row, column=column, sticky="w", padx=(0, 6), pady=4)
                ttk.Entry(limits, textvariable=variable, width=9).grid(row=row, column=column + 1, sticky="w", pady=4)
        ttk.Label(limits, text="Archive connections").grid(row=3, column=0, sticky="w", padx=(0, 6), pady=4)
        ttk.Entry(limits, textvariable=self.archive_connections, width=9).grid(row=3, column=1, sticky="w", pady=4)

        network_tab.columnconfigure(1, weight=1)
        ttk.Label(network_tab, text="Hosts").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=5)
        ttk.Combobox(network_tab, textvariable=self.hosts, values=("System DNS", "EhViewer Built-in Hosts"), state="readonly", width=24).grid(
            row=0, column=1, sticky="w", pady=5
        )
        ttk.Label(network_tab, text="Proxy").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=5)
        proxy_box = ttk.Combobox(network_tab, textvariable=self.proxy_mode, values=("System Proxy", "Direct", "HTTP Proxy"), state="readonly", width=18)
        proxy_box.grid(row=1, column=1, sticky="w", pady=5)
        proxy_box.bind("<<ComboboxSelected>>", lambda _event: self._update_proxy_hint())
        ttk.Label(network_tab, text="Proxy URL").grid(row=2, column=0, sticky="w", padx=(0, 8), pady=5)
        self.proxy_url_entry = ttk.Entry(network_tab, textvariable=self.proxy_url)
        self.proxy_url_entry.grid(row=2, column=1, sticky="ew", pady=5)

        login_tab.columnconfigure(1, weight=1)
        ttk.Label(login_tab, text="Cookie file").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=5)
        ttk.Entry(login_tab, textvariable=self.cookie_file).grid(row=0, column=1, sticky="ew", pady=5)
        ttk.Button(login_tab, text="Browse", command=self._choose_cookie_file).grid(row=0, column=2, padx=(8, 0))
        ttk.Label(login_tab, text="Cookie header").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=5)
        ttk.Entry(login_tab, textvariable=self.cookie_text, show="*").grid(row=1, column=1, sticky="ew", pady=5)
        ttk.Label(login_tab, text="Cookie values stay local and are not written to the task log.", style="Muted.TLabel").grid(
            row=2, column=1, sticky="w", pady=(8, 0)
        )

        self._build_transfers_view(transfers_tab)
        self._build_log_view(log_tab)
        self._update_proxy_hint()
        self._reload_configs(show_errors=False)
        self._schedule_tick()

    def _set_initial_pane_position(self) -> None:
        """Keep the task/transfer rail compact without preventing manual resizing."""
        try:
            total_width = self.main_panes.winfo_width()
            if total_width <= 1:
                return
            current = self.main_panes.sashpos(0)
            if current > 360:
                self.main_panes.sashpos(0, max(300, min(420, int(total_width * 0.30))))
        except tk.TclError:
            pass

    def _build_condition_editor(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="E-Hentai search conditions", padding=10, style="Card.TLabelframe")
        frame.grid(row=0, column=0, sticky="nsew", pady=(0, 8))
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)
        top = ttk.Frame(frame)
        top.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Label(top, text="These conditions are sent to E-Hentai").grid(row=0, column=0, sticky="w")
        ttk.Button(top, text="Search syntax", command=self._show_search_syntax_help).grid(row=0, column=1, sticky="e")
        self.condition_tree = ttk.Treeview(frame, columns=("type", "value"), show="headings", height=4, selectmode="browse")
        self.condition_tree.heading("type", text="Search field")
        self.condition_tree.heading("value", text="Value")
        self.condition_tree.column("type", width=150, anchor="w", stretch=False)
        self.condition_tree.column("value", width=680, anchor="w", stretch=True)
        self.condition_tree.grid(row=1, column=0, sticky="nsew")
        self.condition_tree.bind("<<TreeviewSelect>>", self._on_condition_selected)
        condition_xscroll = ttk.Scrollbar(frame, orient="horizontal", command=self.condition_tree.xview)
        condition_xscroll.grid(row=2, column=0, sticky="ew")
        self.condition_tree.configure(xscrollcommand=condition_xscroll.set)
        controls = ttk.Frame(frame)
        controls.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        condition_box = ttk.Combobox(
            controls, textvariable=self.condition_type, values=("Search", "Uploader", "Tag", "List URL"), state="readonly", width=14
        )
        condition_box.grid(row=0, column=0, padx=(0, 6))
        ttk.Entry(controls, textvariable=self.condition_value).grid(row=0, column=1, sticky="ew", padx=(0, 6))
        controls.columnconfigure(1, weight=1)
        ttk.Button(controls, text="Add", command=self._add_condition).grid(row=0, column=2, padx=(0, 4))
        ttk.Button(controls, text="Update", command=self._update_condition).grid(row=0, column=3, padx=(0, 4))
        ttk.Button(controls, text="Remove", command=self._remove_condition).grid(row=0, column=4)

    def _show_search_syntax_help(self) -> None:
        messagebox.showinfo(
            "E-Hentai search syntax",
            "Enter the site's search expression directly. Conditions of type Search and Tag are combined with spaces.\n\n"
            "Examples:\n"
            'language:chinese$ parody:"touhou project$"\n'
            "f:milf m:muscle\n"
            "pokemon -furry\n"
            "~yaoi ~furry\n"
            'title:"comic aun" -title:2007\n'
            "tag:rimjob$\n\n"
            "Use quotes for spaces, $ for an exact tag, - to exclude, and ~ for OR.\n"
            "Uploader can be entered as uploader:name or added as a separate Uploader condition.",
        )

    def _build_filter_editor(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="Local database filters", padding=10, style="Card.TLabelframe")
        frame.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)
        top = ttk.Frame(frame)
        top.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Label(top, text="Applied after gallery titles are collected").grid(row=0, column=0, sticky="w")
        ttk.Label(top, text="Match").grid(row=0, column=1, sticky="e", padx=(20, 6))
        ttk.Combobox(top, textvariable=self.title_match, values=("All (AND)", "Any (OR)"), state="readonly", width=12).grid(
            row=0, column=2, sticky="w"
        )
        self.filter_tree = ttk.Treeview(frame, columns=("type", "value"), show="headings", height=4, selectmode="browse")
        self.filter_tree.heading("type", text="Filter")
        self.filter_tree.heading("value", text="Value")
        self.filter_tree.column("type", width=150, anchor="w", stretch=False)
        self.filter_tree.column("value", width=520, anchor="w", stretch=True)
        self.filter_tree.grid(row=1, column=0, sticky="nsew")
        self.filter_tree.bind("<<TreeviewSelect>>", self._on_filter_selected)
        controls = ttk.Frame(frame)
        controls.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        ttk.Combobox(controls, textvariable=self.filter_type, values=("Title Contains", "Title Regex"), state="readonly", width=14).grid(
            row=0, column=0, padx=(0, 6)
        )
        ttk.Entry(controls, textvariable=self.filter_value).grid(row=0, column=1, sticky="ew", padx=(0, 6))
        controls.columnconfigure(1, weight=1)
        ttk.Button(controls, text="Add", command=self._add_filter).grid(row=0, column=2, padx=(0, 4))
        ttk.Button(controls, text="Update", command=self._update_filter).grid(row=0, column=3, padx=(0, 4))
        ttk.Button(controls, text="Remove", command=self._remove_filter).grid(row=0, column=4)

    def _build_transfers_view(self, parent: ttk.Frame) -> None:
        parent.rowconfigure(1, weight=1)
        parent.columnconfigure(0, weight=1)
        summary = ttk.Frame(parent)
        summary.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(summary, text="All matched galleries appear here during Preview and Download.", style="Muted.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Button(summary, text="Clear", command=self._clear_progress_table).grid(row=0, column=1, sticky="e")
        columns = ("gid", "status", "progress", "size", "connections", "title", "error")
        self.progress_tree = ttk.Treeview(parent, columns=columns, show="headings")
        headings = {
            "gid": ("GID", 82), "status": ("Status", 100), "progress": ("Progress", 130),
            "size": ("Size", 110), "connections": ("Conn", 56), "title": ("Title", 360), "error": ("Error", 280),
        }
        for column, (label, width) in headings.items():
            self.progress_tree.heading(column, text=label)
            self.progress_tree.column(column, width=width, anchor="w", stretch=column in {"title", "error"})
        self.progress_tree.grid(row=1, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(parent, orient="vertical", command=self.progress_tree.yview)
        scroll.grid(row=1, column=1, sticky="ns")
        self.progress_tree.configure(yscrollcommand=scroll.set)
        horizontal = ttk.Scrollbar(parent, orient="horizontal", command=self.progress_tree.xview)
        horizontal.grid(row=2, column=0, sticky="ew")
        self.progress_tree.configure(xscrollcommand=horizontal.set)
        for tag, background, foreground in (
            ("waiting", "#f2f4f7", "#475467"), ("running", "#fff4e5", "#9a3412"),
            ("done", "#ecfdf3", "#067647"), ("error", "#fef3f2", "#b42318"),
        ):
            self.progress_tree.tag_configure(tag, background=background, foreground=foreground)

    def _build_log_view(self, parent: ttk.Frame) -> None:
        parent.rowconfigure(1, weight=1)
        parent.columnconfigure(0, weight=1)
        controls = ttk.Frame(parent)
        controls.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Checkbutton(controls, text="Capture detailed log", variable=self.log_enabled).grid(row=0, column=0, sticky="w")
        ttk.Button(controls, text="Clear", command=self._clear_log).grid(row=0, column=1, sticky="e", padx=(8, 0))
        self.log_text = tk.Text(parent, wrap="word", height=12, state="disabled")
        self.log_text.grid(row=1, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(parent, orient="vertical", command=self.log_text.yview)
        scroll.grid(row=1, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scroll.set)

    def _update_source_hint(self) -> None:
        labels = {
            "Search": "Keywords (one per line)",
            "Uploader": "Uploaders (one per line)",
            "Tag": "Tags (one per line)",
            "List URL": "URLs (one per line)",
        }
        source_label = getattr(self, "source_label", None)
        if source_label is not None:
            source_label.configure(text=labels.get(self.source_type.get(), "Values (one per line)"))

    def _get_source_value(self) -> str:
        if self.server_conditions:
            return "\n".join(condition["value"] for condition in self.server_conditions)
        if self.source_values_text is not None:
            return self.source_values_text.get("1.0", "end-1c")
        return self.source_value.get()

    def _effective_server_conditions(self) -> list[dict[str, str]]:
        if self.server_conditions:
            return [dict(condition) for condition in self.server_conditions]
        kind = self.source_type.get() or "Search"
        return [{"type": kind, "value": value} for value in eh_batch_downloader.split_multi_values(self._get_source_value())]

    def _set_source_value(self, value: str) -> None:
        self.source_value.set(value)
        if hasattr(self, "condition_tree"):
            kind = self.source_type.get() or "Search"
            self.server_conditions = [{"type": kind, "value": item} for item in eh_batch_downloader.split_multi_values(value)]
            self._render_conditions()
        if self.source_values_text is not None:
            self.source_values_text.delete("1.0", "end")
            self.source_values_text.insert("1.0", value)

    def _render_conditions(self) -> None:
        tree = getattr(self, "condition_tree", None)
        if tree is None:
            return
        for item in tree.get_children():
            tree.delete(item)
        for condition in self.server_conditions:
            tree.insert("", "end", values=(condition["type"], condition["value"]))

    def _render_filters(self) -> None:
        tree = getattr(self, "filter_tree", None)
        if tree is None:
            return
        for item in tree.get_children():
            tree.delete(item)
        for condition in self.local_filters:
            tree.insert("", "end", values=(condition["type"], condition["value"]))

    def _selected_row_index(self, tree: ttk.Treeview) -> int | None:
        selected = tree.selection()
        if not selected:
            return None
        try:
            return list(tree.get_children("" )).index(selected[0])
        except ValueError:
            return None

    def _on_condition_selected(self, _event: object = None) -> None:
        index = self._selected_row_index(self.condition_tree)
        if index is not None and index < len(self.server_conditions):
            condition = self.server_conditions[index]
            self.condition_type.set(condition["type"])
            self.condition_value.set(condition["value"])

    def _add_condition(self) -> None:
        condition = {"type": self.condition_type.get(), "value": self.condition_value.get().strip()}
        if not condition["value"]:
            messagebox.showerror("Search condition", "Enter a search value first.")
            return
        self.server_conditions.append(condition)
        self._render_conditions()
        self.condition_value.set("")

    def _update_condition(self) -> None:
        index = self._selected_row_index(self.condition_tree)
        if index is None:
            messagebox.showinfo("Search condition", "Select a condition to update.")
            return
        condition = {"type": self.condition_type.get(), "value": self.condition_value.get().strip()}
        if not condition["value"]:
            messagebox.showerror("Search condition", "Enter a search value first.")
            return
        self.server_conditions[index] = condition
        self._render_conditions()

    def _remove_condition(self) -> None:
        index = self._selected_row_index(self.condition_tree)
        if index is None:
            return
        del self.server_conditions[index]
        self._render_conditions()
        self.condition_value.set("")

    def _on_filter_selected(self, _event: object = None) -> None:
        index = self._selected_row_index(self.filter_tree)
        if index is not None and index < len(self.local_filters):
            condition = self.local_filters[index]
            self.filter_type.set(condition["type"])
            self.filter_value.set(condition["value"])

    def _add_filter(self) -> None:
        value = self.filter_value.get().strip()
        if not value:
            messagebox.showerror("Local filter", "Enter a title value or regular expression first.")
            return
        if self.filter_type.get() == "Title Regex":
            try:
                import re
                re.compile(value)
            except re.error as exc:
                messagebox.showerror("Local filter", f"Invalid regular expression:\n{exc}")
                return
        self.local_filters.append({"type": self.filter_type.get(), "value": value})
        self._render_filters()
        self.filter_value.set("")

    def _update_filter(self) -> None:
        index = self._selected_row_index(self.filter_tree)
        value = self.filter_value.get().strip()
        if index is None:
            messagebox.showinfo("Local filter", "Select a filter to update.")
            return
        if not value:
            messagebox.showerror("Local filter", "Enter a title value or regular expression first.")
            return
        if self.filter_type.get() == "Title Regex":
            try:
                import re
                re.compile(value)
            except re.error as exc:
                messagebox.showerror("Local filter", f"Invalid regular expression:\n{exc}")
                return
        self.local_filters[index] = {"type": self.filter_type.get(), "value": value}
        self._render_filters()

    def _remove_filter(self) -> None:
        index = self._selected_row_index(self.filter_tree)
        if index is None:
            return
        del self.local_filters[index]
        self._render_filters()

    def _update_proxy_hint(self) -> None:
        uses_http_proxy = self.proxy_mode.get() == "HTTP Proxy"
        state = "normal" if uses_http_proxy else "disabled"
        self.proxy_url_entry.configure(state=state)

    def _choose_output(self) -> None:
        directory = filedialog.askdirectory(initialdir=self.output_dir.get() or str(APP_DIR))
        if directory:
            self.output_dir.set(directory)

    def _choose_config_file(self) -> None:
        filename = filedialog.askopenfilename(
            initialdir=str(Path(self.config_file.get() or default_config_file()).parent),
            title="Select config file",
            filetypes=(("JSON config files", "*.json"), ("All files", "*.*")),
        )
        if filename:
            self.config_file.set(filename)
            self._reload_configs(show_errors=True)

    def _choose_cookie_file(self) -> None:
        filename = filedialog.askopenfilename(
            title="Select cookie file",
            filetypes=(("Cookie or text files", "*.txt *.json *.cookies"), ("All files", "*.*")),
        )
        if filename:
            self.cookie_file.set(filename)

    def _reload_configs(self, show_errors: bool) -> None:
        path = Path(default_config_file())
        self.config_file.set(str(path))
        self.config_tasks = {}
        self.task_records = {}
        self.config_document = {"version": 1, "configs": []}
        if not path.exists():
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(self.config_document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            except OSError as exc:
                if show_errors:
                    messagebox.showerror("Config", f"Could not create task file:\n{path}\n{exc}")
            self._refresh_task_tree(preserve_selection=False)
            self._new_task()
            return
        try:
            raw_data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw_data, dict):
                self.config_document = raw_data
                if "configs" not in self.config_document and isinstance(self.config_document.get("tasks"), list):
                    self.config_document["configs"] = list(self.config_document["tasks"])
            raw_configs = raw_data.get("configs") or raw_data.get("tasks") if isinstance(raw_data, dict) else raw_data
            if not isinstance(raw_configs, list):
                raw_configs = []
            tasks = eh_batch_downloader.load_task_definitions(path)
        except ValueError as exc:
            if show_errors:
                messagebox.showerror("Config", str(exc))
            return
        for task in tasks:
            name = str(task["name"])
            self.config_tasks[name] = list(task["args"])  # type: ignore[arg-type]
            raw = next(
                (item for item in raw_configs if isinstance(item, dict) and str(item.get("name") or "").strip() == name),
                {"name": name},
            )
            self.task_records[name] = dict(raw)
            self.task_records[name]["_args"] = list(task["args"])
        # Reloading already has a single authoritative selection step below.
        # Preserving the old Treeview selection here would queue a second
        # <<TreeviewSelect>> event before _load_task_name() restores the task.
        self._refresh_task_tree(preserve_selection=False)
        names = list(self.config_tasks)
        if names:
            selected = self.config_name.get().strip()
            self._load_task_name(selected if selected in names else names[0])
        else:
            self._new_task()

    def _load_selected_config(self) -> None:
        name = self.config_name.get().strip()
        if name:
            self._load_task_name(name)

    def _refresh_task_tree(self, selected_name: str | None = None, *, preserve_selection: bool = True) -> None:
        tree = getattr(self, "task_tree", None)
        if tree is None:
            return
        if selected_name is None and preserve_selection:
            selected = tree.selection()
            selected_name = selected[0] if selected else None
        elif not preserve_selection:
            self._programmatic_task_selection = None
        for item in tree.get_children(""):
            tree.delete(item)
        for name, record in self.task_records.items():
            output = str(record.get("output") or "")
            if not output and record.get("_args"):
                try:
                    parsed = eh_batch_downloader.build_parser().parse_args(record["_args"])  # type: ignore[arg-type]
                    output = str(parsed.output)
                except SystemExit:
                    output = "?"
            enabled = bool(record.get("enabled", True))
            status = str(record.get("last_status") or ("Scheduled" if enabled else "Disabled"))
            next_run = self._format_next_run(str(record.get("next_run") or ""))
            status_key = "running" if status == "Running" else "failed" if "failed" in status.lower() else "completed" if "completed" in status.lower() else "scheduled"
            tree.insert("", "end", iid=name, values=(name, output, next_run, status), tags=(status_key,))
        if selected_name and tree.exists(selected_name):
            self._programmatic_task_selection = selected_name
            self._suppress_task_selection = True
            try:
                tree.selection_set(selected_name)
                tree.focus(selected_name)
            finally:
                self._suppress_task_selection = False

    def _on_task_selected(self, _event: object = None) -> None:
        selected = self.task_tree.selection()
        if selected and self._programmatic_task_selection is not None:
            expected = self._programmatic_task_selection
            self._programmatic_task_selection = None
            if selected[0] == expected:
                return
        if self._loading_task or self._suppress_task_selection:
            return
        if selected and selected[0] != self.config_name.get().strip():
            self._load_task_name(selected[0])

    def _load_task_name(self, name: str) -> None:
        if self._loading_task or name not in self.config_tasks:
            return
        self._loading_task = True
        try:
            parser = eh_batch_downloader.build_parser()
            try:
                args = parser.parse_args(self.config_tasks[name])
            except SystemExit:
                messagebox.showerror("Config", f"Config '{name}' contains invalid arguments.")
                return
            record = self.task_records.get(name, {})
            self._apply_cli_args_to_form(args, name, record.get("server_conditions"))
            self.config_name.set(name)
            self.task_enabled.set(bool(record.get("enabled", True)))
            self.interval_minutes.set(str(record.get("interval_minutes", 360)))
            if self.task_enabled.get() and not record.get("next_run"):
                try:
                    interval = max(1.0, float(self.interval_minutes.get()))
                except ValueError:
                    interval = 360.0
                record["next_run"] = (datetime.now().astimezone() + timedelta(minutes=interval)).isoformat(timespec="seconds")
            self.next_run.set(self._format_next_run(str(record.get("next_run") or "")))
            self.task_status.set(str(record.get("last_status") or ("Scheduled" if self.task_enabled.get() else "Disabled")))
            self._refresh_task_tree(selected_name=name)
        finally:
            self._loading_task = False

    def _new_task(self) -> None:
        self.config_name.set("new-task")
        self.job_name.set("new-task")
        self.server_conditions = []
        self.local_filters = []
        self.source_value.set("")
        self.title_contains.set("")
        self.title_regex.set("")
        self._render_conditions()
        self._render_filters()
        self.title_match.set("All (AND)")
        self.task_enabled.set(True)
        self.interval_minutes.set("360")
        self.next_run.set("On demand")
        self.task_status.set("New")
        self._set_all_categories(True)
        self._clear_progress_table()

    def _run_selected_task(self) -> None:
        name = self.config_name.get().strip()
        if not name:
            messagebox.showerror("Task", "Select or save a task first.")
            return
        if not self._effective_server_conditions():
            messagebox.showerror("Task", "Add at least one E-Hentai search condition before running.")
            return
        self._save_current_config()
        args = self.config_tasks.get(name)
        if args:
            self._start_process(dry_run=False, task_args=list(args), task_name=name)

    def _new_config(self) -> None:
        self._new_task()

    def _delete_current_task(self) -> None:
        name = self.config_name.get().strip()
        if not name or name not in self.task_records:
            return
        if not messagebox.askyesno("Delete task", f"Delete task '{name}' from the config file?"):
            return
        configs = self.config_document.get("configs") if isinstance(self.config_document, dict) else None
        if isinstance(configs, list):
            self.config_document["configs"] = [
                item for item in configs if not isinstance(item, dict) or str(item.get("name") or "") != name
            ]
        self._write_config_document()
        self._reload_configs(show_errors=False)
        if not self.config_tasks:
            self._new_task()

    def _save_current_config(self) -> None:
        name = self.config_name.get().strip()
        if not name:
            job_name = self.job_name.get().strip()
            name = "" if job_name == "default" else job_name
        if not name:
            messagebox.showerror("Config", "Type a Config name before saving.")
            return
        if not self._effective_server_conditions():
            messagebox.showerror("Task", "Add at least one E-Hentai search condition before saving.")
            return
        path = Path(default_config_file())
        try:
            data = dict(self.config_document) if isinstance(self.config_document, dict) else {"version": 1}
            configs = data.get("configs")
            if not isinstance(configs, list):
                configs = []
            if name not in self.task_records and self.task_enabled.get():
                try:
                    interval = max(1.0, float(self.interval_minutes.get()))
                except ValueError:
                    interval = 360.0
                self.next_run.set(self._format_next_run((datetime.now().astimezone() + timedelta(minutes=interval)).isoformat(timespec="seconds")))
            current = self._current_config_object(name)
            replaced = False
            for index, item in enumerate(configs):
                if isinstance(item, dict) and str(item.get("name") or "") == name:
                    configs[index] = current
                    replaced = True
                    break
            if not replaced:
                configs.append(current)
            data["configs"] = configs
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_suffix(path.suffix + ".part")
            temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            temp.replace(path)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            messagebox.showerror("Config", f"Could not save config:\n{exc}")
            return
        self.config_document = data
        self.config_file.set(str(path))
        self.config_name.set(name)
        self._reload_configs(show_errors=False)
        self._append_log(f"\nSaved task: {name}\n")

    def _apply_cli_args_to_form(self, args: object, config_name: str, raw_conditions: object = None) -> None:
        self.server_conditions = []
        if isinstance(raw_conditions, list) and raw_conditions:
            labels = {"search": "Search", "keywords": "Search", "uploader": "Uploader", "tag": "Tag", "url": "List URL", "list url": "List URL"}
            for condition in raw_conditions:
                if not isinstance(condition, dict):
                    continue
                condition_type = str(condition.get("type") or condition.get("source_type") or "").strip().lower()
                label = labels.get(condition_type)
                value = str(condition.get("value") or "").strip()
                if not label or not value:
                    continue
                self.server_conditions.append({"type": label, "value": value})
        else:
            for key, label in (("search", "Search"), ("uploader", "Uploader"), ("tag", "Tag"), ("url", "List URL")):
                self.server_conditions.extend(
                    {"type": label, "value": value} for value in eh_batch_downloader.source_values(args, key)
                )
        if self.server_conditions:
            self.source_type.set(self.server_conditions[0]["type"])
            self.source_value.set("\n".join(condition["value"] for condition in self.server_conditions))
            self.condition_type.set(self.server_conditions[0]["type"])
            self._render_conditions()
        else:
            self.source_value.set("")
            self.condition_type.set("Search")
            self._render_conditions()

        self.site.set("ExHentai" if getattr(args, "site", "e") == "ex" else "E-Hentai")
        self.local_filters = []
        self.local_filters.extend(
            {"type": "Title Contains", "value": value}
            for value in eh_batch_downloader.split_multi_values(getattr(args, "title_contains", None))
        )
        self.local_filters.extend(
            {"type": "Title Regex", "value": value}
            for value in eh_batch_downloader.split_multi_values(getattr(args, "title_regex", None))
        )
        self._render_filters()
        self.title_contains.set(";".join(condition["value"] for condition in self.local_filters if condition["type"] == "Title Contains"))
        self.title_regex.set(";".join(condition["value"] for condition in self.local_filters if condition["type"] == "Title Regex"))
        self.title_match.set("Any (OR)" if getattr(args, "title_match", "all") == "any" else "All (AND)")
        self.max_list_pages.set(str(getattr(args, "max_list_pages", 1)))
        self.max_galleries.set(str(getattr(args, "max_galleries", 0)))
        self.max_image_pages.set(str(getattr(args, "max_image_pages", 0)))
        self.job_name.set(str(getattr(args, "job_name", "") or config_name))
        self.output_dir.set(normalize_output_dir(str(getattr(args, "output", "") or default_output_dir())))
        self.cookie_file.set(str(getattr(args, "cookie_file", "") or ""))
        self.timeout.set(str(getattr(args, "timeout", 60)))
        self.delay.set(str(getattr(args, "delay", 0)))
        self.retries.set(str(getattr(args, "retries", 3)))
        self.retry_backoff.set(str(getattr(args, "retry_backoff", 2)))
        self.gallery_workers.set(str(getattr(args, "gallery_workers", 1)))
        self.page_workers.set(str(getattr(args, "page_workers", 3)))
        self.archive_connections.set(
            str(getattr(args, "archive_connections", eh_batch_downloader.DEFAULT_ARCHIVE_CONNECTIONS))
        )
        self.hosts.set("EhViewer Built-in Hosts" if getattr(args, "hosts", "system") == "builtin" else "System DNS")

        proxy_mode = getattr(args, "proxy_mode", "system")
        self.proxy_mode.set({"direct": "Direct", "http": "HTTP Proxy"}.get(proxy_mode, "System Proxy"))
        self.proxy_url.set(str(getattr(args, "proxy_url", "") or ""))
        mode = getattr(args, "download_mode", "archive-original")
        self.download_mode.set(
            {
                "archive-original": "Archive Original",
                "archive-resample": "Archive Resample",
                "pages": "Image Pages",
            }.get(mode, "Archive Original")
        )
        self.original.set(bool(getattr(args, "original", False)))
        self.html_pages.set(bool(getattr(args, "html_pages", False)))
        self.overwrite.set(bool(getattr(args, "overwrite", False)))
        self.keep_going.set(bool(getattr(args, "keep_going", True)))

        categories = eh_batch_downloader.split_category_values(getattr(args, "categories", None))
        valid = {value for _label, value in CATEGORY_CHOICES}
        if categories and set(categories).issubset(valid):
            for value, variable in self.category_vars.items():
                variable.set(value in categories)
        elif not categories:
            self._set_all_categories(True)

        self._update_source_hint()
        self._update_proxy_hint()

    def _current_config_object(self, name: str) -> dict[str, object]:
        download_mode = {
            "Archive Original": "archive-original",
            "Archive Resample": "archive-resample",
            "Image Pages": "pages",
        }.get(self.download_mode.get(), "archive-original")
        server_conditions = self._effective_server_conditions()
        source_values = [condition["value"] for condition in server_conditions]
        title_contains = [condition["value"] for condition in self.local_filters if condition["type"] == "Title Contains"]
        title_regex = [condition["value"] for condition in self.local_filters if condition["type"] == "Title Regex"]
        kinds = {condition["type"] for condition in server_conditions}
        compatibility_type = next(iter(kinds)) if len(kinds) == 1 else ""
        old_record = self.task_records.get(name, {})
        next_run_value = str(old_record.get("next_run") or "")
        if not next_run_value and self.task_enabled.get():
            try:
                interval = max(1.0, float(self.interval_minutes.get()))
            except ValueError:
                interval = 360.0
            next_run_value = (datetime.now().astimezone() + timedelta(minutes=interval)).isoformat(timespec="seconds")
        return {
            "name": name,
            "enabled": self.task_enabled.get(),
            "interval_minutes": self.interval_minutes.get(),
            "source_type": compatibility_type,
            "source_value": source_values if len(source_values) > 1 else (source_values[0] if source_values else ""),
            "server_conditions": server_conditions,
            "site": "ex" if self.site.get() == "ExHentai" else "e",
            "categories": self._selected_categories_for_settings(),
            "title_contains": title_contains if len(title_contains) > 1 else (title_contains[0] if title_contains else ""),
            "title_regex": title_regex if len(title_regex) > 1 else (title_regex[0] if title_regex else ""),
            "local_filters": [dict(condition) for condition in self.local_filters],
            "title_match": "any" if self.title_match.get() == "Any (OR)" else "all",
            "max_list_pages": self.max_list_pages.get(),
            "max_galleries": self.max_galleries.get(),
            "max_image_pages": self.max_image_pages.get(),
            "download_mode": download_mode,
            "archive_connections": self.archive_connections.get(),
            "gallery_workers": self.gallery_workers.get(),
            "page_workers": self.page_workers.get(),
            "timeout": self.timeout.get(),
            "delay": self.delay.get(),
            "retries": self.retries.get(),
            "retry_backoff": self.retry_backoff.get(),
            "output": normalize_output_dir(self.output_dir.get()),
            "job_name": self.job_name.get().strip() or name,
            "cookie_file": self.cookie_file.get().strip(),
            "hosts": "builtin" if self.hosts.get() == "EhViewer Built-in Hosts" else "system",
            "proxy_mode": {"Direct": "direct", "HTTP Proxy": "http"}.get(self.proxy_mode.get(), "system"),
            "proxy_url": self.proxy_url.get().strip(),
            "original": self.original.get(),
            "html_pages": self.html_pages.get(),
            "overwrite": self.overwrite.get(),
            "keep_going": self.keep_going.get(),
            "last_run": old_record.get("last_run", ""),
            "next_run": next_run_value,
            "last_status": old_record.get("last_status", ""),
            "last_code": old_record.get("last_code", ""),
        }

    def _run_self_test(self) -> None:
        self._start_process(dry_run=False, extra_args=["--self-test"])

    def _build_command_from_args(self, task_args: list[str], dry_run: bool) -> tuple[list[str], dict[str, str]]:
        if not is_frozen_app() and not GUI_SCRIPT.exists():
            raise ValueError(f"GUI script not found: {GUI_SCRIPT}")
        cmd = build_child_command()
        cmd.extend(task_args)
        if "--progress-json" not in cmd:
            cmd.append("--progress-json")
        if dry_run and "--dry-run" not in cmd:
            cmd.append("--dry-run")
        env = self._build_child_env()
        cookie_text = self.cookie_text.get().strip()
        if cookie_text:
            env["EH_COOKIES"] = cookie_text
        return cmd, env

    def _build_command(
        self,
        dry_run: bool,
        extra_args: list[str] | None = None,
        retry_failed: bool = False,
    ) -> tuple[list[str], dict[str, str]]:
        if not is_frozen_app() and not GUI_SCRIPT.exists():
            raise ValueError(f"GUI script not found: {GUI_SCRIPT}")

        cmd = build_child_command()
        env = self._build_child_env()
        if extra_args:
            cmd.extend(extra_args)
            return cmd, env

        if not retry_failed:
            server_conditions = self._effective_server_conditions()
            if not server_conditions:
                raise ValueError("Add at least one E-Hentai search condition.")
            self._append_server_conditions(cmd, server_conditions)
        else:
            cmd.append("--retry-failed")

        cmd.extend(["--site", "ex" if self.site.get() == "ExHentai" else "e"])
        job_name = self.job_name.get().strip()
        if job_name:
            cmd.extend(["--job-name", job_name])
        cmd.extend(["--max-list-pages", str(parse_positive_int(self.max_list_pages.get(), "List Pages", minimum=1))])
        cmd.extend(["--max-galleries", str(parse_positive_int(self.max_galleries.get(), "Galleries", minimum=0))])
        cmd.extend(["--max-image-pages", str(parse_positive_int(self.max_image_pages.get(), "Image Pages", minimum=0))])
        cmd.extend(["--timeout", str(parse_positive_float(self.timeout.get(), "Timeout", minimum=1.0))])
        cmd.extend(["--delay", str(parse_positive_float(self.delay.get(), "Delay", minimum=0.0))])
        cmd.extend(["--retries", str(parse_positive_int(self.retries.get(), "Retries", minimum=0))])
        cmd.extend(["--retry-backoff", str(parse_positive_float(self.retry_backoff.get(), "Backoff", minimum=0.0))])
        cmd.extend(["--gallery-workers", str(parse_positive_int(self.gallery_workers.get(), "Gallery Workers", minimum=1))])
        cmd.extend(["--page-workers", str(parse_positive_int(self.page_workers.get(), "Page Workers", minimum=1))])
        cmd.extend(
            [
                "--archive-connections",
                str(parse_positive_int(self.archive_connections.get(), "Archive Connections", minimum=1)),
            ]
        )
        download_mode = {
            "Archive Original": "archive-original",
            "Archive Resample": "archive-resample",
            "Image Pages": "pages",
        }.get(self.download_mode.get(), "archive-original")
        cmd.extend(["--download-mode", download_mode])
        cmd.append("--progress-json")
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

        local_filters = self.local_filters or (
            [{"type": "Title Contains", "value": item} for item in eh_batch_downloader.split_multi_values(self.title_contains.get())]
            + [{"type": "Title Regex", "value": item} for item in eh_batch_downloader.split_multi_values(self.title_regex.get())]
        )
        for condition in local_filters:
            cmd.extend(["--title-contains" if condition["type"] == "Title Contains" else "--title-regex", condition["value"]])
        cmd.extend(["--title-match", "any" if self.title_match.get() == "Any (OR)" else "all"])

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

    def _append_server_conditions(self, cmd: list[str], conditions: list[dict[str, str]]) -> None:
        combined_search_terms: list[str] = []
        source_flags = {"Uploader": "--uploader", "List URL": "--url"}
        for condition in conditions:
            kind = condition.get("type", "")
            value = condition.get("value", "").strip()
            if not value:
                continue
            if kind in {"Search", "Tag"}:
                # Tag rows are raw E-Hentai search expressions too. This keeps
                # namespaces, quotes, exact $, exclusions, and OR ~ intact.
                combined_search_terms.append(value)
                continue
            flag = source_flags.get(kind)
            if not flag:
                raise ValueError(f"Unknown search condition type: {kind}")
            cmd.extend([flag, value])
        if combined_search_terms:
            cmd.extend(["--search", " ".join(combined_search_terms)])

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
            "source_value": self._get_source_value(),
            "config_name": self.config_name.get(),
            "task_enabled": self.task_enabled.get(),
            "interval_minutes": self.interval_minutes.get(),
            "next_run": self.next_run.get(),
            "site": self.site.get(),
            "title_contains": self.title_contains.get(),
            "title_regex": self.title_regex.get(),
            "title_match": self.title_match.get(),
            "max_list_pages": self.max_list_pages.get(),
            "max_galleries": self.max_galleries.get(),
            "max_image_pages": self.max_image_pages.get(),
            "job_name": self.job_name.get(),
            "output_dir": self.output_dir.get(),
            "cookie_file": self.cookie_file.get(),
            "timeout": self.timeout.get(),
            "delay": self.delay.get(),
            "retries": self.retries.get(),
            "retry_backoff": self.retry_backoff.get(),
            "gallery_workers": self.gallery_workers.get(),
            "page_workers": self.page_workers.get(),
            "archive_connections": self.archive_connections.get(),
            "hosts": self.hosts.get(),
            "proxy_mode": self.proxy_mode.get(),
            "proxy_url": self.proxy_url.get(),
            "download_mode": self.download_mode.get(),
            "original": self.original.get(),
            "html_pages": self.html_pages.get(),
            "overwrite": self.overwrite.get(),
            "keep_going": self.keep_going.get(),
            "log_enabled": self.log_enabled.get(),
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
            "config_name": self.config_name,
            "interval_minutes": self.interval_minutes,
            "next_run": self.next_run,
            "site": self.site,
            "title_contains": self.title_contains,
            "title_regex": self.title_regex,
            "title_match": self.title_match,
            "max_list_pages": self.max_list_pages,
            "max_galleries": self.max_galleries,
            "max_image_pages": self.max_image_pages,
            "job_name": self.job_name,
            "output_dir": self.output_dir,
            "cookie_file": self.cookie_file,
            "timeout": self.timeout,
            "delay": self.delay,
            "retries": self.retries,
            "retry_backoff": self.retry_backoff,
            "gallery_workers": self.gallery_workers,
            "page_workers": self.page_workers,
            "archive_connections": self.archive_connections,
            "hosts": self.hosts,
            "proxy_mode": self.proxy_mode,
            "proxy_url": self.proxy_url,
            "download_mode": self.download_mode,
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
            "log_enabled": self.log_enabled,
            "task_enabled": self.task_enabled,
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
        if self.task_records:
            self._persist_task_records()
        if self.process and self.process.poll() is None:
            self.process.terminate()
        self.destroy()

    def _start_process(
        self,
        dry_run: bool,
        extra_args: list[str] | None = None,
        retry_failed: bool = False,
        task_args: list[str] | None = None,
        task_name: str | None = None,
    ) -> None:
        if self.process and self.process.poll() is None:
            messagebox.showinfo("Running", "A task is already running.")
            return
        try:
            if task_args is not None:
                cmd, env = self._build_command_from_args(task_args, dry_run=dry_run)
            else:
                cmd, env = self._build_command(dry_run=dry_run, extra_args=extra_args, retry_failed=retry_failed)
        except ValueError as exc:
            messagebox.showerror("Invalid Input", str(exc))
            return

        if not extra_args:
            self._save_settings()
            self._clear_progress_table()
        self.running_task_name = task_name or (self.config_name.get().strip() or None)
        self.running_dry_run = dry_run
        if self.running_task_name in self.task_records:
            self.task_status.set("Running")
            self.task_records[self.running_task_name]["last_status"] = "Running"
            self._refresh_task_tree()
        self._set_running(True)
        if self.log_enabled.get():
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
                creationflags=child_creation_flags(),
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
        self.log_queue.put(f"__PROCESS_DONE__:{return_code}")

    def _stop_process(self) -> None:
        if not self.process or self.process.poll() is not None:
            return
        self._append_log("\nStopping process...\n")
        self.process.terminate()

    def _set_running(self, running: bool) -> None:
        state = "disabled" if running else "normal"
        self.preview_button.configure(state=state)
        self.start_button.configure(state=state)
        self.retry_button.configure(state=state)
        self.stop_button.configure(state="normal" if running else "disabled")

    def _poll_log_queue(self) -> None:
        try:
            while True:
                item = self.log_queue.get_nowait()
                if item.startswith("__PROCESS_DONE__:"):
                    try:
                        code = int(item.split(":", 1)[1])
                    except ValueError:
                        code = 1
                    self._finish_task_run(code)
                elif item.startswith(eh_batch_downloader.PROGRESS_PREFIX):
                    self._update_progress_row(item)
                elif self.log_enabled.get():
                    self._append_log(item)
        except queue.Empty:
            pass
        self.after(100, self._poll_log_queue)

    def _finish_task_run(self, return_code: int) -> None:
        self._set_running(False)
        name = self.running_task_name
        self.running_task_name = None
        if not name or name not in self.task_records:
            return
        record = self.task_records[name]
        status = ("Preview complete" if return_code == 0 else "Preview failed") if self.running_dry_run else ("Completed" if return_code == 0 else "Failed")
        record["last_status"] = status
        record["last_code"] = return_code
        record["last_run"] = datetime.now().astimezone().isoformat(timespec="seconds")
        if self.running_dry_run:
            if self.config_name.get().strip() == name:
                self.next_run.set(self._format_next_run(str(record.get("next_run") or "")))
        elif bool(record.get("enabled", True)):
            try:
                interval = max(1.0, float(record.get("interval_minutes", 360)))
            except (TypeError, ValueError):
                interval = 360.0
            next_value = datetime.now().astimezone() + timedelta(minutes=interval)
            record["next_run"] = next_value.isoformat(timespec="seconds")
            if self.config_name.get().strip() == name:
                self.next_run.set(self._format_next_run(str(record["next_run"])))
        else:
            record["next_run"] = ""
            if self.config_name.get().strip() == name:
                self.next_run.set("Disabled")
        if self.config_name.get().strip() == name:
            self.task_status.set(status)
        self._persist_task_records()
        self._refresh_task_tree()

    def _schedule_tick(self) -> None:
        if not self.process or self.process.poll() is not None:
            now = datetime.now().astimezone()
            for name, record in self.task_records.items():
                if not bool(record.get("enabled", True)):
                    continue
                next_value = str(record.get("next_run") or "")
                try:
                    due = datetime.fromisoformat(next_value).astimezone() if next_value else now
                except ValueError:
                    due = now
                if due <= now and next_value:
                    args = record.get("_args")
                    if isinstance(args, list):
                        self._load_task_name(name)
                        self._start_process(dry_run=False, task_args=list(args), task_name=name)
                    break
        self.after(1000, self._schedule_tick)

    def _format_next_run(self, value: str) -> str:
        if not value:
            return "On demand"
        try:
            parsed = datetime.fromisoformat(value).astimezone()
        except ValueError:
            return value
        return parsed.strftime("%Y-%m-%d %H:%M")

    def _persist_task_records(self) -> None:
        configs = self.config_document.get("configs") if isinstance(self.config_document, dict) else None
        if not isinstance(configs, list):
            configs = []
            self.config_document["configs"] = configs
        for item in configs:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "")
            record = self.task_records.get(name)
            if record is None:
                continue
            for key in ("enabled", "interval_minutes", "last_run", "next_run", "last_status", "last_code"):
                item[key] = record.get(key, "")
        self._write_config_document()

    def _write_config_document(self) -> None:
        path = Path(default_config_file())
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_suffix(path.suffix + ".part")
            temp.write_text(json.dumps(self.config_document, ensure_ascii=False, indent=2), encoding="utf-8")
            temp.replace(path)
        except OSError as exc:
            if self.log_enabled.get():
                self._append_log(f"\nCould not save task state: {exc}\n")

    def _update_progress_row(self, line: str) -> None:
        raw = line[len(eh_batch_downloader.PROGRESS_PREFIX) :].strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return
        gid = str(data.get("gid") or "")
        if not gid:
            return
        key = gid
        title = str(data.get("title") or "")
        status = str(data.get("status") or "")
        percent = data.get("percent")
        done = data.get("done")
        total = data.get("total")
        bytes_done = data.get("bytes_done")
        bytes_total = data.get("bytes_total")
        progress = ""
        if isinstance(percent, (int, float)):
            progress = f"{percent:.1f}%"
        if isinstance(done, int) and isinstance(total, int) and total:
            progress = f"{progress} ({done}/{total})" if progress else f"{done}/{total}"
        if isinstance(bytes_done, int):
            byte_text = eh_batch_downloader.format_bytes(bytes_done)
            if isinstance(bytes_total, int):
                byte_text += "/" + eh_batch_downloader.format_bytes(bytes_total)
            progress = f"{progress} {byte_text}".strip()
        size = str(data.get("size") or "")
        if not size and isinstance(bytes_total, int):
            size = eh_batch_downloader.format_bytes(bytes_total)
        values = (
            gid,
            status,
            progress,
            size,
            str(data.get("connections") or ""),
            title,
            str(data.get("error") or ""),
        )
        item_id = self.progress_items.get(key)
        status_key = status.lower()
        row_tag = "error" if status_key in {"error", "failed"} else "done" if status_key in {"done", "skipped", "completed"} else "running" if status_key in {"downloading", "preparing", "requesting", "archiver", "checking"} else "waiting"
        if item_id and self.progress_tree.exists(item_id):
            self.progress_tree.item(item_id, values=values, tags=(row_tag,))
        else:
            self.progress_items[key] = self.progress_tree.insert("", "end", values=values, tags=(row_tag,))

    def _append_log(self, text: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _clear_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")
        self._clear_progress_table()

    def _clear_progress_table(self) -> None:
        tree = getattr(self, "progress_tree", None)
        if tree is None:
            return
        for item_id in tree.get_children():
            tree.delete(item_id)
        self.progress_items.clear()


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


def default_config_file(app_dir: Path = APP_DIR) -> str:
    return str(app_dir / "eh_batch_configs.json")


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


def child_creation_flags() -> int:
    if os.name != "nt":
        return 0
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))


def attach_child_stdio() -> None:
    for name, fd in (("stdout", 1), ("stderr", 2)):
        if getattr(sys, name, None) is not None:
            continue
        try:
            stream = os.fdopen(fd, "w", buffering=1, encoding="utf-8", errors="replace", closefd=False)
        except OSError:
            continue
        setattr(sys, name, stream)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == CHILD_FLAG:
        attach_child_stdio()
        return eh_batch_downloader.main(args[1:])
    if args:
        attach_child_stdio()
        return eh_batch_downloader.main(args)

    app = BatchDownloaderGui()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
