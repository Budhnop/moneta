"""The journal of writes (spec 002, E5, E6, contract journal-ecritures): one JSON line per event, one file per local month.

Every WR_SAVE_* that writes logs an intention before any window or write (no journal, no write), with the money its
batch counts (D090: the policy's daily cap reads it back, go_du_jour), then its result. A
result that cannot be logged never undoes the write: the tool's answer carries AVERTISSEMENT instead. Steps hold the
lines the window would show, never someone else's text: only plain str values pass (a marked value, whatever its form,
is refused). JournalIndisponible carries the refusal; the tools turn it into their ToolError. Without a declared agent nothing is written.

One journal for every agent of an ecosystem (spec 013, D110; spec 017, D112): the ecosystem's root for an enrolled agent on
its real data (the signal's rule), else inside its data folder. Each line names its agent; a line without one, from before
D110, reads as « inconnu » (revue 017, P07 : the socle names no agent).

A write cut short leaves a last line without its end. The next append first closes it with a « coupure » line (revue 017,
S08): the cut line never merges with the next one, and the daily cap tolerates a broken line that a coupure follows (the
write it announced never happened: no journal, no write).
"""
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import re
import secrets

from socle import installation
from socle.chemins import private_folder
from socle.declaration import DeclarationAbsente, actuelle

DOSSIER = 'journal-ecritures'
MODES = ('go', 'fenetre')
# Where the write was asked (D098, spec 008): the app's instance, or Claude Desktop; older lines read as desktop.
ORIGINES = ('desktop', 'app')
ETATS = ('enregistre', 'refuse', 'non_confirme', 'inconnu')
CLES_ETAPE = {'numero', 'operation', 'resume'}
DETAIL_MAX = 200
INDISPONIBLE = "Journal des écritures indisponible; rien n'est enregistré."
AVERTISSEMENT = "Écrit, mais le résultat n'a pas pu être ajouté au journal."
IDENTIFIANT = re.compile(r'^[0-9a-f]{8}$')
# D110 : les lignes d'avant le journal commun n'ont pas d'« agent ». Le socle ne nomme aucun agent (revue 017, P07).
AGENT_SANS_NOM = 'inconnu'
COUPURE = 'coupure'


class JournalIndisponible(Exception):
    def __init__(self):
        super().__init__(INDISPONIBLE)


def dossier():
    """The shared journal in the ecosystem's root for an enrolled agent on its real data, else inside its data folder."""
    return installation.dossier_commun(DOSSIER)


def _texte(valeur):
    # Exactly str: a subclass may be a mark of someone else's text.
    return type(valeur) is str


def _ajouter(ligne, maintenant):
    """Appends one line to the month's file, never through a link, and syncs it to the disk."""
    folder = dossier()
    if folder.is_symlink():
        raise OSError(f'Dossier du journal remplacé par un lien : {folder}')
    private_folder(folder)
    held = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fd = os.open(f'{maintenant:%Y-%m}.jsonl', os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=held)
        try:
            texte = (json.dumps(ligne, ensure_ascii=False) + '\n').encode('utf-8')
            taille = os.fstat(fd).st_size
            if taille and os.pread(fd, 1, taille - 1) != b'\n':
                coupure = {'type': COUPURE, 'heure': maintenant.strftime('%Y-%m-%dT%H:%M:%S')}
                texte = b'\n' + json.dumps(coupure).encode('utf-8') + b'\n' + texte
            os.write(fd, texte)
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        os.close(held)


def intention(outil, lot, decision, lignes, maintenant=None, origine='desktop', profil=None):
    """Logged before the window and the write; returns the id its result will carry. Any doubt refuses the write."""
    try:
        agent = actuelle()
        regles = agent.regles
    except DeclarationAbsente:
        raise JournalIndisponible() from None
    etapes, mode, raisons = list(lignes), getattr(decision, 'mode', None), getattr(decision, 'raisons', None)
    montant = getattr(decision, 'montant', None)
    if type(montant) is not Decimal or not montant.is_finite() or montant < 0 \
            or not (_texte(outil) and (outil.startswith('WR_SAVE_') or outil in regles.hors_wr or (profil == 'versionne' and outil in regles.versionnees))) or not (lot is None or lot in regles.lots) or mode not in MODES \
            or not (_texte(origine) and origine in ORIGINES) \
            or not isinstance(raisons, (list, tuple)) or not all(_texte(r) for r in raisons) \
            or not all(isinstance(e, dict) and set(e) == CLES_ETAPE and type(e['numero']) is int
                       and _texte(e['operation']) and _texte(e['resume']) for e in etapes):
        raise JournalIndisponible()
    moment = maintenant or datetime.now()
    ident = secrets.token_hex(4)
    try:
        _ajouter({'id': ident, 'heure': moment.strftime('%Y-%m-%dT%H:%M:%S'), 'type': 'intention', 'agent': agent.nom, 'outil': outil,
                  'lot': lot, 'mode': mode, 'raisons': list(raisons), 'etapes': etapes, 'montant': f'{montant:.2f}',
                  'origine': origine, **({'profil': profil} if profil == 'versionne' else {})}, moment)
    except OSError:
        raise JournalIndisponible() from None
    return ident


def resultat(ident, etat, detail=None, maintenant=None):
    """Logged after the write; None when logged, else AVERTISSEMENT (never an exception: the write already happened)."""
    try:
        agent = actuelle().nom
    except DeclarationAbsente:
        return AVERTISSEMENT
    if not (_texte(ident) and IDENTIFIANT.match(ident)) or etat not in ETATS or not (detail is None or _texte(detail)):
        return AVERTISSEMENT
    moment = maintenant or datetime.now()
    try:
        _ajouter({'id': ident, 'heure': moment.strftime('%Y-%m-%dT%H:%M:%S'), 'type': 'resultat', 'agent': agent, 'resultat': etat,
                  'detail': detail[:DETAIL_MAX] if detail else None}, moment)
    except Exception:
        return AVERTISSEMENT
    return None


def _lire(mois, strict=False):
    """The month's lines. For display a line that cannot be read is skipped; for the daily cap
    (strict, F9) anything but a missing file or a last line cut by an interrupted write raises: the policy has a doubt."""
    try:
        texte = (dossier() / f'{mois}.jsonl').read_text(encoding='utf-8')
    except FileNotFoundError:
        return []
    except OSError:
        if strict:
            raise
        return []
    bruts = texte.splitlines()
    lignes = []
    for numero, brut in enumerate(bruts, 1):
        if not brut.strip():
            continue
        try:
            ligne = json.loads(brut)
        except ValueError:
            coupee = (numero == len(bruts) and not texte.endswith('\n')) or _est_coupure(bruts[numero] if numero < len(bruts) else '')
            if strict and not coupee:
                raise
            continue  # a line cut by an interrupted write
        if isinstance(ligne, dict) and ligne.get('type') == COUPURE and set(ligne) == {'type', 'heure'}:
            continue
        if isinstance(ligne, dict) and ligne.get('type') in ('intention', 'resultat') and _texte(ligne.get('id')):
            lignes.append(ligne)
        elif strict:
            raise ValueError('ligne inconnue au journal')
    return lignes


def _est_coupure(brut):
    try:
        ligne = json.loads(brut)
    except ValueError:
        return False
    return isinstance(ligne, dict) and ligne.get('type') == COUPURE and set(ligne) == {'type', 'heure'}


def du_jour(jour, strict=False):
    """The day's writes in order, each with its last result; an intention without one is inconnu (process stopped)."""
    suivant = date(jour.year + jour.month // 12, jour.month % 12 + 1, 1)
    lignes = _lire(f'{jour:%Y-%m}', strict) + _lire(f'{suivant:%Y-%m}', strict)  # a result may land just after midnight
    # Two agents could draw the same id: a result answers the intention of its own agent.
    resultats = {(l.get('agent', AGENT_SANS_NOM), l['id']): l for l in lignes if l['type'] == 'resultat'}
    jour_texte = jour.isoformat()
    ecritures = []
    for ligne in lignes:
        if ligne['type'] != 'intention' or not str(ligne.get('heure', '')).startswith(jour_texte):
            continue
        agent = ligne.get('agent', AGENT_SANS_NOM)
        fin = resultats.get((agent, ligne['id']), {})
        ecritures.append({**{k: ligne.get(k) for k in ('id', 'heure', 'outil', 'lot', 'mode', 'raisons', 'etapes', 'montant')},
                          'agent': agent if type(agent) is str else AGENT_SANS_NOM,
                          'origine': ligne['origine'] if ligne.get('origine') in ORIGINES else 'desktop',
                          'resultat': fin.get('resultat', 'inconnu'), 'detail': fin.get('detail')})
    return ecritures


def go_du_jour(jour):
    """Money written on the go that day (enregistre, or inconnu: the write may have happened). An unknown mode,
    an unreadable amount, or a journal that cannot be read (F9), raises: the policy then has a doubt."""
    total = Decimal('0')
    for ecriture in du_jour(jour, strict=True):
        if ecriture['mode'] not in MODES:
            raise ValueError('mode inconnu au journal')
        if ecriture['mode'] != 'go' or ecriture['resultat'] not in ('enregistre', 'inconnu'):
            continue
        try:
            montant = Decimal(ecriture['montant']) if type(ecriture['montant']) is str else None
        except InvalidOperation:
            montant = None
        if montant is None or not montant.is_finite() or montant < 0:
            raise ValueError('montant illisible au journal')
        total += montant
    return total
