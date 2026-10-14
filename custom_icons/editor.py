# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 APBureauEtude
"""The icon chooser's page, served to the browser from 127.0.0.1.

A plain ``http.server`` on its own thread, reachable only from this
machine and only at an address holding a random token, so no web page can
find it or write to it. It serves the page, its state (the slots, the
themes, the files of the theme in use, its pairing), the SVG files and the
cursor previews. On POST it chooses the theme, saves the pairing, reads
the folder again or opens it; anything touching Qt is handed to the main
thread.
"""
from __future__ import annotations

import base64
import binascii
import json
import logging
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from PySide6.QtCore import QObject, Qt, Signal

log = logging.getLogger("ingetrazo.plugins.custom_icons")

PAGE = Path(__file__).with_name("editor.html")
_MAX_BODY = 1 << 20                 # a pairing, a list of icons
_MAX_UPLOAD = 48 << 20              # files from the computer, in base64
_MAX_FILES = 2000


class _Relay(QObject):
    """Runs a function on the main thread. Created there; the slot is a
    bound method of this QObject on a queued connection, so it runs on the
    main thread whichever thread emits."""

    run = Signal(object)

    def __init__(self):
        super().__init__()
        self.run.connect(self._call, Qt.QueuedConnection)

    def _call(self, fn) -> None:
        fn()


class Conflict(Exception):
    """The page holds another theme than the one in use."""


def _is_font(path: Path) -> bool:
    """An SVG font (a web page's glyphs), not an icon: kept out of the
    library, where it would show as a blank tile."""
    try:
        with path.open("rb") as f:
            return b"<font" in f.read(4096)
    except OSError:
        return False


class Editor:
    """One server per IngeTrazo session; ``url`` opens the page."""

    def __init__(self, plugin, slots, previews, texts, on_change):
        self.plugin = plugin          # the custom_icons package
        self.update(slots, previews, texts, on_change)
        self.token = secrets.token_urlsafe(18)
        self._relay = _Relay()
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._httpd.daemon_threads = True
        self._httpd.editor = self
        self.port = self._httpd.server_address[1]
        threading.Thread(target=self._httpd.serve_forever, daemon=True,
                         name="custom-icons-editor").start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/{self.token}/"

    def update(self, slots, previews, texts, on_change) -> None:
        """New data from the main thread (the page opened again). Each is
        replaced whole, so the server thread never sees half of it."""
        self.slots = slots
        self.previews = previews
        self.texts = texts
        self.on_change = on_change    # reload and redraw, on the main thread

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()

    # ---- What the page asks for (on the server thread) ----------------------

    def state(self) -> dict:
        plugin = self.plugin
        theme = plugin.active_theme()
        pool = plugin.theme_pool(theme)
        return {"texts": self.texts, "lang": plugin._lang(),
                "slots": self.slots,
                "themes": plugin.themes(), "theme": theme,
                "pool": sorted((p for p, f in pool.items()
                                if not _is_font(f)), key=str.lower),
                "mapping": plugin.load_mapping(theme, pool),
                "suggested": plugin.suggested_mapping(pool),
                "saved": plugin.has_mapping(theme),
                "saves": plugin.list_saves(theme)}

    def _pairs(self, data, pool) -> dict:
        """The pairs of ``data`` naming a slot of the page and a file of
        the theme."""
        slots = {(s["group"], s["key"]) for s in self.slots}
        mapping: dict = {"icons": {}, "cursors": {}}
        for group, pairs in mapping.items():
            given = data.get(group) or {}
            if not isinstance(given, dict):
                raise ValueError(f"{group}: an object was expected")
            for key, name in given.items():
                if (group, key) in slots and name in pool:
                    pairs[key] = name
        return mapping

    def _theme(self, data) -> str:
        """The theme in use, which ``data`` must name (the page may hold
        another one, chosen since in another tab)."""
        if not isinstance(data, dict):
            raise ValueError("a JSON object was expected")
        theme = self.plugin.active_theme()
        if theme is None or data.get("theme") != theme:
            raise Conflict(theme)
        return theme

    def choose_theme(self, data) -> dict:
        theme = data.get("theme") if isinstance(data, dict) else ""
        if theme is not None and theme not in self.plugin.themes():
            raise ValueError(f"no theme {theme!r}")
        self.plugin.save_theme(theme)
        self._relay.run.emit(self.on_change)
        return self.state()

    def save(self, data) -> dict:
        """Write the page's pairing in the theme's folder, and redraw."""
        theme = self._theme(data)
        mapping = self._pairs(data, self.plugin.theme_pool(theme))
        self.plugin.save_mapping(theme, mapping)
        self._relay.run.emit(self.on_change)
        return mapping

    def save_as(self, data) -> dict:
        """Keep the page's pairing as a save of the theme, by name."""
        theme = self._theme(data)
        mapping = self._pairs(data, self.plugin.theme_pool(theme))
        name = self.plugin.write_save(theme, data.get("name", ""), mapping)
        return {**self.state(), "name": name}

    def load(self, data) -> dict:
        """Make a save the theme's pairing, and redraw."""
        theme = self._theme(data)
        try:
            saved = self.plugin.read_save(theme, data.get("name", ""))
        except FileNotFoundError as exc:
            raise ValueError("no such save") from exc
        mapping = self._pairs(saved, self.plugin.theme_pool(theme))
        self.plugin.save_mapping(theme, mapping)
        self._relay.run.emit(self.on_change)
        return self.state()

    def delete_save(self, data) -> dict:
        theme = self._theme(data)
        self.plugin.delete_save(theme, data.get("name", ""))
        return self.state()

    def refresh(self) -> None:
        """The user added or changed files: IngeTrazo reads them again."""
        self._relay.run.emit(self.on_change)

    def open_folder(self) -> None:
        self._relay.run.emit(self.plugin.open_custom_folder)

    def import_icons(self, data) -> dict:
        """Copy icons of another theme into the one in use; their paths
        there, with the new state."""
        theme = self._theme(data)
        source, paths = data.get("from"), data.get("paths")
        if source not in self.plugin.themes() or \
                not isinstance(paths, list) or not paths or \
                len(paths) > _MAX_FILES:
            raise ValueError("a theme and a list of its icons were expected")
        new = self.plugin.import_from_theme(theme, source, paths)
        self._relay.run.emit(self.on_change)
        return {**self.state(), "paths": new}

    def upload(self, data) -> dict:
        """Add SVG files sent by the page (from the computer) to the theme
        in use; their paths there, with the new state."""
        theme = self._theme(data)
        files = data.get("files")
        if not isinstance(files, list) or not files or \
                len(files) > _MAX_FILES:
            raise ValueError("a list of files was expected")
        decoded = []
        for item in files:          # all checked before anything is written
            try:
                raw = base64.b64decode(item["data"], validate=True)
                name = str(item["name"])
            except (KeyError, TypeError, ValueError, binascii.Error) as exc:
                raise ValueError("a file could not be read") from exc
            if len(raw) > self.plugin.MAX_IMPORT or \
                    not self.plugin._is_svg(raw):
                raise ValueError(f"{name!r} is not an SVG file")
            decoded.append((name, raw))
        new = [self.plugin.import_file(theme, name, raw)
               for name, raw in decoded]
        self._relay.run.emit(self.on_change)
        return {**self.state(), "paths": new}

    def library(self, theme: str) -> dict | None:
        """Another theme's icons, for the page's library."""
        if theme not in self.plugin.themes():
            return None
        pool = self.plugin.theme_pool(theme)
        return {"theme": theme,
                "pool": sorted((p for p, f in pool.items()
                                if not _is_font(f)), key=str.lower)}

    def file(self, kind: str, name: str) -> Path | None:
        """``original/<key>.svg``, ``custom/<path in the theme in use>``
        or ``file/<theme>/<path in it>``; never anything outside those
        folders."""
        if kind == "original":
            folder = self.plugin.originals_dir()
        elif kind == "custom":
            theme = self.plugin.active_theme()
            if theme is None:
                return None
            folder = self.plugin.theme_dir(theme)
        else:
            theme, _, name = name.partition("/")
            if theme not in self.plugin.themes():
                return None
            folder = self.plugin.theme_dir(theme)
        parts = name.split("/")
        if not name.lower().endswith(".svg") or "\\" in name or \
                ":" in name or any(p in ("", ".", "..") for p in parts):
            return None
        path = folder.joinpath(*parts)
        try:
            if not path.resolve().is_relative_to(folder.resolve()) or \
                    not path.is_file():
                return None
        except OSError:
            return None
        return path


class _Handler(BaseHTTPRequestHandler):
    server_version = "CustomIcons/1"

    def log_message(self, format, *args):  # noqa: A002 — the base's name
        log.debug("editor: " + format, *args)

    def _route(self) -> str | None:
        """The path after the token; None when the request is not ours."""
        editor = self.server.editor
        # DNS rebinding: a page reaching 127.0.0.1 under its own name.
        if self.headers.get("Host") not in (f"127.0.0.1:{editor.port}",
                                            f"localhost:{editor.port}"):
            return None
        path = urlsplit(self.path).path
        prefix = f"/{editor.token}/"
        if not path.startswith(prefix):
            return None
        return unquote(path[len(prefix):])

    def do_GET(self):  # noqa: N802 — http.server's name
        editor = self.server.editor
        route = self._route()
        if route is None:
            return self._send(404, b"Not found", "text/plain")
        if route == "":
            return self._send(200, PAGE.read_bytes(),
                              "text/html; charset=utf-8")
        if route == "state":
            return self._json(200, editor.state())
        kind, _, name = route.partition("/")
        if kind == "preview" and name in editor.previews:
            return self._send(200, editor.previews[name], "image/png")
        if kind == "library":
            answer = editor.library(name)
            if answer is not None:
                return self._json(200, answer)
        if kind in ("original", "custom", "file"):
            path = editor.file(kind, name)
            if path is not None:
                return self._svg(path)
        self._send(404, b"Not found", "text/plain")

    def do_POST(self):  # noqa: N802
        editor = self.server.editor
        route = self._route()
        actions = {"mapping": editor.save, "theme": editor.choose_theme,
                   "save-as": editor.save_as, "load": editor.load,
                   "delete-save": editor.delete_save,
                   "import": editor.import_icons, "upload": editor.upload}
        if route in ("refresh", "open-folder"):
            if self.headers.get_content_type() != "application/json":
                return self._send(415, b"JSON only", "text/plain")
            (editor.refresh if route == "refresh" else editor.open_folder)()
            return self._json(200, {"ok": True})
        if route not in actions:
            return self._send(404, b"Not found", "text/plain")
        if self.headers.get_content_type() != "application/json":
            return self._send(415, b"JSON only", "text/plain")
        length = int(self.headers.get("Content-Length") or 0)
        if not 0 < length <= (_MAX_UPLOAD if route == "upload"
                              else _MAX_BODY):
            return self._send(413, b"Too large", "text/plain")
        try:
            answer = actions[route](json.loads(self.rfile.read(length)))
        except Conflict as exc:
            return self._json(409, {"error": "theme", "theme": exc.args[0]})
        except (ValueError, OSError) as exc:
            return self._json(400, {"error": str(exc)})
        self._json(200, answer)

    def _svg(self, path: Path):
        """An SVG file, revalidated by its date and size so the page sees
        a file the user changed, without downloading the others again."""
        stat = path.stat()
        tag = f'"{stat.st_mtime_ns:x}-{stat.st_size:x}"'
        if self.headers.get("If-None-Match") == tag:
            self.send_response(304)
            self.send_header("ETag", tag)
            self.end_headers()
            return None
        return self._send(200, path.read_bytes(), "image/svg+xml",
                          {"ETag": tag, "Cache-Control": "no-cache",
                           "Content-Security-Policy": "default-src 'none'; "
                           "style-src 'unsafe-inline'"})

    def _json(self, status: int, data):
        self._send(status, json.dumps(data, ensure_ascii=False).encode(),
                   "application/json; charset=utf-8")

    def _send(self, status: int, body: bytes, ctype: str, extra=None):
        self.send_response(status)
        headers = {"Content-Type": ctype, "Content-Length": str(len(body)),
                   "Cache-Control": "no-store",
                   "X-Content-Type-Options": "nosniff"}
        headers.update(extra or {})
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)
