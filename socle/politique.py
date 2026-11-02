"""Go in the chat or the Mac's window, for one batch (spec 002, E1 to E4, E14, contract politique-confirmation).

decider judges the frozen steps of a batch, never the model's arguments: each step comes from its batch as
{outil, operation, resume}, outil being the preparation's name in the agent's registry. The rules are the agent's
declaration (regles); the course of the decision is here. Every rule is checked and one is enough for the window; the reasons
come back in display order. A step may carry signal: the signal's state when it joined its batch (recent or douteux; None when
old or absent), so a step added beside someone else's text keeps the window after the 30 minutes (D090). Money written on the go
today, read in the journal, counts with the batch's own (D090). What only the agent can judge goes through regles.juges[lot](steps,
version_app), which returns a set of reasons. A reason outside the agent's table is a doubt, never a silent go.
No exception ever leaves: any doubt is the window. No declaration is a doubt too.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import math

from socle import journal_ecritures, signal_texte
from socle.declaration import DeclarationAbsente, actuelle

RAISONS_NEUTRES = {
    'texte_autrui_recent': "Du texte écrit par d'autres a été lu il y a moins de 30 minutes.",
    'signal_douteux': "Le serveur ne sait pas si du texte d'autrui a été lu récemment.",
    'hors_liste': 'Écriture hors de la liste des écritures locales.',
    'plafond_montant': 'Plafond de ce lot dépassé.',
    'plafond_jour': "Plafond du jour dépassé.",
    'plafond_etapes': 'Trop d\'étapes dans ce lot.',
    'doute': 'Le serveur a un doute.',
}


@dataclass(frozen=True)
class Decision:
    mode: str
    raisons: tuple
    montant: Decimal
    etapes: int


def _table():
    try:
        return actuelle().regles.raisons
    except DeclarationAbsente:
        return RAISONS_NEUTRES


def libelles(decision):
    table = _table()
    return [table.get(r, r) for r in decision.raisons]


# The signal's state when a step joined its batch, as the step keeps it (D090).
SIGNAL_ETAPE = {None: set(), 'recent': {'texte_autrui_recent'}, 'douteux': {'signal_douteux'}}


def signal_ajout(maintenant=None):
    """What a step keeps of the signal when it joins a batch: recent, douteux, or None."""
    try:
        etat = signal_texte.etat(maintenant)
    except Exception:
        return 'douteux'
    return etat if etat in ('recent', 'douteux') else None


def _signal(maintenant):
    etat = signal_texte.etat(maintenant)
    return {'recent': {'texte_autrui_recent'}, 'douteux': {'signal_douteux'}}.get(etat, set())


def _montant(resume, champ='montant'):
    valeur = resume.get(champ)
    if type(valeur) not in (int, float) or not math.isfinite(valeur):
        raise ValueError('montant illisible')
    return abs(Decimal(str(valeur)))


def _juger(lot, etapes, maintenant, version_app):
    agent = actuelle()
    regles = agent.regles
    raisons = _signal(maintenant)
    if not isinstance(etapes, list):
        raise TypeError('étapes')
    if lot not in regles.lots:
        return raisons | {'hors_liste'}, Decimal('0')
    if not etapes:
        raisons.add('doute')
    montant = Decimal('0')
    pour_le_juge = []
    for etape in etapes:
        if not isinstance(etape, dict) or not {'operation', 'resume'} <= set(etape) or not isinstance(etape['resume'], dict):
            raisons.add('doute')
            continue
        outil = agent.outil(etape.get('outil'))
        if outil is None or outil.type != 'preparation' or outil.lot != lot:
            raisons.add('hors_liste')
            continue
        if outil.confirmation != 'go':
            raisons.add(regles.raison_fenetre.get(etape.get('outil'), 'suppression'))
        signal = etape.get('signal')
        raisons |= SIGNAL_ETAPE[signal] if type(signal) in (str, type(None)) and signal in SIGNAL_ETAPE else {'doute'}
        champ = regles.avec_montant.get(lot, {}).get(etape['operation'])
        if champ and not ((lot, etape['operation']) in regles.facultatif and champ not in etape['resume']):
            try:
                montant += _montant(etape['resume'], champ)
            except (ValueError, InvalidOperation):
                raisons.add('doute')
        pour_le_juge.append(etape)
    juge = regles.juges.get(lot)
    if juge is not None:
        raisons |= set(juge(pour_le_juge, version_app))
    if montant > regles.montant_max:
        raisons.add('plafond_montant')
    if montant > 0:
        # Money already written on the go today (local day, journal; enregistre or inconnu): the cap holds for the day too.
        jour = (maintenant or datetime.now(timezone.utc)).astimezone().date()
        avant = journal_ecritures.go_du_jour(jour)
        if avant > 0 and avant + montant > regles.montant_max:
            raisons.add('plafond_jour')
    if len(etapes) > regles.etapes_max:
        raisons.add('plafond_etapes')
    return raisons, montant


def _ordre(raisons):
    """The reasons in display order; one the table does not know becomes a doubt, so it can never vanish into a go."""
    table = actuelle().regles.raisons
    raisons = set(raisons)
    if raisons - set(table):
        raisons = (raisons & set(table)) | {'doute'}
    return tuple(r for r in table if r in raisons)


def plus_stricte(avant, maintenant):
    """The decision at the commit, never softer than the summary's (avant): every reason of both, in display order; the
    amount and steps are the commit's. No summary decision: the commit's; anything else, or no declaration: a doubt."""
    if avant is None:
        return maintenant
    if not isinstance(avant, Decision) or not isinstance(maintenant, Decision):
        return Decision('fenetre', ('doute',), Decimal('0'), getattr(maintenant, 'etapes', 0))
    try:
        raisons = _ordre(set(avant.raisons) | set(maintenant.raisons))
    except Exception:
        return Decision('fenetre', ('doute',), maintenant.montant, maintenant.etapes)
    if not raisons and 'fenetre' in (avant.mode, maintenant.mode):
        raisons = ('doute',)  # a window already decided never turns into a go, whatever the table says now
    return Decision('fenetre' if raisons else 'go', raisons, maintenant.montant, maintenant.etapes)


def decider(lot, etapes, *, maintenant=None, version_app=None):
    """The decision for these frozen steps; reasons empty if and only if the mode is go. Never raises."""
    try:
        raisons, montant = _juger(lot, etapes, maintenant, version_app)
        ordre = _ordre(raisons)
        return Decision('fenetre' if ordre else 'go', ordre, montant, len(etapes))
    except Exception:
        try:
            nombre = len(etapes)
        except TypeError:
            nombre = 0
        return Decision('fenetre', ('doute',), Decimal('0'), nombre)
