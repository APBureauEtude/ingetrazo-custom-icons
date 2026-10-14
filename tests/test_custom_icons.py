from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

import pytest
from PySide6.QtCore import QByteArray, QCoreApplication, QPoint
from PySide6.QtGui import QColor
from PySide6.QtSvg import QSvgRenderer

import custom_icons
from views import icons

RED = (b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48">'
       b'<rect width="48" height="48" fill="#ff0000"/></svg>')
BLUE = RED.replace(b"#ff0000", b"#0000ff")
INK = RED.replace(b"#ff0000", b"currentColor")
THEME = "Mon thème"


def _centre(icon, size=48) -> QColor:
    return icon.pixmap(size, size).toImage().pixelColor(size // 2, size // 2)


def _cursor_centre(cursor) -> QColor:
    image = cursor.pixmap().toImage()
    return image.pixelColor(image.width() // 2, image.height() // 2)


def _write(folder, path, data):
    target = folder / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


@pytest.fixture
def theme(tmp_path, monkeypatch):
    """An icons folder holding one empty theme; returns the theme's
    folder."""
    from core import extensions
    monkeypatch.setattr(extensions, "user_plugins_dir",
                        lambda: tmp_path / "plugins")
    custom_icons._install()
    folder = custom_icons.custom_dir() / THEME
    folder.mkdir(parents=True)
    yield folder
    if custom_icons._editor is not None:
        custom_icons._editor.close()
        custom_icons._editor = None


def test_the_names_are_ingetrazos_keys():
    icon_keys, cursor_keys = custom_icons._keys()
    assert icon_keys == list(icons._DRAW)
    assert {"select", "line", "orbit", "tape", "tape+", "protractor+"} <= \
        set(cursor_keys)
    assert "save" not in cursor_keys            # no cursor of its own
    # The originals' names pair each icon with itself.
    originals = [f"{k}.svg" for k in icons._DRAW]
    assert custom_icons.suggested_mapping(originals) == {
        "icons": {k: f"{k}.svg" for k in icons._DRAW}, "cursors": {}}


def test_cursors_are_named_so_in_a_cursors_folder():
    paths = ["line.svg", "cursors/line.svg", "Set/Cursors/Tape+.svg",
             "cursors/save.svg", "orbit_cursor.svg", "Orbit/x.svg"]
    assert custom_icons.suggested_mapping(paths) == {
        "icons": {"line": "line.svg"},
        "cursors": {"line": "cursors/line.svg",
                    "tape+": "Set/Cursors/Tape+.svg"}}


def test_a_name_in_several_folders_comes_from_the_themes_set():
    """The folder holding most of the names wins over a shallower one, or
    one first in the alphabet, holding a few spares."""
    paths = ["orbit.svg", "Extra/zoom.svg", "Extra/Set/orbit.svg",
             "Set/orbit.svg", "Set/zoom.svg", "Set/line.svg", "Set/move.svg",
             "Extra/cursors/orbit.svg", "Set/cursors/orbit.svg",
             "Set/cursors/zoom.svg"]
    mapping = custom_icons.suggested_mapping(paths)
    assert mapping["icons"]["orbit"] == "Set/orbit.svg"
    assert mapping["icons"]["zoom"] == "Set/zoom.svg"
    assert mapping["cursors"]["orbit"] == "Set/cursors/orbit.svg"


def test_the_originals_are_written_by_key_and_left_alone_after(theme):
    custom_icons.reload()
    originals = custom_icons.originals_dir()
    assert sorted(p.stem for p in originals.glob("*.svg")) == \
        sorted(icons._DRAW)
    line = (originals / "line.svg").read_bytes()
    assert b'viewBox="0 0 48 48"' in line and b'width="48" height="48"' in line
    assert b'stroke="currentColor"' in line and b"#f37329" in line
    for path in originals.glob("*.svg"):
        assert QSvgRenderer(QByteArray(path.read_bytes())).isValid(), path
    before = {p.name: p.stat().st_mtime_ns for p in originals.glob("*.svg")}
    custom_icons.reload()
    assert before == {p.name: p.stat().st_mtime_ns
                      for p in originals.glob("*.svg")}
    assert list(theme.iterdir()) == []          # never writes the user's


def test_a_theme_is_a_folder_and_the_first_one_is_used(theme):
    other = custom_icons.custom_dir() / "Another"
    other.mkdir()
    (custom_icons.custom_dir() / ".hidden").mkdir()
    assert custom_icons.themes() == ["Another", THEME]
    assert custom_icons.active_theme() == "Another"     # no choice yet
    custom_icons.save_theme(THEME)
    assert custom_icons.active_theme() == THEME
    custom_icons.save_theme(None)                        # IngeTrazo's own
    assert custom_icons.active_theme() is None
    custom_icons.save_theme("Gone")                      # deleted since
    assert custom_icons.active_theme() == "Another"


def test_files_in_every_subfolder_pair_by_name(theme):
    _write(theme, "line.svg", RED)
    _write(theme, "Circle.svg", RED)            # names are matched case aside
    _write(theme, "rectangle.svg", RED)
    _write(theme, "Sheets/line.svg", BLUE)
    _write(theme, "Sheets/arr_left.svg", BLUE)
    _write(theme, "Deep/er/orbit.svg", RED)
    assert set(custom_icons.theme_pool(THEME)) == {
        "line.svg", "Circle.svg", "rectangle.svg", "Sheets/line.svg",
        "Sheets/arr_left.svg", "Deep/er/orbit.svg"}
    assert custom_icons.reload() == 5
    assert not custom_icons.has_mapping(THEME)
    assert custom_icons._mapping["icons"] == {
        "circle": "Circle.svg", "rectangle": "rectangle.svg",
        "line": "line.svg", "arr_left": "Sheets/arr_left.svg",
        "orbit": "Deep/er/orbit.svg"}
    assert _centre(icons.tool_icon("line")) == QColor("#ff0000")
    assert _centre(icons.tool_icon("orbit")) == QColor("#ff0000")
    # The others are still IngeTrazo's own drawings.
    assert icons.tool_icon("polygon").pixmap(48, 48).toImage() == \
        custom_icons._original_icon("polygon").pixmap(48, 48).toImage()


def test_each_theme_keeps_its_own_pairing(theme):
    _write(theme, "line.svg", RED)
    _write(theme, "icons/my_line.svg", BLUE)
    custom_icons.save_mapping(THEME, {"icons": {"line": "icons/my_line.svg"},
                                      "cursors": {}})
    assert (theme / custom_icons.MAPPING).is_file()     # travels with it
    assert custom_icons.reload() == 1
    assert _centre(icons.tool_icon("line")) == QColor("#0000ff")
    other = custom_icons.custom_dir() / "Other"
    _write(other, "line.svg", RED)
    custom_icons.save_theme("Other")
    custom_icons.reload()
    assert _centre(icons.tool_icon("line")) == QColor("#ff0000")
    custom_icons.save_theme(None)
    assert custom_icons.reload() == 0
    assert _centre(icons.tool_icon("line")) not in (QColor("#ff0000"),
                                                    QColor("#0000ff"))
    # A pair naming a file that is gone: IngeTrazo's icon, no error.
    custom_icons.save_theme(THEME)
    (theme / "icons" / "my_line.svg").unlink()
    assert custom_icons.reload() == 0


def test_current_color_is_the_interface_ink():
    icon = custom_icons.svg_icon(INK, ink="#123456")
    assert _centre(icon) == QColor("#123456")


def test_a_broken_svg_keeps_the_builtin_icon(theme):
    _write(theme, "line.svg", b"not an svg")
    custom_icons.reload()
    assert not icons.tool_icon("line").isNull()


# A pencil-like wedge, its tip at the bottom left (4, 44); an arrow, its
# point at the top left (12, 6); a pot with its drop at the bottom (30, 46).
WEDGE = (b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48">'
         b'<path d="M4 44 L10 22 L26 38 Z" fill="#cc0000"/></svg>')
ARROW = (b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48">'
         b'<path d="M12 6 L12 40 L22 30 L36 30 Z" fill="#000000"/></svg>')
POT = (b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48">'
       b'<rect x="14" y="4" width="30" height="30" fill="#444444"/>'
       b'<circle cx="30" cy="43" r="3" fill="#0000cc"/></svg>')


def _near(point, x, y, tol=1.5):
    return abs(point.x() - x) <= tol and abs(point.y() - y) <= tol


def test_cursors_click_where_their_drawing_points(theme):
    """Each theme draws its cursors, so the hotspot is found in the
    drawing, by the tool it serves: a pencil's tip, an arrow's point, the
    drop under a pot; elsewhere where IngeTrazo's own cursor clicks."""
    for path, data in (("cursors/line.svg", WEDGE),
                       ("New/pipette.svg", WEDGE), ("arrow.svg", ARROW),
                       ("cursors/paint.svg", POT), ("cursors/orbit.svg", RED),
                       ("anything.svg", BLUE)):
        _write(theme, path, data)
    custom_icons.save_mapping(THEME, {"icons": {}, "cursors": {
        "line": "cursors/line.svg", "eyedropper": "New/pipette.svg",
        "select": "arrow.svg", "offset": "arrow.svg",
        "paint": "cursors/paint.svg", "orbit": "cursors/orbit.svg",
        "move": "cursors/line.svg", "zoom": "anything.svg"}})
    custom_icons.reload()
    scale = 31 / 48
    assert _near(icons.tool_cursor("line").hotSpot(), 4 * scale, 44 * scale)
    assert _near(icons.tool_cursor("eyedropper").hotSpot(),
                 4 * scale, 44 * scale)
    assert _near(icons.tool_cursor("select").hotSpot(),
                 12 * scale, 6 * scale)
    assert _near(icons.tool_cursor("offset").hotSpot(),
                 12 * scale, 6 * scale)
    assert _near(icons.tool_cursor("paint").hotSpot(), 30 * scale,
                 46 * scale, tol=2)
    orbit = icons.tool_cursor("orbit")
    assert _cursor_centre(orbit) == QColor("#ff0000")
    assert orbit.hotSpot() == QPoint(16, 16)    # the orbit's centre
    # The Line tool's pencil serving Move clicks at Move's centre.
    assert icons.tool_cursor("move").hotSpot() == QPoint(16, 16)
    assert icons.tool_cursor("zoom").hotSpot() == \
        custom_icons._original_cursor("zoom").hotSpot() != QPoint(16, 16)
    # Not paired, IngeTrazo's own cursor; Select keeps the standard arrow
    # when nothing is paired with it.
    assert icons.tool_cursor("pan").pixmap().toImage() ==         custom_icons._original_cursor("pan").pixmap().toImage()


def test_guide_mode_takes_its_own_slot_or_draws_a_plus(theme):
    _write(theme, "cursors/tape.svg", RED)
    _write(theme, "cursors/tape+.svg", BLUE)
    custom_icons.save_mapping(THEME, {"icons": {}, "cursors": {
        "tape": "cursors/tape.svg"}})
    custom_icons.reload()
    plain = icons.tool_cursor("tape").pixmap().toImage()
    plus = icons.tool_cursor("tape", True).pixmap().toImage()
    assert plain.pixelColor(27, 4) == QColor("#ff0000")
    assert plus.pixelColor(27, 4) == QColor("#000000")   # the «+»
    custom_icons.save_mapping(THEME, {"icons": {}, "cursors": {
        "tape": "cursors/tape.svg", "tape+": "cursors/tape+.svg"}})
    custom_icons.reload()
    assert _cursor_centre(icons.tool_cursor("tape", True)) == \
        QColor("#0000ff")
    assert _cursor_centre(icons.tool_cursor("tape")) == QColor("#ff0000")


# ---- The page's server --------------------------------------------------------

class _Window:
    """What open_editor needs of the main window: nothing it must have."""


def _get(url, host=None):
    req = urllib.request.Request(url)
    if host:
        req.add_header("Host", host)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.headers.get_content_type(), r.read()
    except urllib.error.HTTPError as exc:
        return exc.code, None, b""


def _post(url, data=None, ctype="application/json"):
    body = data if isinstance(data, bytes) else json.dumps(data or {}).encode()
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": ctype})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as exc:
        return exc.code, None


def _wait(condition, seconds=3.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        QCoreApplication.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return False


def test_the_page_lists_icons_cursors_themes_and_the_themes_files(theme):
    _write(theme, "line.svg", RED)
    _write(theme, "Extra/spare.svg", BLUE)
    _write(theme, "fonts/glyphs.svg", b'<svg xmlns="http://www.w3.org/2000/'
           b'svg"><defs><font id="g"><glyph unicode="a"/></font></defs></svg>')
    (custom_icons.custom_dir() / "Zeta").mkdir()
    custom_icons.reload()
    url = custom_icons.open_editor(_Window(), browse=False)
    assert url.startswith("http://127.0.0.1:")
    status, ctype, body = _get(url)
    assert (status, ctype) == (200, "text/html") and b"<title>" in body
    state = json.loads(_get(url + "state")[2])
    assert set(state["texts"]) == {"fr", "en", "es"}
    assert state["texts"]["fr"]["Library"] == "Bibliothèque"
    line = next(s for s in state["slots"] if s["id"] == "icons:line")
    assert line["labels"]["en"] == "line" or line["labels"]["fr"] == "Ligne"
    tape = next(s for s in state["slots"] if s["id"] == "cursors:tape+")
    assert tape["labels"]["fr"].endswith("(mode guide)")
    icon_slots = [s["key"] for s in state["slots"] if s["group"] == "icons"]
    cursor_slots = [s["key"] for s in state["slots"]
                    if s["group"] == "cursors"]
    assert sorted(icon_slots) == sorted(icons._DRAW)
    assert {"select", "line", "orbit", "tape", "tape+"} <= set(cursor_slots)
    assert state["themes"] == [THEME, "Zeta"] and state["theme"] == THEME
    assert state["pool"] == ["Extra/spare.svg", "line.svg"]
    assert state["mapping"]["icons"] == {"line": "line.svg"}
    assert state["saved"] is False and state["saves"] == []
    assert _get(url + "original/line.svg")[:2] == (200, "image/svg+xml")
    assert _get(url + "custom/Extra/spare.svg")[:2] == (200,
                                                         "image/svg+xml")
    assert _get(url + "preview/orbit.png")[:2] == (200, "image/png")


def test_the_page_answers_nobody_else(theme):
    custom_icons.reload()
    url = custom_icons.open_editor(_Window(), browse=False)
    base = url.rsplit("/", 2)[0] + "/"
    assert _get(base + "state")[0] == 404                 # no token
    assert _get(url + "state", host="evil.example")[0] == 404
    _write(custom_icons.custom_dir(), "Other/secret.svg", RED)
    _write(custom_icons.root_folder(), "secret.svg", RED)
    for path in ("custom/..%2FOther%2Fsecret.svg", "custom/../secret.svg",
                 "custom/..%5C..%5Csecret.svg", "custom/C:secret.svg",
                 "custom//secret.svg", "original/../settings.json"):
        assert _get(url + path)[0] == 404, path
    assert _post(url + "mapping", b"{}", ctype="text/plain")[0] == 415
    assert _post(url + "theme", {"theme": "../.."})[0] == 400


def test_a_change_on_the_page_is_saved_in_the_theme_and_redrawn(theme):
    _write(theme, "line.svg", RED)
    _write(theme, "Mine/my_line.svg", BLUE)
    custom_icons.reload()
    url = custom_icons.open_editor(_Window(), browse=False)
    assert _centre(icons.tool_icon("line")) == QColor("#ff0000")
    status, answer = _post(url + "mapping", {
        "theme": THEME,
        "icons": {"line": "Mine/my_line.svg",
                  "no_such_tool": "line.svg",
                  "rectangle": "no_such_file.svg"},
        "cursors": {"tape+": "line.svg", "orbit": "line.svg"}})
    assert status == 200
    saved = json.loads((theme / custom_icons.MAPPING).read_text(
        encoding="utf-8"))
    assert saved == {"icons": {"line": "Mine/my_line.svg"},
                     "cursors": {"tape+": "line.svg",
                                 "orbit": "line.svg"}} == answer
    # Redrawn on the main thread, through the relay.
    assert _wait(lambda: _centre(icons.tool_icon("line")) ==
                 QColor("#0000ff"))
    # A page still showing another theme cannot write over this one.
    assert _post(url + "mapping", {"theme": "Other", "icons": {},
                                   "cursors": {}})[0] == 409


def test_saves_keep_a_pairing_under_a_name_and_load_it_again(theme):
    _write(theme, "line.svg", RED)
    _write(theme, "mine.svg", BLUE)
    custom_icons.reload()
    url = custom_icons.open_editor(_Window(), browse=False)
    status, state = _post(url + "save-as", {
        "theme": THEME, "name": "Bleu: ../essai", "icons": {"line": "mine.svg"},
        "cursors": {}})
    assert status == 200
    name = state["name"]
    assert name == "Bleu_ .._essai"             # a safe file name
    assert [s["name"] for s in state["saves"]] == [name]
    assert (theme / custom_icons.SAVES / f"{name}.json").is_file()
    assert not custom_icons.has_mapping(THEME)  # saving is not applying
    status, state = _post(url + "load", {"theme": THEME, "name": name})
    assert status == 200 and state["mapping"]["icons"] == {"line": "mine.svg"}
    assert _wait(lambda: _centre(icons.tool_icon("line")) ==
                 QColor("#0000ff"))
    assert _post(url + "load", {"theme": THEME, "name": "../mapping"})[0]         == 400
    assert _post(url + "load", {"theme": THEME, "name": "nope"})[0] == 400
    status, state = _post(url + "delete-save", {"theme": THEME,
                                                "name": name})
    assert status == 200 and state["saves"] == []
    assert custom_icons.save_file_name("CON") == "_CON"
    assert custom_icons.save_file_name(" . ") == ""


def test_icons_of_another_theme_are_copied_in_once(theme):
    other = custom_icons.custom_dir() / "Other"
    _write(other, "Set/arr_left.svg", BLUE)
    _write(other, "line.svg", RED)
    first = custom_icons.import_from_theme(THEME, "Other",
                                           ["Set/arr_left.svg"])
    assert first == ["Importées/Other/Set/arr_left.svg"]
    assert (theme / first[0]).read_bytes() == BLUE
    # The same icon again: the same copy, no duplicate.
    assert custom_icons.import_from_theme(
        THEME, "Other", ["Set/arr_left.svg"]) == first
    # Changed since, in the other theme: a copy beside, the first one kept.
    _write(other, "Set/arr_left.svg", RED)
    assert custom_icons.import_from_theme(
        THEME, "Other", ["Set/arr_left.svg"]) == [
        "Importées/Other/Set/arr_left (2).svg"]
    assert (theme / first[0]).read_bytes() == BLUE
    with pytest.raises(ValueError):
        custom_icons.import_from_theme(THEME, "Other", ["../secret.svg"])
    assert custom_icons.import_from_theme(THEME, THEME, ["x.svg"]) ==         ["x.svg"]                               # its own: nothing to copy


def test_the_page_shows_another_theme_and_imports_from_it(theme):
    other = custom_icons.custom_dir() / "Other"
    _write(other, "Set/arr_left.svg", BLUE)
    _write(other, "Sub/b.svg", BLUE)
    _write(theme, "line.svg", RED)
    custom_icons.reload()
    url = custom_icons.open_editor(_Window(), browse=False)
    lib = json.loads(_get(url + "library/Other")[2])
    assert lib == {"theme": "Other",
                   "pool": ["Set/arr_left.svg", "Sub/b.svg"]}
    assert _get(url + "library/Nope")[0] == 404
    assert _get(url + "file/Other/Set/arr_left.svg")[:2] == (
        200, "image/svg+xml")
    for path in ("file/Other/../Mon%20th%C3%A8me/line.svg",
                 "file/Other/..%2F..%2Fsettings.json", "file/../x.svg",
                 "file/Nope/a.svg"):
        assert _get(url + path)[0] == 404, path
    # Used on a tool: copied in first, then paired like any other icon.
    status, answer = _post(url + "import", {
        "theme": THEME, "from": "Other", "paths": ["Set/arr_left.svg",
                                                   "Sub/b.svg"]})
    assert status == 200
    assert answer["paths"] == ["Importées/Other/Set/arr_left.svg",
                               "Importées/Other/Sub/b.svg"]
    assert set(answer["paths"]) <= set(answer["pool"])
    assert _post(url + "mapping", {"theme": THEME, "icons": {
        "line": answer["paths"][0]}, "cursors": {}})[0] == 200
    assert _wait(lambda: _centre(icons.tool_icon("line")) ==
                 QColor("#0000ff"))
    assert _post(url + "import", {"theme": THEME, "from": "Other",
                                  "paths": ["../x.svg"]})[0] == 400
    assert _post(url + "import", {"theme": THEME, "from": "Nope",
                                  "paths": ["a.svg"]})[0] == 400


def test_svg_files_from_the_computer_go_into_the_theme(theme):
    import base64
    custom_icons.reload()
    url = custom_icons.open_editor(_Window(), browse=False)
    b64 = base64.b64encode(BLUE).decode()
    status, answer = _post(url + "upload", {"theme": THEME, "files": [
        {"name": "mon icône.svg", "data": b64},
        {"name": "../../evil.svg", "data": b64}]})
    assert status == 200
    assert answer["paths"] == ["Importées/mon icône.svg",
                               "Importées/evil.svg"]     # no way out
    assert (theme / "Importées" / "mon icône.svg").read_bytes() == BLUE
    assert set(answer["paths"]) <= set(answer["pool"])
    # Not an SVG, too big, not base64: refused, and nothing written.
    png = base64.b64encode(bytes([0x89]) + b"PNG....").decode()
    big = base64.b64encode(b"<svg>" + b" " * (3 << 20)).decode()
    for bad in ({"name": "a.svg", "data": png}, {"name": "b.svg", "data": big},
                {"name": "c.svg", "data": "%%%"}):
        assert _post(url + "upload", {"theme": THEME, "files": [
            {"name": "ok.svg", "data": b64}, bad]})[0] == 400
    assert not (theme / "Importées" / "ok.svg").exists()


def test_the_page_chooses_the_theme_refreshes_and_opens_the_folder(
        theme, monkeypatch):
    _write(theme, "line.svg", RED)
    _write(custom_icons.custom_dir(), "Other/line.svg", BLUE)
    custom_icons.reload()
    url = custom_icons.open_editor(_Window(), browse=False)
    status, state = _post(url + "theme", {"theme": "Other"})
    assert status == 200 and state["theme"] == "Other"
    assert json.loads((custom_icons.root_folder() /
                       custom_icons.SETTINGS).read_text()) == {
        "theme": "Other"}
    assert _wait(lambda: _centre(icons.tool_icon("line")) ==
                 QColor("#0000ff"))
    assert _post(url + "theme", {"theme": None})[1]["theme"] is None
    assert _wait(lambda: _centre(icons.tool_icon("line")) not in (
        QColor("#0000ff"), QColor("#ff0000")))
    # Refresh: a file changed on disk is read again.
    custom_icons.save_theme(THEME)
    _post(url + "refresh")
    assert _wait(lambda: _centre(icons.tool_icon("line")) ==
                 QColor("#ff0000"))
    _write(theme, "line.svg", BLUE)
    _post(url + "refresh")
    assert _wait(lambda: _centre(icons.tool_icon("line")) ==
                 QColor("#0000ff"))
    # The folder opens on the main thread («Icônes custom»).
    opened = []
    monkeypatch.setattr(custom_icons, "open_custom_folder",
                        lambda: opened.append(custom_icons.custom_dir()))
    assert _post(url + "open-folder")[0] == 200
    assert _wait(lambda: opened == [custom_icons.custom_dir()])


def test_the_main_window_shows_the_custom_icon_and_cursor(theme, tmp_path,
                                                          monkeypatch):
    """The real thing: the toolbars are built BEFORE setup(app) runs, and
    the main window imported tool_icon by name — both must be covered —
    and the 3D view asks for its pointer through tool_cursor."""
    import shutil
    from pathlib import Path
    from core import extensions
    from views.main_window import MainWindow
    plugins = tmp_path / "plugins"
    shutil.copytree(Path(custom_icons.__file__).parent,
                    plugins / "custom_icons",
                    ignore=shutil.ignore_patterns("__pycache__"))
    monkeypatch.setattr(extensions, "plugin_dirs", lambda: [plugins])
    _write(theme, "line.svg", RED)
    _write(theme, "Sub/cursors/line.svg", BLUE)
    win = MainWindow()
    try:
        assert _centre(win._tool_actions["line"].icon()) == QColor("#ff0000")
        assert _centre(win._tool_actions["select"].icon()) != \
            QColor("#ff0000")
        assert (custom_icons.originals_dir() / "rectangle.svg").is_file()
        win._tool_actions["line"].trigger()
        assert _cursor_centre(win.viewport.cursor()) == QColor("#0000ff")
        menu = next(m for m in win.menuBar().actions()
                    if m.menu() is not None and "xtensi" in m.text()).menu()
        sub = next(a.menu() for a in menu.actions()
                   if a.menu() is not None and "icon" in a.text().lower())
        texts = [a.text() for a in sub.actions()]
        assert any("hoose" in t for t in texts)        # Choose icons…
        # A pairing saved elsewhere, then Reload icons: no restart.
        _write(theme, "mine.svg", RED)
        custom_icons.save_mapping(THEME, {"icons": {"rectangle": "mine.svg"},
                                          "cursors": {}})
        next(a for a in sub.actions() if "eload" in a.text()).trigger()
        assert _centre(win._tool_actions["rectangle"].icon()) == \
            QColor("#ff0000")
        assert _centre(win._tool_actions["line"].icon()) != QColor("#ff0000")
    finally:
        win._saved_version = win.viewport.scene.version
        win.close()
