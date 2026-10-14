"""Run with IngeTrazo's own Python, from this folder:

    ..\\ingetrazo\\venv\\Scripts\\python -m pytest tests

IngeTrazo is looked for in $INGETRAZO_DIR, else beside this repository."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
INGETRAZO = Path(os.environ.get("INGETRAZO_DIR", ROOT.parent / "ingetrazo"))
sys.path.insert(0, str(INGETRAZO))
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

if QApplication.instance() is None:
    QApplication(sys.argv[:1])

# A settings store thrown away with the session, as IngeTrazo's own suite
# does: the main window test must not touch your real preferences.
_settings_dir = tempfile.mkdtemp(prefix="custom-icons-tests-settings-")
QSettings.setDefaultFormat(QSettings.IniFormat)
for _scope in (QSettings.UserScope, QSettings.SystemScope):
    QSettings.setPath(QSettings.IniFormat, _scope, _settings_dir)
