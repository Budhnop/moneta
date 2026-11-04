"""Calendars, lists and addresses the owner trusts (spec 002, E9, E14, R6, R8): <data folder>/config/confiance.json.

Written by hand by the owner; the model has no tool to change it. Absent, unreadable or not exactly as described, nothing is
private and no address is the owner's: everything is shared, everything is retained, the window opens. The app Calendrier
(version 7) reads the same file itself. A calendar or a list is private only when listed, found once and named in a
single script (the checks of may_write, D053): a renamed, new or doubled one is shared.
The policy reads this module. The key of the addresses comes from the declaration: the file keeps its name.
"""
from dataclasses import dataclass
import json
from pathlib import Path

from socle.declaration import DeclarationAbsente, actuelle
from socle.externe import mixed_scripts

CHAMPS = ('calendriers_prives', 'listes_privees', 'adresses_proprietaire')
TEXTE_MAX = 80
ELEMENTS_MAX = 20


@dataclass(frozen=True)
class Configuration:
    calendriers_prives: tuple = ()
    listes_privees: tuple = ()
    adresses_proprietaire: tuple = ()
    configuree: bool = False
    # Names dropped for mixing scripts, for the agent's checker to name.
    ignores: tuple = ()


def chemin():
    return actuelle().dossier_donnees() / 'config' / 'confiance.json'


def charger():
    try:
        CLES = ('calendriers_prives', 'listes_privees', actuelle().textes.cle_adresses)
        contenu = json.loads(Path(chemin()).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return Configuration()
    if not isinstance(contenu, dict) or set(contenu) != {'version', *CLES} or type(contenu['version']) is not int \
            or contenu['version'] != 1:
        return Configuration()
    listes = {}
    for cle in CLES:
        valeur = contenu[cle]
        if not isinstance(valeur, list) or len(valeur) > ELEMENTS_MAX \
                or not all(isinstance(v, str) and len(v) <= TEXTE_MAX for v in valeur):
            return Configuration()
        listes[cle] = valeur
    ignores = tuple(v for cle in CLES for v in listes[cle] if mixed_scripts(v))
    garde = {champ: tuple(v for v in listes[cle] if not mixed_scripts(v)) for champ, cle in zip(CHAMPS, CLES)}
    return Configuration(**garde, configuree=True, ignores=ignores)


def _prive(nom, trouves, liste):
    # As may_write: listed (without case), a single script, found exactly once among what the calendar holds.
    cle = nom.casefold()
    return (cle in {n.casefold() for n in liste} and not mixed_scripts(nom)
            and sum(t.casefold() == cle for t in trouves) == 1)


def calendrier_prive(etiquette, etiquettes, config=None):
    """etiquette: « compte / nom »; etiquettes: every calendar found, for the uniqueness check."""
    return _prive(etiquette, etiquettes, (config or charger()).calendriers_prives)


def liste_privee(nom, noms, config=None):
    """nom: a reminder list's name; noms: every list found."""
    return _prive(nom, noms, (config or charger()).listes_privees)


def adresse_proprietaire(adresse, config=None):
    """The organiser's address (EventKit gives mailto:…), compared without case; never a display name."""
    if not isinstance(adresse, str) or not adresse:
        return False
    adresse = adresse[len('mailto:'):] if adresse.casefold().startswith('mailto:') else adresse
    return adresse.casefold() in {a.casefold() for a in (config or charger()).adresses_proprietaire}


def _raison_adresse():
    try:
        return actuelle().textes.raison_adresse
    except DeclarationAbsente:
        return 'adresse'


def juger(evenement, prive, config=None):
    """(trusted, reason) for one event read from the app (version 7). prive: calendrier_prive of its calendar.

    Retained (False) at the least doubt: a guest, someone else as organiser, no organiser outside a private calendar
    (in a shared iCloud calendar, another person's event often has none), or an answer missing a field (older app)."""
    moi, adresse, invites = (evenement.get(k) for k in ('organisateur_moi', 'organisateur_adresse', 'nombre_invites'))
    if not all(k in evenement for k in ('organisateur_moi', 'organisateur_adresse', 'nombre_invites')) \
            or type(moi) is not bool or type(invites) is not int or invites < 0 \
            or not (adresse is None or isinstance(adresse, str)):
        return False, 'reponse_incomplete'
    if invites:
        return False, 'invites'
    if moi:
        return True, 'organisateur_moi'
    if adresse:
        return (True, _raison_adresse()) if adresse_proprietaire(adresse, config) else (False, 'organisateur')
    return (True, 'calendrier_prive') if prive else (False, 'organisateur_absent')
