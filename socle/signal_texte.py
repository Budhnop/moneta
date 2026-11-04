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
import stat
import time

from socle.chemins import private_folder, write_private
from socle import installation
from socle.declaration import DeclarationAbsente, actuelle

AGE_SIGNAL = 30 * 60  # same as a step's life in a batch (STEP_SECONDS, D052)
AVANCE_MAX = 120      # seconds a time may run ahead of this Mac's clock before it is a doubt
NOM = 'texte-autrui.json'
VERROU = 'texte-autrui.verrou'
VERROU_SECONDES = 15  # how long a writer of the signal or a write on the go waits for the other (F7, F8)
CLES = {'version', 'dernier_rendu', 'par'}
HEURE = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$')


def chemin():
    """The shared file in the ecosystem's root for an enrolled agent on its real data, else inside its own data folder
    (socle.installation.dossier_commun, spec 017). No declared agent, or a refused state, raises an OSError: the caller has
    a doubt. SOCLE_SIGNAL_TEXTE is for tests: honoured only on fictitious data, so a variable left in a terminal never gives
    a real program another signal than the others' (review 006, P2-1)."""
    from socle.confirmation import fictitious  # lazily: confirmation never imports this module
    if os.environ.get('SOCLE_SIGNAL_TEXTE') and fictitious():
        return Path(os.environ['SOCLE_SIGNAL_TEXTE']).expanduser()
    return installation.dossier_commun('signaux') / NOM


def _ecrire(contenu):
    fichier = chemin()
    if fichier.parent.is_symlink():
        raise OSError(f'Dossier du signal remplacé par un lien : {fichier.parent}')
    private_folder(fichier.parent)
    return write_private(fichier.parent, fichier.name, json.dumps(contenu, ensure_ascii=False).encode('utf-8'))


class Doute(OSError):
    """The existing signal or lock cannot be used without following a link."""


class Occupe(Exception):
    """The lock stayed taken longer than VERROU_SECONDES."""


@contextmanager
def verrou(existant=False):
    """One lock, next to the signal, for every process (F7, F8): each writer of the signal takes it, and so does a write
    on the go from its decision to its end. No text from someone else is shown, and no other decision reads the day's
    money, between a decision and its write. Raises Occupe after VERROU_SECONDES; never re-entered by one process."""
    if existant:
        with _verrou_existant() as dossier:
            yield dossier
        return
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


@contextmanager
def _dossier_existant():
    try:
        fd = os.open(chemin().parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as exc:
        raise Doute('Dossier du signal inaccessible.') from exc
    try:
        yield fd
    finally:
        os.close(fd)


def _verifier_dossier(fd):
    ouvert = os.fstat(fd)
    actuel = os.stat(chemin().parent, follow_symlinks=False)
    if not stat.S_ISDIR(actuel.st_mode) or (ouvert.st_dev, ouvert.st_ino) != (actuel.st_dev, actuel.st_ino):
        raise Doute('Dossier du signal remplacé.')


def _ouvrir_existant(dossier, nom, mode):
    fd = os.open(nom, mode | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dossier)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise Doute('Le signal et son verrou doivent être des fichiers ordinaires.')
    return fd


@contextmanager
def _verrou_existant():
    """The versioned path never creates a lock; checks its identity after waiting."""
    with _dossier_existant() as dossier:
        fd = _ouvrir_existant(dossier, VERROU, os.O_RDWR)
        try:
            fin = time.monotonic() + VERROU_SECONDES
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    restant = fin - time.monotonic()
                    if restant <= 0:
                        raise Occupe('Le verrou du signal est occupé.') from None
                    time.sleep(min(0.05, restant))
            _verifier_dossier(dossier)
            ouvert = os.fstat(fd)
            actuel = os.stat(VERROU, dir_fd=dossier, follow_symlinks=False)
            if not stat.S_ISREG(actuel.st_mode) or (ouvert.st_dev, ouvert.st_ino) != (actuel.st_dev, actuel.st_ino):
                raise Doute('Verrou du signal remplacé.')
            yield dossier
        finally:
            os.close(fd)


def inscrire(par=None, maintenant=None):
    """Writes the time now (UTC, to the second), under the lock. par: the declared agent, by default and only (R4, spec 017:
    an agent never writes in another's name, even an enrolled one). Any failure raises: the caller then shows nothing."""
    nom = actuelle().nom
    par = nom if par is None else par
    if par != nom or not installation.ecrivain_connu(par):
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
    return _etat_et_heure(maintenant, strict=False)[0]


def etat_et_heure(maintenant=None):
    """State and validated timestamp from one read, without following the signal's links."""
    return _etat_et_heure(maintenant, strict=True)


def _etat_et_heure(maintenant, strict):
    """aucun (nothing shown yet), ancien, recent (under AGE_SIGNAL), or douteux (anything not exactly as written)."""
    try:
        if strict:
            with _dossier_existant() as dossier:
                fd = _ouvrir_existant(dossier, chemin().name, os.O_RDONLY)
                with os.fdopen(fd, encoding='utf-8') as fichier:
                    contenu = json.load(fichier)
                _verifier_dossier(dossier)
        else:
            contenu = json.loads(chemin().read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return 'douteux', None
    if not isinstance(contenu, dict) or set(contenu) != CLES or type(contenu['version']) is not int \
            or contenu['version'] != 1 or not installation.ecrivain_connu(contenu['par']):
        return 'douteux', None
    rendu = contenu['dernier_rendu']
    if rendu is None:
        return 'aucun', None
    if not isinstance(rendu, str) or not HEURE.fullmatch(rendu):
        return 'douteux', None
    try:
        moment = datetime.strptime(rendu, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
    except ValueError:
        return 'douteux', None
    age = ((maintenant or datetime.now(timezone.utc)).astimezone(timezone.utc) - moment).total_seconds()
    if age < -AVANCE_MAX:
        return 'douteux', None
    return ('recent' if age < AGE_SIGNAL else 'ancien'), moment
