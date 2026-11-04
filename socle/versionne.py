"""Cadre des écritures versionnées : intention avant, métadonnées seules, résultat après."""
from contextlib import contextmanager
from datetime import timedelta
from decimal import Decimal
import re
from types import SimpleNamespace

from socle import journal_ecritures, signal_texte
from socle.declaration import actuelle
from socle.lots import ToolError

ULID = re.compile(r'[0-9A-HJKMNP-TV-Z]{26}')


ENTIER_MAX = 2 ** 63 - 1


def _objet(valeur):
    # Revue 015, R11 : un entier borné, comme un identifiant SQLite; jamais un grand nombre qui porterait du texte.
    return (type(valeur) is int and 0 < valeur <= ENTIER_MAX) or \
        (type(valeur) is str and (valeur == 'nouveau' or ULID.fullmatch(valeur) is not None))


def intention(outil, objet='nouveau'):
    """Refuse tout doute avant d'appeler l'outil, sans jamais journaliser son texte."""
    try:
        agent = actuelle()
        info = agent.outil(outil)
        if not _objet(objet) or outil not in agent.regles.versionnees or info is None or info.type != 'versionnee':
            raise ValueError()
        decision = SimpleNamespace(mode='go', raisons=(), montant=Decimal('0.00'))
        return journal_ecritures.intention(outil, None, decision,
            [{'numero': 1, 'operation': outil, 'resume': str(objet)}], profil='versionne')
    except Exception:
        raise ToolError(journal_ecritures.INDISPONIBLE) from None


def resultat(ident, etat, objet=None, version=None):
    """Un résultat impossible à écrire ne défait jamais une écriture déjà faite."""
    if etat not in ('enregistre', 'refuse', 'inconnu'):
        return journal_ecritures.AVERTISSEMENT
    try:
        if objet is not None and not _objet(objet) or \
                version is not None and (type(version) is not int or not 0 < version <= ENTIER_MAX):
            return journal_ecritures.AVERTISSEMENT
        # Revue T069 de Thot, R05 : l'identifiant créé seul quand l'écriture n'a pas de numéro de version (contrat).
        detail = None if objet is None else f'objet {objet}' + ('' if version is None else f', version {version}')
        return journal_ecritures.resultat(ident, etat, detail)
    except Exception:
        return journal_ecritures.AVERTISSEMENT


@contextmanager
def autrui(par_humain: bool):
    """with autrui(par_humain) as decision : <écriture de l'agent>. Le verrou du signal couvre la décision et l'écriture
    jusqu'à la fin du bloc (revue 015, R05); les marques restent dans les tables de l'agent."""
    if actuelle().regles.regle_autrui != 'refus_et_marque':
        raise RuntimeError("La règle d'autrui n'est pas déclarée pour cet agent.")
    if type(par_humain) is not bool:
        raise TypeError('par_humain doit être un booléen.')
    with signal_texte.verrou(existant=True):
        yield _decision(par_humain, *signal_texte.etat_et_heure())


def _decision(par_humain, etat, heure):
    if etat in ('recent', 'douteux'):
        if par_humain:
            return 'marque', etat
        if etat == 'douteux':
            return 'refus_doute', None
        return 'refus', (heure + timedelta(seconds=signal_texte.AGE_SIGNAL)).astimezone().strftime('%H:%M')
    return None
