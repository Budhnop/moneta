"""The signal « someone else's text shown recently » (spec 002, E13, contract signal-texte-autrui).

One small JSON file, shared by every agent: the UTC time of the last result that carried someone else's text.
A server writes it before showing such a result; the confirmation policy reads it at every decision. Absent, unreadable,
badly formed or too far in the future, the file is a doubt, never a quiet « nothing shown ». The initial file (no time
yet) is made by the agent's checker only (creer_initial): a deleted file stays a doubt for the server.
The policy reads this module, and it must stay free of the tools it judges.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import time

from socle.chemins import private_folder, write_private
from socle.declaration import PAR, DeclarationAbsente, actuelle

AGE_SIGNAL = 30 * 60  # same as a step's life in a batch (STEP_SECONDS, D052)
AVANCE_MAX = 120      # seconds a time may run ahead of this Mac's clock before it is a doubt
NOM = 'texte-autrui.json'
VERROU = 'texte-autrui.verrou'
VERROU_SECONDES = 15  # how long a writer of the signal or a write on the go waits for the other (F7, F8)
CLES = {'version', 'dernier_rendu', 'par'}
HEURE = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$')


def chemin():
    """The shared real file when the agent's data folder is in one of its real folders, compared by identity, not by how its
    path is written, or when no agent is declared (EF-008); else inside its data folder. UGOD_SIGNAL_TEXTE is for tests:
    honoured only on fictitious data, so a variable left in a terminal never gives a real program another signal than the
    others' (review 006, P2-1)."""
    from socle.confirmation import _inside, fictitious  # lazily: confirmation never imports this module
    if os.environ.get('UGOD_SIGNAL_TEXTE') and fictitious():
        return Path(os.environ['UGOD_SIGNAL_TEXTE']).expanduser()
    partage = Path.home() / '_PersonalAgents' / 'donnees' / 'signaux' / NOM
    try:
        agent = actuelle()
    except DeclarationAbsente:
        return partage
    donnees = Path(agent.dossier_donnees())
    if any(donnees == Path(reel) or _inside(donnees, reel) for reel in agent.dossiers_reels):
        return partage
    return donnees / 'signaux' / NOM


def _ecrire(contenu):
    fichier = chemin()
    if fichier.parent.is_symlink():
        raise OSError(f'Dossier du signal remplacé par un lien : {fichier.parent}')
    private_folder(fichier.parent)
    return write_private(fichier.parent, fichier.name, json.dumps(contenu, ensure_ascii=False).encode('utf-8'))


class Occupe(Exception):
    """The lock stayed taken longer than VERROU_SECONDES."""


@contextmanager
def verrou():
    """One lock, next to the signal, for every process (F7, F8): each writer of the signal takes it, and so does a write
    on the go from its decision to its end. No text from someone else is shown, and no other decision reads the day's
    money, between a decision and its write. Raises Occupe after VERROU_SECONDES; never re-entered by one process."""
    dossier = chemin().parent
    if dossier.is_symlink():
        raise OSError(f'Dossier du signal remplacé par un lien : {dossier}')
    private_folder(dossier)
    fd = os.open(dossier / VERROU, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fin = time.monotonic() + VERROU_SECONDES
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= fin:
                    raise Occupe() from None
                time.sleep(0.05)
        yield
    finally:
        os.close(fd)  # closing releases the lock


def inscrire(par=None, maintenant=None):
    """Writes the time now (UTC, to the second), under the lock. par: the declared agent by default. Any failure raises: the caller then shows nothing."""
    par = actuelle().nom if par is None else par
    if par not in PAR:
        raise ValueError(f'Écrivain du signal inconnu : {par}')
    moment = (maintenant or datetime.now(timezone.utc)).astimezone(timezone.utc)
    with verrou():
        return _ecrire({'version': 1, 'dernier_rendu': moment.strftime('%Y-%m-%dT%H:%M:%SZ'), 'par': par})


def creer_initial():
    """The agent's checker only: the file with no time yet, never over an existing one (or a link)."""
    fichier = chemin()
    if fichier.exists() or fichier.is_symlink():
        return fichier
    return _ecrire({'version': 1, 'dernier_rendu': None, 'par': actuelle().nom})


def etat(maintenant=None):
    """aucun (nothing shown yet), ancien, recent (under AGE_SIGNAL), or douteux (anything not exactly as written)."""
    try:
        contenu = json.loads(chemin().read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return 'douteux'
    if not isinstance(contenu, dict) or set(contenu) != CLES or type(contenu['version']) is not int \
            or contenu['version'] != 1 or contenu['par'] not in PAR:
        return 'douteux'
    rendu = contenu['dernier_rendu']
    if rendu is None:
        return 'aucun'
    if not isinstance(rendu, str) or not HEURE.match(rendu):
        return 'douteux'
    try:
        moment = datetime.strptime(rendu, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
    except ValueError:
        return 'douteux'
    age = ((maintenant or datetime.now(timezone.utc)).astimezone(timezone.utc) - moment).total_seconds()
    if age < -AVANCE_MAX:
        return 'douteux'
    return 'recent' if age < AGE_SIGNAL else 'ancien'
