# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 APBureauEtude
"""Custom Icons — replace IngeTrazo's icons and cursors with SVG files.

IngeTrazo draws its icons in code: there are no image files to swap. This
extension keeps everything in one folder — ``%APPDATA%\\ingetrazo\\
custom_icons`` on Windows, ``~/.local/share/ingetrazo/custom_icons`` on
Linux:

- ``Icônes originales/`` — every built-in icon as an SVG file, named after
  its key (``line.svg``…), written again at each start: a reference and a
  template, never read back;
- ``Icônes custom/<theme>/`` — one folder per icon theme, its SVG files
  in any subfolders, and its ``mapping.json``: which of them replaces which
  icon or cursor. A theme travels as its folder;
- ``settings.json`` — the theme in use (by default the first one there).

Extensions ▸ Custom icons ▸ Choose icons… opens a page in the browser: the
theme to use, IngeTrazo's icons on the left, the theme's on the right,
paired by dragging; each change is saved and shown in IngeTrazo at once.
Until a theme's first change, its pairing follows IngeTrazo's own keys:
``line.svg`` replaces the Line icon, as in «Icônes originales», and
``cursors/line.svg`` the Line tool's cursor; a theme made from copies of
the originals is paired on its own.

``currentColor`` in an SVG is painted in the interface's ink, so the icon
follows the light and dark themes; the originals use it for their lines.

Install: copy this folder into your plugins folder (Extensions ▸ Open
plugins folder) and restart IngeTrazo.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
from pathlib import Path

from PySide6.QtCore import (
    QBuffer, QByteArray, QIODevice, QPointF, QRect, QRectF, QSize, Qt, QUrl)
from PySide6.QtGui import (
    QColor, QCursor, QDesktopServices, QIcon, QImage, QImageReader, QPainter,
    QPen, QPixmap)
from PySide6.QtWidgets import QApplication

try:  # Not every packaged IngeTrazo ships the QtSvg module.
    from PySide6.QtSvg import QSvgGenerator, QSvgRenderer
except ImportError:  # pragma: no cover — depends on the build
    QSvgGenerator = QSvgRenderer = None

log = logging.getLogger("ingetrazo.plugins.custom_icons")

__version__ = "1.1"                 # see CHANGELOG.md

FOLDER_NAME = "custom_icons"
ORIGINALS = "Icônes originales"
CUSTOM = "Icônes custom"
SETTINGS = "settings.json"          # beside the two folders: the theme in use
MAPPING = "mapping.json"            # in a theme's folder: its pairing
SAVES = "Sauvegardes"               # in a theme's folder: its saved pairings
IMPORTS = "Importées"               # in a theme's folder: icons brought in
CANVAS = 48
CURSOR_SIZE = 32            # logical pixels, as IngeTrazo's own cursors
# Each icon is rendered at the toolbar sizes (20–40 px) at 1× and 2×;
# QIcon picks the closest, so lines stay crisp on HiDPI screens.
_SIZES = (16, 20, 24, 32, 40, 48, 64, 80)
# The originals are drawn in this ink, which then becomes currentColor.
_SENTINEL = "#010203"
# Tools whose cursor has a guide mode (a «+»): a slot of its own, «tape+».
GUIDE_TOOLS = ("tape", "protractor")

# The pairing by file name, IngeTrazo's keys being the names (those of the
# originals): a theme's ``line.svg`` replaces the Line icon, its
# ``cursors/line.svg`` the Line tool's cursor and ``cursors/tape+.svg`` the
# Tape Measure's in guide mode.
CURSORS_FOLDER = "cursors"
# Found by drawing IngeTrazo's cursors, which Qt does on the main thread
# only: by reload(), before the page's server can ask.
_cursor_slots: list[str] = []


def _keys() -> tuple[list[str], list[str]]:
    """IngeTrazo's icon keys, and its cursor slots: the tools it draws a
    cursor for, Select (the standard arrow) and each guide mode."""
    from views import icons
    keys = list(getattr(icons, "_DRAW", {}))
    if not _cursor_slots:
        draw = _original_cursor or icons.tool_cursor
        found = [k for k in keys if k == "select" or draw(k) is not None]
        _cursor_slots.extend(found + [f"{k}+" for k in GUIDE_TOOLS
                                      if k in found])
    return keys, list(_cursor_slots)


# Where a cursor clicks, by the tool it serves. Found in the drawing itself,
# since each theme draws its own: the extreme dark pixel toward a corner —
# the tip of a pencil, a pipette, a tape's hook or a leader (bottom left),
# the point of an arrow (top left), the drop under a paint pot (bottom).
# Any other cursor clicks where IngeTrazo's own does (the centre of a move
# cross, a magnifier's lens…), else at its centre.
_TIP = ("line", "freehand", "rectangle", "rectangle_center", "rotated_rect",
        "circle", "polygon", "arc", "arc3", "center_arc", "pie", "geopath",
        "dimension", "text", "eyedropper", "tape", "eraser")
_POINT = ("select", "scale", "offset", "followme")
_DRIP = ("paint",)
# Toward which corner each kind looks, as a direction (x, y).
_DIRECTIONS = {"tip": (-1.0, 1.0), "point": (-1.0, -1.0), "drip": (-0.2, 1.0)}


def hotspot_kind(slot: str):
    """«tip», «point», «drip», or a fixed (x, y) fraction, for the cursor
    of the tool ``slot`` («tape», or «tape+» in guide mode)."""
    key = slot.rstrip("+")
    for kind, keys in (("tip", _TIP), ("point", _POINT), ("drip", _DRIP)):
        if key in keys:
            return kind
    own = _original_cursor(key) if _original_cursor is not None else None
    if own is None:
        return (0.5, 0.5)
    spot, last = own.hotSpot(), CURSOR_SIZE - 1
    return spot.x() / last, spot.y() / last


def find_hotspot(image: QImage, kind) -> tuple[int, int]:
    """The pixel of ``image`` where a cursor of ``kind`` clicks."""
    w, h = image.width(), image.height()
    if not isinstance(kind, str):
        return round(kind[0] * (w - 1)), round(kind[1] * (h - 1))
    dx, dy = _DIRECTIONS[kind]
    best, spot = None, (round(0.5 * (w - 1)), round(0.5 * (h - 1)))
    for y in range(h):
        for x in range(w):
            c = image.pixelColor(x, y)
            # Drawn, and not the white halo around the drawing.
            if c.alpha() > 160 and c.lightness() < 200:
                score = x * dx + y * dy
                if best is None or score > best:
                    best, spot = score, (x, y)
    return spot


# Names for the icons no toolbar action labels (the sheet composer's).
_LABELS = {
    "side_collapse": "Show or hide the sidebar", "side_expand": "Sidebar",
    "overflow_h": "More tools", "overflow_v": "More tools",
    "rectangle_center": "Center Rectangle", "image": "Image",
    "arr_left": "Align left", "arr_right": "Align right",
    "arr_top": "Align top", "arr_bottom": "Align bottom",
    "arr_hcenter": "Center horizontally", "arr_vcenter": "Center vertically",
    "arr_dist_h": "Distribute horizontally",
    "arr_dist_v": "Distribute vertically",
    "arr_duplicate": "Duplicate", "arr_group": "Group",
    "arr_ungroup": "Ungroup", "arr_lock": "Lock / unlock",
    "comp_vista": "View", "comp_norte": "North arrow",
    "comp_leyenda": "Legend", "comp_escala": "Scale bar",
    "comp_cajetin": "Title block", "comp_flecha": "Arrow",
    "comp_terreno": "Terrain", "comp_etiqueta": "Label",
    "comp_perfil": "Profile", "comp_nivel": "Level",
    "comp_llamada": "Callout",
    "refresh": "Refresh", "export_pdf": "Export PDF",
    "print_preview": "Print preview",
    "dimension_chain": "Chain dimension",
    "dimension_radius": "Radius dimension",
    "dimension_baseline": "Baseline dimension",
    "dimension_diameter": "Diameter dimension",
    "dimension_angular": "Angular dimension",
}


# ---- Interface texts ----------------------------------------------------------
# No program's name in them: a theme is whatever folder the user makes. The
# menus follow IngeTrazo's language; the page has its own choice.

LANGUAGES = ("fr", "en", "es")      # the page's, on its FR / EN / ES buttons

_TEXTS = {
    "es": {
        "Custom icons": "Iconos personalizados",
        "Choose icons…": "Elegir los iconos…",
        "Open icons folder": "Abrir la carpeta de iconos",
        "Reload icons": "Recargar los iconos",
        "Open a page in your browser to pair each IngeTrazo icon with one "
        "of yours.":
            "Abrir una página en el navegador para emparejar cada icono de "
            "IngeTrazo con uno de los suyos.",
        "Open the folder of the original icons and of yours.":
            "Abrir la carpeta de los iconos originales y de los suyos.",
        "Read the SVG files again and redraw the icons, without "
        "restarting.":
            "Volver a leer los archivos SVG y redibujar los iconos, sin "
            "reiniciar.",
        "Custom icons and cursors in use: {n}":
            "Iconos y cursores personalizados en uso: {n}",
        "Theme": "Tema",
        "None — IngeTrazo's own icons": "Ninguno: iconos de IngeTrazo",
        "Search tools": "Buscar herramientas",
        "Search icons": "Buscar iconos",
        "All folders": "Todas las carpetas",
        "Match by file name": "Emparejar por nombre de archivo",
        "Clear all": "Vaciar todo",
        "Open the «{folder}» folder": "Abrir la carpeta «{folder}»",
        "Add icons, or a theme as a folder of its own, then press Refresh.":
            "Añada iconos, o un tema como carpeta propia, y luego pulse "
            "Actualizar.",
        "Refresh": "Actualizar",
        "Read the folder again after adding or changing icons.":
            "Volver a leer la carpeta tras añadir o cambiar iconos.",
        "Drag to widen or narrow; double-click to reset.":
            "Arrastre para ensanchar o estrechar; doble clic para "
            "restablecer.",
        "Language of this page": "Idioma de esta página",
        "Toolbar icons": "Iconos de las barras",
        "3D view cursors": "Cursores de la vista 3D",
        "Library": "Biblioteca",
        "In use: {n}": "En uso: {n}",
        "standard arrow": "flecha estándar",
        "guide mode": "modo guía",
        "Drag an icon of the library onto a tool, or click it and then the "
        "tool. Drag one tool's icon onto another to swap them; ✕ gives a "
        "tool its own icon back.":
            "Arrastre un icono de la biblioteca sobre una herramienta, o "
            "haga clic en él y luego en la herramienta. Arrastre el icono de "
            "una herramienta sobre otra para intercambiarlos; ✕ le devuelve "
            "su propio icono.",
        "Saving…": "Guardando…",
        "Saved — IngeTrazo is up to date.":
            "Guardado: IngeTrazo está al día.",
        "Not saved: is IngeTrazo still open?":
            "No guardado: ¿sigue abierto IngeTrazo?",
        "The theme was changed elsewhere: reload this page.":
            "El tema se cambió en otro lugar: vuelva a cargar esta página.",
        "Matches by file name, in use until your first change.":
            "Emparejamiento por nombre de archivo, en uso hasta su primer "
            "cambio.",
        "Replace every choice with the matches by file name?":
            "¿Reemplazar todas las elecciones por el emparejamiento por "
            "nombre de archivo?",
        "Give every tool its own icon back?":
            "¿Devolver a cada herramienta su propio icono?",
        "Back to IngeTrazo's icon": "Volver al icono de IngeTrazo",
        "No themes yet: make a folder inside «{folder}» and put your SVG "
        "files in it.":
            "Todavía no hay temas: cree una carpeta dentro de «{folder}» y "
            "ponga en ella sus archivos SVG.",
        "IngeTrazo's own icons are in use. Choose a theme to pair icons.":
            "Se usan los iconos de IngeTrazo. Elija un tema para emparejar "
            "iconos.",
        "This theme has no SVG files yet.":
            "Este tema todavía no tiene archivos SVG.",
        "Saves": "Copias",
        "Save as…": "Guardar como…",
        "Keep these choices under a name, to load them again later.":
            "Guardar estas elecciones con un nombre, para volver a cargarlas "
            "más tarde.",
        "Load": "Cargar",
        "Delete": "Borrar",
        "No saves yet": "Todavía no hay copias",
        "Name of this save:": "Nombre de esta copia:",
        "Replace the save «{name}»?": "¿Reemplazar la copia «{name}»?",
        "Load «{name}»? It replaces the current choices.":
            "¿Cargar «{name}»? Reemplaza las elecciones actuales.",
        "Delete the save «{name}»?": "¿Borrar la copia «{name}»?",
        "Saved as «{name}».": "Guardado como «{name}».",
        "«{name}» loaded — IngeTrazo is up to date.":
            "«{name}» cargado: IngeTrazo está al día.",
        "{name} (this theme)": "{name} (este tema)",
        "Import the {n} icons shown": "Importar los {n} iconos mostrados",
        "Import {n} icons into «{name}»? They are copied into its folder "
        "«Importées».":
            "¿Importar {n} iconos en «{name}»? Se copian en su carpeta "
            "«Importées».",
        "Import files…": "Importar archivos…",
        "Add SVG files from your computer to this theme — or drop them on "
        "the library.":
            "Añadir a este tema archivos SVG de su ordenador, o soltarlos "
            "sobre la biblioteca.",
        "{n} icons imported into «{name}».":
            "{n} iconos importados en «{name}».",
        "Icons of another theme are copied into this one when you use "
        "them.":
            "Los iconos de otro tema se copian en este cuando los usa.",
        "Not imported: only SVG files can be.":
            "No importado: solo se pueden importar archivos SVG.",
        "Import the icon shown": "Importar el icono mostrado",
        "Import this icon into «{name}»? It is copied into its folder "
        "«Importées».":
            "¿Importar este icono en «{name}»? Se copia en su carpeta "
            "«Importées».",
        "1 icon imported into «{name}».": "1 icono importado en «{name}».",
    },
    "fr": {
        "Custom icons": "Icônes personnalisées",
        "Choose icons…": "Choisir les icônes…",
        "Open icons folder": "Ouvrir le dossier des icônes",
        "Reload icons": "Recharger les icônes",
        "Open a page in your browser to pair each IngeTrazo icon with one "
        "of yours.":
            "Ouvrir une page dans le navigateur pour associer chaque icône "
            "d'IngeTrazo à l'une des vôtres.",
        "Open the folder of the original icons and of yours.":
            "Ouvrir le dossier des icônes originales et des vôtres.",
        "Read the SVG files again and redraw the icons, without "
        "restarting.":
            "Relire les fichiers SVG et redessiner les icônes, sans "
            "redémarrer.",
        "Custom icons and cursors in use: {n}":
            "Icônes et curseurs personnalisés utilisés : {n}",
        "Theme": "Thème",
        "None — IngeTrazo's own icons": "Aucun : icônes d'IngeTrazo",
        "Search tools": "Chercher un outil",
        "Search icons": "Chercher une icône",
        "All folders": "Tous les dossiers",
        "Match by file name": "Associer par nom de fichier",
        "Clear all": "Tout vider",
        "Open the «{folder}» folder": "Ouvrir le dossier « {folder} »",
        "Add icons, or a theme as a folder of its own, then press Refresh.":
            "Ajoutez des icônes, ou un thème dans un dossier à lui, puis "
            "cliquez sur Actualiser.",
        "Refresh": "Actualiser",
        "Read the folder again after adding or changing icons.":
            "Relire le dossier après avoir ajouté ou modifié des icônes.",
        "Drag to widen or narrow; double-click to reset.":
            "Glisser pour élargir ou réduire ; double-clic pour revenir à "
            "la largeur d'origine.",
        "Language of this page": "Langue de cette page",
        "Toolbar icons": "Icônes des barres d'outils",
        "3D view cursors": "Curseurs de la vue 3D",
        "Library": "Bibliothèque",
        "In use: {n}": "Utilisée : {n}",
        "standard arrow": "flèche standard",
        "guide mode": "mode guide",
        "Drag an icon of the library onto a tool, or click it and then the "
        "tool. Drag one tool's icon onto another to swap them; ✕ gives a "
        "tool its own icon back.":
            "Glissez une icône de la bibliothèque sur un outil, ou cliquez "
            "dessus puis sur l'outil. Glissez l'icône d'un outil sur un "
            "autre pour les échanger ; ✕ lui rend son icône d'origine.",
        "Saving…": "Enregistrement…",
        "Saved — IngeTrazo is up to date.":
            "Enregistré : IngeTrazo est à jour.",
        "Not saved: is IngeTrazo still open?":
            "Non enregistré : IngeTrazo est-il toujours ouvert ?",
        "The theme was changed elsewhere: reload this page.":
            "Le thème a été changé ailleurs : rechargez cette page.",
        "Matches by file name, in use until your first change.":
            "Associations par nom de fichier, utilisées jusqu'à votre "
            "premier changement.",
        "Replace every choice with the matches by file name?":
            "Remplacer tous les choix par les associations par nom de "
            "fichier ?",
        "Give every tool its own icon back?":
            "Rendre à chaque outil son icône d'origine ?",
        "Back to IngeTrazo's icon": "Revenir à l'icône d'IngeTrazo",
        "No themes yet: make a folder inside «{folder}» and put your SVG "
        "files in it.":
            "Aucun thème pour l'instant : créez un dossier dans "
            "« {folder} » et mettez-y vos fichiers SVG.",
        "IngeTrazo's own icons are in use. Choose a theme to pair icons.":
            "Les icônes d'IngeTrazo sont utilisées. Choisissez un thème "
            "pour associer des icônes.",
        "This theme has no SVG files yet.":
            "Ce thème n'a pas encore de fichiers SVG.",
        "Saves": "Sauvegardes",
        "Save as…": "Enregistrer sous…",
        "Keep these choices under a name, to load them again later.":
            "Garder ces choix sous un nom, pour les recharger plus tard.",
        "Load": "Charger",
        "Delete": "Supprimer",
        "No saves yet": "Aucune sauvegarde",
        "Name of this save:": "Nom de cette sauvegarde :",
        "Replace the save «{name}»?":
            "Remplacer la sauvegarde « {name} » ?",
        "Load «{name}»? It replaces the current choices.":
            "Charger « {name} » ? Elle remplace les choix actuels.",
        "Delete the save «{name}»?":
            "Supprimer la sauvegarde « {name} » ?",
        "Saved as «{name}».": "Enregistré sous « {name} ».",
        "«{name}» loaded — IngeTrazo is up to date.":
            "« {name} » chargée : IngeTrazo est à jour.",
        "{name} (this theme)": "{name} (ce thème)",
        "Import the {n} icons shown": "Importer les {n} icônes affichées",
        "Import {n} icons into «{name}»? They are copied into its folder "
        "«Importées».":
            "Importer {n} icônes dans « {name} » ? Elles sont copiées dans "
            "son dossier « Importées ».",
        "Import files…": "Importer des fichiers…",
        "Add SVG files from your computer to this theme — or drop them on "
        "the library.":
            "Ajouter à ce thème des fichiers SVG de votre ordinateur — ou "
            "les déposer sur la bibliothèque.",
        "{n} icons imported into «{name}».":
            "{n} icônes importées dans « {name} ».",
        "Icons of another theme are copied into this one when you use "
        "them.":
            "Les icônes d'un autre thème sont copiées dans celui-ci quand "
            "vous les utilisez.",
        "Not imported: only SVG files can be.":
            "Non importé : seuls les fichiers SVG peuvent l'être.",
        "Import the icon shown": "Importer l'icône affichée",
        "Import this icon into «{name}»? It is copied into its folder "
        "«Importées».":
            "Importer cette icône dans « {name} » ? Elle est copiée dans son "
            "dossier « Importées ».",
        "1 icon imported into «{name}».":
            "1 icône importée dans « {name} ».",
    },
}

# The texts the page shows (its own strings, translated here).
_PAGE_TEXTS = (
    "Custom icons", "Theme", "None — IngeTrazo's own icons", "Search tools",
    "Search icons", "All folders", "Match by file name", "Clear all",
    "Add icons, or a theme as a folder of its own, then press Refresh.",
    "Refresh", "Read the folder again after adding or changing icons.",
    "Drag to widen or narrow; double-click to reset.",
    "Language of this page", "Toolbar icons", "3D view cursors", "Library",
    "In use: {n}", "standard arrow",
    "Drag an icon of the library onto a tool, or click it and then the "
    "tool. Drag one tool's icon onto another to swap them; ✕ gives a "
    "tool its own icon back.",
    "Saving…", "Saved — IngeTrazo is up to date.",
    "Not saved: is IngeTrazo still open?",
    "The theme was changed elsewhere: reload this page.",
    "Matches by file name, in use until your first change.",
    "Replace every choice with the matches by file name?",
    "Give every tool its own icon back?", "Back to IngeTrazo's icon",
    "IngeTrazo's own icons are in use. Choose a theme to pair icons.",
    "This theme has no SVG files yet.",
    "Saves", "Save as…",
    "Keep these choices under a name, to load them again later.",
    "Load", "Delete", "No saves yet", "Name of this save:",
    "Replace the save «{name}»?",
    "Load «{name}»? It replaces the current choices.",
    "Delete the save «{name}»?", "Saved as «{name}».",
    "«{name}» loaded — IngeTrazo is up to date.",
    "{name} (this theme)", "Import the {n} icons shown",
    "Import {n} icons into «{name}»? They are copied into its folder "
    "«Importées».",
    "Import files…",
    "Add SVG files from your computer to this theme — or drop them on "
    "the library.",
    "{n} icons imported into «{name}».",
    "Icons of another theme are copied into this one when you use them.",
    "Not imported: only SVG files can be.",
    "Import the icon shown",
    "Import this icon into «{name}»? It is copied into its folder "
    "«Importées».",
    "1 icon imported into «{name}».",
)

# The tools' names on the page in French (IngeTrazo has no French yet).
_FR_LABELS = {
    "select": "Sélectionner", "eraser": "Gomme", "paint": "Peindre",
    "line": "Ligne", "freehand": "Main levée", "rectangle": "Rectangle",
    "rectangle_center": "Rectangle par le centre",
    "rotated_rect": "Rectangle orienté", "circle": "Cercle",
    "polygon": "Polygone", "arc": "Arc", "arc3": "Arc 3 points",
    "center_arc": "Arc par le centre", "pie": "Secteur",
    "pushpull": "Pousser / tirer", "move": "Déplacer",
    "rotate": "Faire pivoter", "scale": "Échelle", "flip": "Symétrie",
    "followme": "Suivez-moi", "offset": "Décalage", "fillet": "Congé 3D",
    "eyedropper": "Échantillonner un matériau", "tape": "Mètre",
    "protractor": "Rapporteur", "dimension": "Cote",
    "dimension_chain": "Cotation en chaîne",
    "dimension_radius": "Cote de rayon",
    "dimension_baseline": "Cotation depuis une référence",
    "dimension_diameter": "Cote de diamètre",
    "dimension_angular": "Cote angulaire",
    "dimension_style": "Style de cote", "text": "Texte",
    "text3d": "Texte 3D", "geopath": "Chemin",
    "section": "Plan de coupe", "section_planes": "Plans de coupe",
    "section_cuts": "Coupes", "section_fill": "Remplissage de coupe",
    "styles": "Styles", "shadows": "Ombres",
    "orbit": "Orbite", "pan": "Panoramique", "zoom": "Zoom",
    "zoom_window": "Fenêtre de zoom", "zoom_extents": "Zoom étendu",
    "zoom_selection": "Zoom sur la sélection",
    "position_camera": "Positionner la caméra", "walk": "Visite",
    "look_around": "Pivoter", "first_person": "Première personne",
    "view_iso": "Iso", "view_top": "Dessus", "view_bottom": "Dessous",
    "view_front": "Face", "view_back": "Arrière", "view_right": "Droite",
    "view_left": "Gauche",
    "undo": "Annuler", "redo": "Rétablir", "save": "Enregistrer",
    "refresh": "Actualiser", "export_pdf": "Exporter en PDF",
    "print_preview": "Aperçu avant impression", "image": "Image",
    "outer_shell": "Coque extérieure", "solid_union": "Union",
    "solid_subtract": "Soustraire", "solid_trim": "Ajuster",
    "solid_intersect": "Intersection", "solid_split": "Diviser",
    "side_collapse": "Afficher ou masquer le panneau latéral",
    "side_expand": "Panneau latéral",
    "overflow_h": "Plus d'outils", "overflow_v": "Plus d'outils",
    "arr_left": "Aligner à gauche", "arr_right": "Aligner à droite",
    "arr_top": "Aligner en haut", "arr_bottom": "Aligner en bas",
    "arr_hcenter": "Centrer horizontalement",
    "arr_vcenter": "Centrer verticalement",
    "arr_dist_h": "Répartir horizontalement",
    "arr_dist_v": "Répartir verticalement",
    "arr_duplicate": "Dupliquer", "arr_group": "Grouper",
    "arr_ungroup": "Dégrouper", "arr_lock": "Verrouiller / déverrouiller",
    "comp_vista": "Vue", "comp_norte": "Flèche du nord",
    "comp_leyenda": "Légende", "comp_escala": "Échelle graphique",
    "comp_cajetin": "Cartouche", "comp_flecha": "Flèche",
    "comp_terreno": "Terrain", "comp_etiqueta": "Étiquette",
    "comp_perfil": "Profil", "comp_nivel": "Niveau",
    "comp_llamada": "Rappel",
}


def _lang() -> str:
    try:
        from core.i18n import current_language
        return current_language()
    except Exception:  # noqa: BLE001
        return "en"


def _tr(text: str, **kw) -> str:
    """``text`` in the interface language, with ``{fields}`` filled."""
    out = _TEXTS.get(_lang(), {}).get(text, text)
    return out.format(**kw) if kw else out


def _app_tr(text: str) -> str:
    """``text`` through IngeTrazo's own catalogue (its tool names)."""
    try:
        from core.i18n import tr
        return tr(text)
    except Exception:  # noqa: BLE001
        return text


# ---- Folders, themes and pairing ----------------------------------------------

def root_folder() -> Path:
    """``custom_icons``, beside the per-user plugins folder."""
    from core.extensions import user_plugins_dir
    return user_plugins_dir().parent / FOLDER_NAME


def originals_dir() -> Path:
    return root_folder() / ORIGINALS


def custom_dir() -> Path:
    return root_folder() / CUSTOM


def themes() -> list[str]:
    """The theme folders in «Icônes custom», by name."""
    try:
        names = [d.name for d in custom_dir().iterdir()
                 if d.is_dir() and not d.name.startswith(".")]
    except OSError:
        return []
    return sorted(names, key=str.lower)


def theme_dir(theme: str) -> Path:
    return custom_dir() / theme


def active_theme() -> str | None:
    """The theme in use: the one chosen on the page, by default the first
    there is; None for IngeTrazo's own icons."""
    names = themes()
    try:
        data = json.loads((root_folder() / SETTINGS).read_text(
            encoding="utf-8"))
        if "theme" in data:
            if data["theme"] is None:
                return None                 # chosen: IngeTrazo's own
            if data["theme"] in names:
                return data["theme"]
    except FileNotFoundError:
        pass
    except (OSError, ValueError, AttributeError):
        log.warning("%s unreadable: first theme used", SETTINGS)
    return names[0] if names else None


def save_theme(theme: str | None) -> None:
    _write_json(root_folder() / SETTINGS, {"theme": theme})


def theme_pool(theme: str | None) -> dict[str, Path]:
    """A theme's SVG files, from all its subfolders, by their path in it
    (``icons/select.svg``)."""
    if not theme:
        return {}
    folder = theme_dir(theme)
    try:
        return {p.relative_to(folder).as_posix(): p
                for p in folder.rglob("*.svg") if p.is_file()}
    except OSError:
        return {}


def _folder(path: str) -> str:
    return path.rsplit("/", 1)[0] if "/" in path else ""


def suggested_mapping(paths) -> dict:
    """The pairing by file name (see CURSORS_FOLDER), case aside. A name
    found in several subfolders is taken from the one holding the most of
    these names — the theme's set rather than a few spares — then from the
    shallowest."""
    icon_keys, cursor_keys = _keys()
    wanted = {"icons": {k.lower(): k for k in icon_keys},
              "cursors": {k.lower(): k for k in cursor_keys}}
    where = {"icons": {}, "cursors": {}}        # key → the paths named so
    score: dict[str, int] = {}
    for path in paths:
        folder = _folder(path)
        group = ("cursors" if folder.rsplit("/", 1)[-1].lower() ==
                 CURSORS_FOLDER else "icons")
        key = wanted[group].get(Path(path).stem.lower())
        if key is not None:
            where[group].setdefault(key, []).append(path)
            score[folder] = score.get(folder, 0) + 1

    def pick(found):
        return min(found, key=lambda p: (-score[_folder(p)], p.count("/"),
                                         p.lower()))

    return {group: {key: pick(found) for key, found in pairs.items()}
            for group, pairs in where.items()}


def has_mapping(theme: str | None) -> bool:
    return bool(theme) and (theme_dir(theme) / MAPPING).is_file()


def _read_pairs(path: Path) -> dict:
    """A pairing file's pairs (FileNotFoundError, OSError, ValueError)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    out = {"icons": {}, "cursors": {}}
    for group, pairs in out.items():
        given = data.get(group) if isinstance(data, dict) else None
        for key, name in (given or {}).items():
            if isinstance(key, str) and isinstance(name, str):
                pairs[key] = name
    return out


def load_mapping(theme: str | None, paths) -> dict:
    """A theme's saved pairing, or the one by file name until its first
    save."""
    if not theme:
        return {"icons": {}, "cursors": {}}
    try:
        return _read_pairs(theme_dir(theme) / MAPPING)
    except FileNotFoundError:
        return suggested_mapping(paths)
    except (OSError, ValueError):
        log.warning("%s of %r unreadable: pairing by file name used",
                    MAPPING, theme)
        return suggested_mapping(paths)


def save_mapping(theme: str, mapping: dict) -> None:
    _write_json(theme_dir(theme) / MAPPING, mapping)


# Saves: a theme's pairing kept under a name, to load again later, in its
# folder «Sauvegardes» (so they travel with the theme).

def save_file_name(name: str) -> str:
    """``name`` made a safe file name (no folder, no reserved name); empty
    if nothing is left of it."""
    clean = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(name)).strip(" .")[:80]
    if clean.split(".")[0].upper() in _RESERVED:
        clean = "_" + clean
    return clean


_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
             *(f"LPT{i}" for i in range(1, 10))}


def list_saves(theme: str | None) -> list[dict]:
    """A theme's saves, the newest first: name and date (ISO)."""
    if not theme:
        return []
    from datetime import datetime
    try:
        files = [p for p in (theme_dir(theme) / SAVES).glob("*.json")
                 if p.is_file()]
    except OSError:
        return []
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return [{"name": p.stem, "date": datetime.fromtimestamp(
        p.stat().st_mtime).isoformat(timespec="minutes")} for p in files]


def write_save(theme: str, name: str, mapping: dict) -> str:
    """Keep ``mapping`` as the theme's save ``name``; the name kept."""
    clean = save_file_name(name)
    if not clean:
        raise ValueError("empty name")
    _write_json(theme_dir(theme) / SAVES / f"{clean}.json", mapping)
    return clean


def read_save(theme: str, name: str) -> dict:
    clean = save_file_name(name)
    if not clean or clean != name:
        raise ValueError(f"no save {name!r}")
    return _read_pairs(theme_dir(theme) / SAVES / f"{clean}.json")


def delete_save(theme: str, name: str) -> None:
    clean = save_file_name(name)
    if not clean or clean != name:
        raise ValueError(f"no save {name!r}")
    (theme_dir(theme) / SAVES / f"{clean}.json").unlink(missing_ok=True)


# Imports: icons brought into a theme — from another theme, kept in
# «Importées/<that theme>/<its path>», or files from the computer, in
# «Importées/» — so a theme stays whole when its folder is shared.

MAX_IMPORT = 2 * 1024 * 1024        # bytes, one file from the computer


def _is_svg(data: bytes) -> bool:
    head = data[:4096].lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    return head.startswith((b"<?xml", b"<svg", b"<!--", b"<!doctype")) \
        and b"<svg" in head


def _copy_in(folder: Path, rel: str, data: bytes) -> str:
    """Write ``data`` at ``folder/rel``; a different file already there
    keeps its place and the copy becomes «name (2)…». Its path in the
    folder."""
    target = folder / rel
    stem, n = target.stem, 1
    while target.exists():
        if target.is_file() and target.read_bytes() == data:
            break                       # imported before: the same file
        n += 1
        target = target.with_name(f"{stem} ({n}){target.suffix}")
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return target.relative_to(folder).as_posix()


def import_from_theme(theme: str, source: str, paths) -> list[str]:
    """Copy icons of theme ``source`` (by their path there) into
    ``theme``; their paths in ``theme``, in the same order."""
    if source == theme:
        return list(paths)
    pool = theme_pool(source)
    out = []
    for path in paths:
        if path not in pool:
            raise ValueError(f"no icon {path!r} in {source!r}")
        out.append(_copy_in(theme_dir(theme), f"{IMPORTS}/{source}/{path}",
                            pool[path].read_bytes()))
    return out


def import_file(theme: str, name: str, data: bytes) -> str:
    """Add an SVG file from the computer to ``theme``; its path there."""
    if len(data) > MAX_IMPORT or not _is_svg(data):
        raise ValueError(f"{name!r} is not an SVG file")
    stem = save_file_name(Path(str(name)).stem) or "icon"
    return _copy_in(theme_dir(theme), f"{IMPORTS}/{stem}.svg", data)


def _write_json(path: Path, data) -> None:
    """Write ``path`` whole or not at all."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True,
                              ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


# ---- The originals ------------------------------------------------------------

def original_svg(key: str, draw) -> bytes | None:
    """The built-in icon ``key`` as an SVG document, its ink written as
    ``currentColor``. None without QtSvg, or if the drawing fails on an SVG
    device."""
    if QSvgGenerator is None:
        return None
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    gen = QSvgGenerator()
    gen.setOutputDevice(buf)
    gen.setSize(QSize(CANVAS, CANVAS))
    gen.setViewBox(QRect(0, 0, CANVAS, CANVAS))
    gen.setTitle(key)
    gen.setDescription("IngeTrazo icon, written again at each start. Copy "
                       "it to make your own.")
    p = QPainter(gen)
    try:
        # The pen views.icons._canvas() starts every drawing with.
        p.setRenderHint(QPainter.Antialiasing, True)
        ink = QColor(_SENTINEL)
        pen = QPen(ink, 3.0)
        pen.setJoinStyle(Qt.RoundJoin)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        draw(p, ink)
    except Exception:  # noqa: BLE001 — one odd drawing must not stop the rest
        log.exception("icon %r could not be exported", key)
        return None
    finally:
        p.end()
    text = bytes(buf.data()).decode("utf-8").replace(_SENTINEL, "currentColor")
    # Pixels, not the millimetres Qt derives from the screen resolution.
    text = re.sub(r'width="[^"]*" height="[^"]*"',
                  f'width="{CANVAS}" height="{CANVAS}"', text, count=1)
    return text.encode("utf-8")


def write_originals(folder: Path, drawings: dict) -> None:
    """Every built-in icon as ``<key>.svg`` in ``folder``; a file already
    up to date is left alone."""
    folder.mkdir(parents=True, exist_ok=True)
    for key, draw in drawings.items():
        data = original_svg(key, draw)
        if data is None:
            continue
        path = folder / f"{key}.svg"
        try:
            if not path.is_file() or path.read_bytes() != data:
                path.write_bytes(data)
        except OSError:
            log.warning("cannot write %s", path)


# ---- Rendering ----------------------------------------------------------------

def _ink() -> str:
    """The interface's text colour, as views.icons paints its icons."""
    app = QApplication.instance()
    if app is None:
        return "#282c34"
    return app.palette().windowText().color().name()


def render_svg(data: bytes, size: int, ink: str | None = None) -> QImage | None:
    """``data`` drawn in a ``size`` × ``size`` image, ``currentColor``
    painted in ``ink`` (the interface's by default); None if it is not a
    valid SVG."""
    svg = QByteArray(data.replace(b"currentColor", (ink or _ink()).encode()))
    if QSvgRenderer is not None:
        renderer = QSvgRenderer(svg)
        if not renderer.isValid():
            return None
        renderer.setAspectRatioMode(Qt.KeepAspectRatio)
        image = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
        image.fill(Qt.transparent)
        p = QPainter(image)
        p.setRenderHint(QPainter.Antialiasing, True)
        renderer.render(p, QRectF(0, 0, size, size))
        p.end()
        return image
    # Without QtSvg, Qt's svg image-format plugin can still read it.
    buf = QBuffer()
    buf.setData(svg)
    buf.open(QIODevice.ReadOnly)
    reader = QImageReader(buf, b"svg")
    reader.setScaledSize(QSize(size, size))
    image = reader.read()
    return None if image.isNull() else image


def svg_icon(data: bytes, ink: str | None = None) -> QIcon | None:
    """``data`` as a QIcon; None if it is not a valid SVG."""
    icon = QIcon()
    for size in _SIZES:
        image = render_svg(data, size, ink)
        if image is None:
            return None
        icon.addPixmap(QPixmap.fromImage(image))
    return icon


def svg_cursor(data: bytes, kind=(0.5, 0.5), plus: bool = False,
               dpr: float = 1.0) -> QCursor | None:
    """``data`` as a CURSOR_SIZE cursor clicking where ``kind`` says (see
    hotspot_kind); ``plus`` adds IngeTrazo's guide-mode «+» at the top
    right. None if it is not a valid SVG."""
    image = render_svg(data, round(CURSOR_SIZE * dpr), "#000000")
    if image is None:
        return None
    hx, hy = find_hotspot(image, kind)       # before the «+» is drawn
    pm = QPixmap.fromImage(image)
    pm.setDevicePixelRatio(dpr)
    if plus:
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing, True)
        for color, width in ((Qt.white, 4.5), (Qt.black, 2.2)):
            pen = QPen(color, width)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.drawLine(QPointF(23.5, 4.5), QPointF(30.5, 4.5))
            p.drawLine(QPointF(27.0, 1.0), QPointF(27.0, 8.0))
        p.end()
    return QCursor(pm, round(hx / dpr), round(hy / dpr))


# ---- Taking over views.icons.tool_icon and tool_cursor ------------------------

_pool: dict[str, Path] = {}         # the theme's SVG files, by path in it
_mapping: dict = {"icons": {}, "cursors": {}}
_cache: dict[tuple, object] = {}
_original_icon = None               # views.icons.tool_icon, IngeTrazo's own
_original_cursor = None             # views.icons.tool_cursor
_editor = None                      # the page's server, once opened


def _read(name: str) -> bytes | None:
    path = _pool.get(name)
    if path is None:
        return None
    try:
        return path.read_bytes()
    except OSError:
        log.warning("cannot read %s", path)
        return None


def _tool_icon(key: str) -> QIcon:
    """views.icons.tool_icon, with the user's SVG where one is paired."""
    name = _mapping["icons"].get(key)
    if name in _pool:
        ink = _ink()                    # re-rendered when the theme flips
        icon = _cache.get((name, ink))
        if icon is None:
            data = _read(name)
            icon = svg_icon(data, ink) if data is not None else None
            if icon is None:
                log.warning("%s.svg is not a valid SVG; built-in icon kept",
                            name)
                _pool.pop(name, None)
            else:
                _cache[(name, ink)] = icon
        if icon is not None:
            return icon
    return _original_icon(key)


def _tool_cursor(key, plus: bool = False):
    """views.icons.tool_cursor, with the user's SVG where one is paired. In
    guide mode the «tape+» slot wins; failing it, the plain cursor gets a
    «+»."""
    if key:
        cursors = _mapping["cursors"]
        name = cursors.get(f"{key}+") if plus else None
        draw_plus = False
        if name not in _pool:
            name, draw_plus = cursors.get(key), plus
        if name in _pool:
            app = QApplication.instance()
            screen = app.primaryScreen() if app is not None else None
            dpr = round(screen.devicePixelRatio(), 2) if screen else 1.0
            # By tool too: where a cursor clicks depends on the tool.
            cached = (key, name, draw_plus, dpr)
            cursor = _cache.get(cached)
            if cursor is None:
                data = _read(name)
                cursor = (svg_cursor(data, hotspot_kind(key), draw_plus, dpr)
                          if data is not None else None)
                if cursor is None:
                    log.warning("%s.svg is not a valid SVG; built-in "
                                "cursor kept", name)
                    _pool.pop(name, None)
                else:
                    _cache[cached] = cursor
            if cursor is not None:
                return cursor
    return _original_cursor(key, plus)


def _take_over(name: str, replacement) -> object:
    """Point every module's ``name`` (views.icons' function) at
    ``replacement`` and return IngeTrazo's own. Modules import it by name
    (``from views.icons import tool_icon``), so replacing it in views.icons
    alone would miss the main window."""
    from views import icons
    current = getattr(icons, name)
    # Loaded twice in one process (tests), the first copy already took over.
    original = getattr(current, "custom_icons_original", current)
    replacement.custom_icons_original = original
    for module in list(sys.modules.values()):
        names = getattr(module, "__dict__", None)
        if not isinstance(names, dict):
            continue
        found = names.get(name)
        if found is original or hasattr(found, "custom_icons_original"):
            setattr(module, name, replacement)
    return original


def _install() -> None:
    global _original_icon, _original_cursor
    _original_icon = _take_over("tool_icon", _tool_icon)
    _original_cursor = _take_over("tool_cursor", _tool_cursor)


def _redraw(window) -> None:
    """Repaint the icons already built (the toolbars exist before any
    extension's setup runs) and the 3D view's pointer. These are IngeTrazo
    internals — the calls its theme switch makes — so each is optional."""
    refresh = getattr(window, "_refresh_toolbar_icons", None)
    if callable(refresh):
        refresh()
    try:
        from views.theme import _redraw_icons
        _redraw_icons(QApplication.instance())
    except Exception:  # noqa: BLE001
        log.debug("views.theme._redraw_icons unavailable", exc_info=True)
    viewport = getattr(window, "viewport", None)
    update = getattr(viewport, "_apply_tool_cursor", None)
    if callable(update):
        try:
            update()
        except Exception:  # noqa: BLE001
            log.debug("viewport cursor not refreshed", exc_info=True)


def reload(window=None) -> int:
    """Write the originals, read the theme in use — its files and its
    pairing — again and redraw; the number of icons and cursors replaced."""
    from views import icons
    drawings = dict(getattr(icons, "_DRAW", {}))
    if not drawings:
        log.warning("views.icons._DRAW not found: originals not exported")
    try:
        write_originals(originals_dir(), drawings)
        custom_dir().mkdir(parents=True, exist_ok=True)
    except OSError:
        log.exception("icons folder unavailable")
    _keys()                             # the cursor slots, on this thread
    theme = active_theme()
    _pool.clear()
    _pool.update(theme_pool(theme))
    _mapping.clear()
    _mapping.update(load_mapping(theme, _pool))
    _cache.clear()
    if window is not None:
        _redraw(window)
    return sum(name in _pool for pairs in _mapping.values()
               for name in pairs.values())


# ---- The page -----------------------------------------------------------------

def _png(pixmap) -> bytes:
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    pixmap.toImage().save(buf, "PNG")
    return bytes(buf.data())


def _catalogue(lang: str) -> dict:
    """IngeTrazo's own translations into ``lang`` (English → ``lang``)."""
    try:
        from core.paths import app_root
        return json.loads((app_root() / "i18n" / f"{lang}.json").read_text(
            encoding="utf-8"))
    except Exception:  # noqa: BLE001 — no catalogue, English names
        return {}


def _source(text: str) -> str:
    """The English source of a text IngeTrazo translated."""
    try:
        from core.i18n import source_of
        return source_of(text)
    except Exception:  # noqa: BLE001
        return text


def editor_slots(window) -> tuple[list[dict], dict[str, bytes]]:
    """What the page lists — every icon, then every cursor, in toolbar
    order, each named in the page's languages — and the PNG previews of
    IngeTrazo's own cursors. Built here, on the main thread: Qt's pixmaps
    live there."""
    from views import icons
    drawings = getattr(icons, "_DRAW", {})
    english: dict[str, str] = {}
    order: list[str] = []
    for action, key in getattr(window, "_icon_actions", []):
        if key not in order:
            order.append(key)
        try:
            text = (action.text() or action.toolTip()).split("\n")[0]
        except Exception:  # noqa: BLE001
            text = ""
        if text:
            english.setdefault(key, _source(text.replace("&", "").strip()))
    for key, name in _LABELS.items():
        english[key] = name
    for key, tool in (getattr(window, "_tools", None) or {}).items():
        if getattr(tool, "name", ""):
            english[key] = tool.name        # the tools' names are English
    order += [k for k in drawings if k not in order]
    keys = [k for k in order if k in drawings]
    spanish = _catalogue("es")

    def labels(key: str, guide: bool = False) -> dict[str, str]:
        en = english.get(key, key)
        out = {"en": en, "fr": _FR_LABELS.get(key, en),
               "es": spanish.get(en, en)}
        if guide:
            for lang, name in out.items():
                word = _TEXTS.get(lang, {}).get("guide mode", "guide mode")
                out[lang] = f"{name} ({word})"
        return out

    slots = [{"id": f"icons:{k}", "group": "icons", "key": k,
              "labels": labels(k), "original": f"original/{k}.svg"}
             for k in keys]
    previews: dict[str, bytes] = {}
    for key in ["select"] + [k for k in keys if k != "select"]:
        for plus in ((False, True) if key in GUIDE_TOOLS else (False,)):
            cursor = _original_cursor(key, plus)
            if cursor is None and key != "select":
                continue
            slot = f"{key}+" if plus else key
            original = None
            if cursor is not None:
                previews[f"{slot}.png"] = _png(cursor.pixmap())
                original = f"preview/{slot}.png"
            slots.append({"id": f"cursors:{slot}", "group": "cursors",
                          "key": slot, "labels": labels(key, plus),
                          "original": original})
    return slots, previews


def page_texts() -> dict[str, dict[str, str]]:
    """The page's texts in each of its languages."""
    out = {}
    for lang in LANGUAGES:
        table = _TEXTS.get(lang, {})

        def tr(text, **kw):
            found = table.get(text, text)
            return found.format(**kw) if kw else found

        texts = {t: tr(t) for t in _PAGE_TEXTS}
        texts["open folder"] = tr("Open the «{folder}» folder", folder=CUSTOM)
        texts["no themes"] = tr("No themes yet: make a folder inside "
                                "«{folder}» and put your SVG files in it.",
                                folder=CUSTOM)
        out[lang] = texts
    return out


def open_editor(window, browse: bool = True) -> str:
    """Start the page's local server (once) and open it in the browser;
    its address."""
    global _editor
    slots, previews = editor_slots(window)

    def changed() -> None:
        """The page saved a pairing or chose a theme (main thread)."""
        n = reload(window)
        viewport = getattr(window, "viewport", None)
        if viewport is not None:
            viewport.flash_status(
                _tr("Custom icons and cursors in use: {n}", n=n), 3000)

    if _editor is None:
        from .editor import Editor
        _editor = Editor(sys.modules[__name__], slots, previews,
                         page_texts(), changed)
        QApplication.instance().aboutToQuit.connect(_editor.close)
    else:
        _editor.update(slots, previews, page_texts(), changed)
    if browse:
        QDesktopServices.openUrl(QUrl(_editor.url))
    return _editor.url


def _open(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))


def open_custom_folder() -> None:
    """«Icônes custom», where themes are added (the page's button)."""
    _open(custom_dir())


def _open_folder() -> None:
    _open(root_folder())


def setup(app) -> None:
    _install()
    reload(app.window)
    menu = app.add_menu(_tr("Custom icons"))
    if menu is None:
        return
    act = menu.addAction(_tr("Choose icons…"))
    act.setStatusTip(_tr("Open a page in your browser to pair each "
                         "IngeTrazo icon with one of yours."))
    act.triggered.connect(lambda: open_editor(app.window))

    act = menu.addAction(_tr("Open icons folder"))
    act.setStatusTip(_tr("Open the folder of the original icons and of "
                         "yours."))
    act.triggered.connect(_open_folder)

    def on_reload() -> None:
        n = reload(app.window)
        app.viewport.flash_status(
            _tr("Custom icons and cursors in use: {n}", n=n), 3000)

    act = menu.addAction(_tr("Reload icons"))
    act.setStatusTip(_tr("Read the SVG files again and redraw the icons, "
                         "without restarting."))
    act.triggered.connect(on_reload)
