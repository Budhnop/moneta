"""The owner's yes, given on the Mac itself, before any write.

The code from a *_preparer tool proves nothing about a human: the model reads it and can pass it on. So every WR_SAVE_*
tool first opens a macOS dialog showing the server's own summary of what will be written (never the model's words).
Only a click on « Enregistrer » lets the write go on; Annuler, closing it or no answer within SECONDS writes nothing.
AUTOMATIC is for fictitious data only (tests, an agent's fictitious mode); the real server forces it off, and it is ignored
whenever a base or the data folder is in a real folder, or none is declared, whatever program asks (scripts in the terminal included).

The dialog shows the whole summary or nothing: a summary taller than the screen is refused before any window opens
(TooLong), never cut, so nothing written can be hidden below what is seen. Each step's values stay on one line: a line
break in a name or a title cannot pass for another step.
"""
import json
import os
from pathlib import Path
import subprocess

import unicodedata

from socle.declaration import DeclarationAbsente, actuelle
from socle.externe import blurs, visible

SECONDS = 50
DEBUT_NEUTRE = 'Un agent va écrire :'
FIN_NEUTRE = "Enregistrer seulement si c'est exactement ce que tu as demandé."
# Measured on the Mac (NSAlert, 888 pt of usable screen height): 38 rows of 55 characters stay under 730 pt.
ROW_CHARS = 55
MAX_ROWS = 38
BREAKS = ('\r\n', '\r', ' ', ' ', '\x85')

AUTOMATIC = os.environ.get('SOCLE_CONFIRMATION') == 'automatique'
# Full path: a program named osascript placed earlier on the PATH could otherwise answer « Enregistrer ».
OSASCRIPT = '/usr/bin/osascript'

# The text reaches the dialog as arguments, never pasted into the script: nothing in a name or a note can change it.
SCRIPT = ('on run argv\n'
          'activate\n'
          'set answer to display dialog (item 2 of argv) with title (item 1 of argv) buttons {"Annuler", "Enregistrer"} '
          'default button "Annuler" cancel button "Annuler" with icon caution giving up after (item 3 of argv as integer)\n'
          'if gave up of answer then return "delai"\n'
          'return button returned of answer\n'
          'end run')


class TooLong(Exception):
    """The whole summary would not fit in the dialog: nothing is shown and nothing is written."""


class Blurred(TooLong):
    """The summary holds text the dialog could not show faithfully (externe.blurs): refused the same way (D054)."""


def lines_of(value):
    """Every kind of line break as \\n (U+2028 and U+2029 break a line in the dialog too)."""
    for mark in BREAKS:
        value = value.replace(mark, '\n')
    return value


def one_line(value):
    """A value kept on its own line: each line break shows as ⏎."""
    return ' ⏎ '.join(visible(lines_of(str(value))).split('\n'))


def describe(number, summary, title=None):
    """One step as one line: « 2. Paiement — membre : Alice Exemple, montant : 10.0 »."""
    parts = [f'{key} : ' + one_line(value if isinstance(value, (str, int, float)) else json.dumps(value, ensure_ascii=False))
             for key, value in summary.items()]
    return f'{number}. ' + (f'{one_line(title)} — ' if title else '') + ', '.join(parts)


def text(lines):
    try:
        textes = actuelle().textes
        debut, fin = textes.fenetre_debut, textes.fenetre_fin
    except DeclarationAbsente:
        debut, fin = DEBUT_NEUTRE, FIN_NEUTRE
    return debut + "\n\n" + '\n'.join(visible(lines_of(line)) for line in lines) + "\n\n" + fin


def columns(line):
    """Width of a line in plain characters: a wide or full-width character (Ｗ, 漢) takes two."""
    return sum(2 if unicodedata.east_asian_width(c) in ('W', 'F') else 1 for c in line)


def rows(shown):
    return sum(max(1, -(-columns(line) // ROW_CHARS)) for line in shown.split('\n'))


def check(lines):
    """The dialog text, or TooLong when it would not fit whole on the screen."""
    shown = text(lines)
    reason = next((r for r in map(blurs, lines) if r), None)
    if reason:
        # Text already in a base or a calendar (a name, a title) can hold it too, not only the model's arguments.
        raise Blurred(f"Le récapitulatif contient du texte que la fenêtre du Mac ne montrerait pas fidèlement ({reason}) : "
                      "rien n'est enregistré. Corrige ce texte à la main, ou retire l'étape qui le contient.")
    needed = rows(shown)
    if needed > MAX_ROWS:
        raise TooLong(f"Le récapitulatif ne tient pas en entier dans la fenêtre du Mac ({needed} lignes, au plus {MAX_ROWS}) : "
                      "rien n'est enregistré. Retire des étapes du lot et enregistre en plusieurs fois.")
    return shown


# Every Drive the Mac keeps in sync: fictitious data is never written there without the window.
CLOUD = Path.home() / 'Library' / 'CloudStorage'


def _identities(path):
    """(device, inode) of the path and of each existing folder above it, links followed. The same folder written with
    another case (APFS ignores case), through a link or through /System/Volumes/Data has the same identity."""
    found = set()
    for folder in (path, *path.parents):
        try:
            info = os.stat(folder)
        except OSError:
            continue
        found.add((info.st_dev, info.st_ino))
    return found


def _inside(path, folder):
    path = Path(os.path.abspath(Path(path).expanduser()))
    try:
        info = os.stat(folder)
    except OSError:
        # A real folder that is gone (a former home): compare the text, case folded.
        return str(path).casefold().startswith(str(Path(folder).resolve()).casefold().rstrip('/') + '/') \
            or str(path).casefold() == str(Path(folder).resolve()).casefold()
    return (info.st_dev, info.st_ino) in _identities(path)


def fictitious():
    """True only when an agent is declared, every base and its data folder are away from every folder it declares real, and
    its outputs are away from every synced Drive (CLOUD). Folders are compared by identity, not by how their path is written.
    Any doubt, no declaration included, is False."""
    try:
        agent = actuelle()
        if any(_inside(path, folder) for path in (agent.dossier_donnees(), *agent.bases()) for folder in agent.dossiers_reels):
            return False
        from socle.installation import est_reel  # a folder marked real by an installer is never fictitious (spec 017)
        if any(est_reel(path) for path in (agent.dossier_donnees(), *agent.bases())):
            return False
        return not any(_inside(path, CLOUD) for path in agent.dossiers_sortie())
    except Exception:
        return False


def ask(title, lines):
    """True only when the owner clicked « Enregistrer » on the Mac; TooLong before any window when the summary would not fit."""
    return dialog(title, check(lines), SCRIPT, 'Enregistrer')


def dialog(title, shown, script, button):
    if AUTOMATIC and fictitious():
        return True
    try:
        done = subprocess.run([OSASCRIPT, '-e', script, title, shown, str(SECONDS)],
                              capture_output=True, text=True, timeout=SECONDS + 15)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return done.returncode == 0 and done.stdout.strip() == button
