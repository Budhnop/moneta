"""Les paiements de Dojo pour Moneta (spec 013, contrat lecture-paiements-moneta) : la seule porte vers ces données.

Moneta n'ouvre jamais dojo.db : il lance le programme fermé de Smith, bin/smith-lecture-paiements, par son chemin fixe et sans
shell, et contrôle sa réponse au champ près. Les mêmes tuples que totaux_annee et lignes_annee de la spec 011, pour que le code de
Finances ne change pas. Tout écart, code non nul ou délai dépassé : ToolError, jamais un zéro, jamais une liste partielle.
MONETA_LECTURE_PAIEMENTS désigne un autre programme sur des données fictives seulement (tests).
"""
from datetime import date
import json
import os
from pathlib import Path

from socle import confirmation
from socle.executeur import ExecutionError, Executeur
from socle.lots import ToolError

PROGRAMME = Path.home() / '_PersonalAgents' / 'smith' / 'bin' / 'smith-lecture-paiements'
DELAI = 20  # les 15 secondes de Smith, et le démarrage du programme
VERSION = 1
MODES = ('comptant', 'virement', 'non_precise')
MAX_SORTIE = 2_000_000
ILLISIBLES = 'Paiements du Dojo illisibles'


def _verifier(annee):
    if type(annee) is not int or not 2000 <= annee <= 2100:
        raise ValueError('Année invalide.')


def programme():
    """Le programme de Smith, sauf sur des données fictives : une variable laissée dans un terminal ne le remplace jamais."""
    autre = os.environ.get('MONETA_LECTURE_PAIEMENTS')
    return Path(autre) if autre and confirmation.fictitious() else PROGRAMME


def lancer(commande, annee):
    try:
        return Executeur.lancer('lecture_paiements', [str(programme()), commande, str(annee)], DELAI)
    except ExecutionError:
        # Le message de Smith reste chez Smith : rien de ce qu'il dit ne passe pour une donnée.
        raise ToolError(f'{ILLISIBLES} : le programme de Dojo a refusé ou n\'a pas répondu.') from None


def _entier(valeur):
    return type(valeur) is int and valeur >= 0


def _jour(valeur, annee):
    if type(valeur) is not str or len(valeur) != 10 or valeur[4] != '-' or valeur[7] != '-':
        return False
    try:
        return date.fromisoformat(valeur).year == annee
    except ValueError:
        return False


def lire(commande, annee, cle):
    _verifier(annee)
    sortie = lancer(commande, annee)
    if len(sortie) > MAX_SORTIE or '\n' in sortie:
        raise ToolError(f'{ILLISIBLES} : réponse hors contrat.')
    try:
        valeur = json.loads(sortie)
    except ValueError:
        raise ToolError(f'{ILLISIBLES} : réponse hors contrat.') from None
    if not isinstance(valeur, dict) or set(valeur) != {'version', 'commande', 'annee', *cle} \
            or type(valeur['version']) is not int or valeur['version'] != VERSION or valeur['commande'] != commande \
            or type(valeur['annee']) is not int or valeur['annee'] != annee:
        raise ToolError(f'{ILLISIBLES} : réponse hors contrat.')
    return valeur


def totaux_annee(annee):
    """Montants et nombres de l'année par mode, comptant inclus, en cents et en nombres entiers."""
    valeur = lire('totaux', annee, ('montants', 'nombres'))
    for cle in ('montants', 'nombres'):
        partie = valeur[cle]
        if not isinstance(partie, dict) or set(partie) != set(MODES) or not all(_entier(partie[m]) for m in MODES):
            raise ToolError(f'{ILLISIBLES} : réponse hors contrat.')
    return ({m: valeur['montants'][m] for m in MODES}, {m: valeur['nombres'][m] for m in MODES})


def lignes_annee(annee):
    """Date, nom de famille et cents des paiements de l'année sans le comptant, dans l'ordre rendu par Dojo."""
    lignes = lire('lignes', annee, ('lignes',))['lignes']
    if not isinstance(lignes, list):
        raise ToolError(f'{ILLISIBLES} : réponse hors contrat.')
    rendues = []
    for ligne in lignes:
        if not isinstance(ligne, list) or len(ligne) != 3 or not _jour(ligne[0], annee) \
                or not (ligne[1] is None or type(ligne[1]) is str) or not _entier(ligne[2]):
            raise ToolError(f'{ILLISIBLES} : réponse hors contrat.')
        rendues.append(tuple(ligne))
    return rendues
