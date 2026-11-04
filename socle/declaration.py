"""Ce qu'un agent dit de lui-même au socle : une déclaration figée, retenue une fois par processus.

N'importe aucun autre module du socle, et aucun module du socle n'importe un agent. Tant que rien n'est retenu, actuelle()
lève DeclarationAbsente, une OSError : les chemins qui attrapent déjà OSError refusent alors d'eux-mêmes, et le socle prend
le parti le plus strict. La déclaration empêche l'oubli, pas la fraude; la retouche relève de l'épingle et de la revue.
"""
from dataclasses import dataclass, field, replace
from pathlib import Path
import re
from types import MappingProxyType
from typing import Callable

from socle import installation

# Socle 2.0 (spec 017 de Smith, D112) : aucune liste d'agents ici. Le nom a une forme; qui écrit le signal et qui se lit
# viennent du fichier d'écosystème (socle.installation).
NOM = installation.NOM
# Une donnée partagée lue chez un autre agent (spec 013) : « agent:donnee », par exemple « smith:paiements_dojo ».
LECTURE = re.compile(r'^[a-z]+:[a-z_]+$')
RAISONS_BASE = ('texte_autrui_recent', 'signal_douteux', 'hors_liste', 'plafond_montant', 'plafond_jour', 'plafond_etapes', 'doute')


class DeclarationAbsente(OSError):
    """Aucun agent n'est déclaré dans ce processus."""


@dataclass(frozen=True)
class InfoOutil:
    type: str
    lot: str
    confirmation: str


@dataclass(frozen=True)
class Regles:
    lots: tuple
    avec_montant: dict
    facultatif: frozenset
    raison_fenetre: dict
    raisons: dict  # code -> libellé; son ordre est celui de l'affichage
    montant_max: object
    etapes_max: int
    juges: dict  # lot -> fonction(etape, config?) fournie par l'agent
    hors_wr: tuple  # écritures sans le préfixe WR_SAVE_
    versionnees: dict = field(default_factory=dict)
    regle_autrui: str = 'aucune'


@dataclass(frozen=True)
class Textes:
    fenetre_debut: str
    fenetre_fin: str
    notice_autrui: str
    message_consigne: str
    modes: dict  # go, fenetre
    cle_adresses: str
    raison_adresse: str


@dataclass(frozen=True)
class Declaration:
    nom: str
    dossier_donnees: Callable
    dossiers_reels: tuple
    bases: Callable
    dossiers_sortie: Callable
    outil: Callable  # nom -> InfoOutil ou None
    noms_outils: Callable
    regles: Regles
    textes: Textes
    lectures: tuple = ()  # données d'autres agents qu'il lit, chacune « agent:donnee »
    installation: object = None  # socle.installation.Installation, ou None : l'agent ne tourne alors que sur du fictif


_retenue = None
_source = None


def _exiger(condition, message):
    if not condition:
        raise ValueError(f'Déclaration invalide : {message}.')


def _entier(valeur):
    return type(valeur) is int


def _valider_regles(r):
    _exiger(isinstance(r, Regles), 'regles')
    _exiger(isinstance(r.lots, tuple) and all(type(l) is str for l in r.lots), 'lots')
    _exiger(isinstance(r.avec_montant, dict), 'avec_montant')
    _exiger(isinstance(r.facultatif, (set, frozenset)), 'facultatif')
    _exiger(isinstance(r.raison_fenetre, dict), 'raison_fenetre')
    _exiger(isinstance(r.raisons, dict) and all(type(k) is str and type(v) is str for k, v in r.raisons.items()), 'raisons')
    manquantes = [c for c in RAISONS_BASE if c not in r.raisons] if isinstance(r.raisons, dict) else []
    _exiger(not manquantes, f'raisons de base manquantes ({", ".join(manquantes)})')
    _exiger(type(r.montant_max).__name__ in ('int', 'Decimal') and r.montant_max >= 0, 'montant_max')
    _exiger(_entier(r.etapes_max) and r.etapes_max > 0, 'etapes_max')
    _exiger(isinstance(r.juges, dict) and all(callable(j) for j in r.juges.values()), 'juges')
    _exiger(isinstance(r.hors_wr, tuple) and all(type(n) is str for n in r.hors_wr), 'hors_wr')

    _exiger(isinstance(r.versionnees, dict) and all(type(k) is str and isinstance(v, (tuple, list)) and len(v) == 2
             and all(type(c) is str and c for c in v) for k, v in r.versionnees.items()), 'versionnees')
    _exiger(r.regle_autrui in ('aucune', 'refus_et_marque'), 'regle_autrui')


def _valider_textes(t):
    _exiger(isinstance(t, Textes), 'textes')
    for champ in ('fenetre_debut', 'fenetre_fin', 'notice_autrui', 'message_consigne', 'cle_adresses', 'raison_adresse'):
        _exiger(type(getattr(t, champ)) is str, champ)
    _exiger(isinstance(t.modes, dict) and set(t.modes) == {'go', 'fenetre'} and all(type(v) is str for v in t.modes.values()), 'modes')


def _valider(d):
    _exiger(isinstance(d, Declaration), "ce n'est pas une Declaration")
    _exiger(type(d.nom) is str and NOM.match(d.nom), 'nom (a-z, 2 à 20 lettres)')
    for champ in ('dossier_donnees', 'bases', 'dossiers_sortie', 'outil', 'noms_outils'):
        _exiger(callable(getattr(d, champ)), champ)
    _exiger(d.installation is None or isinstance(d.installation, installation.Installation), 'installation')
    _exiger(isinstance(d.dossiers_reels, tuple), 'dossiers_reels')
    _exiger(d.installation is None or installation.dossier_reel(d.installation, d.nom) in tuple(Path(c) for c in d.dossiers_reels),
            'le dossier réel de l’installation dans dossiers_reels')
    _exiger(all(isinstance(c, (str, Path)) and Path(c).is_absolute() for c in d.dossiers_reels), 'chemins réels absolus')
    _valider_regles(d.regles)
    _valider_textes(d.textes)
    _exiger(isinstance(d.lectures, tuple) and all(type(l) is str and LECTURE.match(l) and NOM.match(l.split(':')[0])
                                                  and l.split(':')[0] != d.nom for l in d.lectures), 'lectures')


def _verifier_etat(d):
    """Sur les vraies données, l'état décide : EtatRefuse (une OSError) empêche l'agent de démarrer. Un agent seul ne lit
    personne; un agent inscrit ne lit que des agents de son écosystème. En fictif, la forme suffit."""
    mode = installation.etat(d)
    if mode == 'seul' and d.lectures:
        raise installation.EtatRefuse(f'{d.nom} vit seul : aucune lecture chez un autre agent ({", ".join(d.lectures)}). '
                                      f'Retirer la lecture, ou installer {d.nom} dans un écosystème (bin/{d.nom}-installer --ecosysteme).')
    if mode == 'inscrit':
        agents = installation.ecosysteme(d.installation.racine).agents
        hors = [l for l in d.lectures if l.split(':')[0] not in agents]
        if hors:
            raise installation.EtatRefuse(f'{d.nom} lit un agent absent de son écosystème ({", ".join(hors)}).')


def _figer(valeur):
    if isinstance(valeur, dict):
        return MappingProxyType({k: _figer(v) for k, v in valeur.items()})
    if isinstance(valeur, (list, tuple)):
        return tuple(_figer(v) for v in valeur)
    if isinstance(valeur, (set, frozenset)):
        return frozenset(valeur)
    return valeur


def _figee(d):
    """A copy of what was validated: changing the agent's own tables afterwards can never loosen a decision."""
    r, t = d.regles, d.textes
    return replace(d, regles=replace(r, avec_montant=_figer(r.avec_montant), facultatif=_figer(r.facultatif),
                                     raison_fenetre=_figer(r.raison_fenetre), raisons=_figer(r.raisons), juges=_figer(r.juges),
                                     versionnees=_figer(r.versionnees)),
                   textes=replace(t, modes=_figer(t.modes)))


def declarer(declaration):
    """Une fois par processus; ValueError si elle est invalide ou si une autre est déjà retenue, et rien ne change."""
    global _retenue, _source
    if _retenue is not None and (declaration is _retenue or declaration is _source):
        return _retenue
    _valider(declaration)
    _verifier_etat(declaration)
    if _retenue is not None:
        raise ValueError('Déclaration invalide : un autre agent est déjà déclaré dans ce processus.')
    _retenue, _source = _figee(declaration), declaration
    return _retenue


def actuelle():
    if _retenue is None:
        raise DeclarationAbsente('Aucun agent déclaré.')
    return _retenue
