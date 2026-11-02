"""Registre des droits de Moneta, en code (D083, D084, spec 006; repris de Smith f9c1599, spec 013). Seul un changement de code,
revu par Ugo, le modifie.

Chaque agent déclare ses outils (type et lot déclarés, jamais déduits du nom), ses sources de lecture et les partages
qu'il donne. Un manifeste ne nomme que ce que ce registre déclare; la section « Accès aux données » en est calculée.
"""
from dataclasses import dataclass
import re

# photo (spec 007, R5) : lit une image envoyée à l'app; jamais publié dans Claude Desktop ni partagé.
TYPES_OUTIL = ('lecture', 'preparation', 'lot', 'ecriture', 'photo', 'depot')
TYPES_SOURCE = ('nombre', 'liste', 'serie', 'missions', 'texte', 'comparatif')
STATUTS = ('actif', 'en construction', 'en migration', 'à venir')
# Données lues dans le code du receveur, sans outil du propriétaire : liste fermée.
DONNEES = ('calendrier_dojo', 'paiements_dojo')
IDENTIFIANT = re.compile(r'^[a-z][a-z0-9_]{0,31}$')
GESTION = ('voir', 'retirer', 'vider', 'preparer')
# Spec 002, R2 : go dans le chat ou fenêtre du Mac, déclaré par chaque préparation, jamais déduit du nom.
CONFIRMATIONS = ('go', 'fenetre')


class RegistreInvalide(ValueError):
    pass


@dataclass(frozen=True)
class Outil:
    nom: str
    type: str
    lot: str = None
    confirmation: str = None


@dataclass(frozen=True)
class Source:
    nom: str
    type: str
    colonnes: tuple = ()
    # Outil dont la présence dans le processus rend la source lisible (base ou app absente : indisponible).
    requiert: str = None


@dataclass(frozen=True)
class Partage:
    vers: tuple
    outils: tuple = ()
    sources: tuple = ()
    donnees: tuple = ()


@dataclass(frozen=True)
class PorteeRegles:
    agent: str
    themes: tuple


@dataclass(frozen=True)
class Agent:
    id: str
    nom: str
    statut: str
    outils: tuple = ()
    sources: tuple = ()
    partages: tuple = ()
    portees: tuple = ()
    # D090 : les outils dont le code garantit qu'aucun texte écrit par d'autres ne sort sans marque (absent, retenu ou
    # marqué Autrui), dans le résultat comme dans l'erreur. Tout autre outil allume le signal, sauf résultat certifié Juge.
    sans_autrui: tuple = ()

    @property
    def vide(self):
        return not self.outils and not self.sources


def lecture(*noms):
    return tuple(Outil(nom, 'lecture') for nom in noms)


def preparation(lot, go=(), fenetre=()):
    return tuple(Outil(nom, 'preparation', lot, mode) for mode, noms in (('go', go), ('fenetre', fenetre)) for nom in noms)


def gestion(lot):
    return tuple(Outil(f'{lot}_{action}', 'lot', lot) for action in GESTION)


class Registre:
    """Les agents, vérifiés à la construction : propriétaire unique, partages sans lot ni écriture."""

    def __init__(self, agents):
        self.agents = {}
        for agent in agents:
            if not IDENTIFIANT.match(agent.id) or agent.id in self.agents:
                raise RegistreInvalide(f'identifiant d\'agent invalide ou en double : {agent.id}')
            if agent.statut not in STATUTS:
                raise RegistreInvalide(f'{agent.id} : statut inconnu « {agent.statut} »')
            self.agents[agent.id] = agent
        self.outils, self.sources = {}, {}
        for agent in agents:
            for outil in agent.outils:
                if outil.type not in TYPES_OUTIL:
                    raise RegistreInvalide(f'{agent.id} : type d\'outil inconnu « {outil.type} » ({outil.nom})')
                if outil.nom in self.outils:
                    raise RegistreInvalide(f'outil à deux propriétaires : {outil.nom}')
                self.outils[outil.nom] = (agent.id, outil)
            for source in agent.sources:
                if source.type not in TYPES_SOURCE or (source.type == 'liste') != bool(source.colonnes):
                    raise RegistreInvalide(f'{agent.id} : source mal déclarée « {source.nom} »')
                if source.nom in self.sources:
                    raise RegistreInvalide(f'source à deux propriétaires : {source.nom}')
                self.sources[source.nom] = (agent.id, source)
        self.sans_autrui = set()
        for agent in agents:
            for nom in agent.sans_autrui:
                if self.outils.get(nom, (None,))[0] != agent.id or nom in self.sans_autrui:
                    raise RegistreInvalide(f'{agent.id} : sans_autrui pour un outil qui n\'est pas le sien « {nom} »')
                self.sans_autrui.add(nom)
        for agent in agents:
            for partage in agent.partages:
                self._partage_valide(agent, partage)
            for portee in agent.portees:
                if portee.agent not in self.agents or self.agents[portee.agent].vide:
                    raise RegistreInvalide(f'{agent.id} : portée des règles pour un agent au registre vide « {portee.agent} »')

    def _partage_valide(self, agent, partage):
        for vers in partage.vers:
            # Un partage vise un agent au registre non vide (actif ou en construction), jamais lui-même.
            if vers == agent.id or vers not in self.agents or self.agents[vers].vide:
                raise RegistreInvalide(f'{agent.id} : partage vers un agent non permis « {vers} »')
        for nom in partage.outils:
            owner, outil = self.outils.get(nom, (None, None))
            if owner != agent.id:
                raise RegistreInvalide(f'{agent.id} : partage d\'un outil qui n\'est pas le sien « {nom} »')
            if outil.type not in ('lecture', 'preparation'):
                raise RegistreInvalide(f'{agent.id} : un partage ne donne jamais un outil {outil.type} « {nom} »')
        for nom in partage.sources:
            if self.sources.get(nom, (None,))[0] != agent.id:
                raise RegistreInvalide(f'{agent.id} : partage d\'une source qui n\'est pas la sienne « {nom} »')
        for nom in partage.donnees:
            if nom not in DONNEES:
                raise RegistreInvalide(f'{agent.id} : donnée de partage inconnue « {nom} »')

    def agent(self, ident):
        return self.agents.get(ident)

    def proprietaire(self, nom):
        """Agent qui possède cet outil ou cette source, ou None."""
        return (self.outils.get(nom) or self.sources.get(nom) or (None,))[0]

    def partages_recus(self, ident):
        return [(de.id, partage) for de in self.agents.values() for partage in de.partages if ident in partage.vers]

    def outils_permis(self, ident):
        """Outils que cet agent peut appeler : les siens, et les lectures ou préparations partagées avec lui."""
        agent = self.agents.get(ident)
        if agent is None:
            return {}
        permis = {outil.nom: outil for outil in agent.outils}
        for _, partage in self.partages_recus(ident):
            permis.update({nom: self.outils[nom][1] for nom in partage.outils})
        return permis

    def sources_permises(self, ident):
        agent = self.agents.get(ident)
        if agent is None:
            return {}
        permises = {source.nom: source for source in agent.sources}
        for _, partage in self.partages_recus(ident):
            permises.update({nom: self.sources[nom][1] for nom in partage.sources})
        return permises

    def portee(self, ident):
        return next((p.themes for a in self.agents.values() for p in a.portees if p.agent == ident), ())


def verifier(registre, catalogue, lots, complet=True):
    """Écarts entre le registre et les outils du serveur (liste vide : conforme).

    catalogue : outils publiés (name, annotations); lots : {lot: {'preparations', 'gestion', 'ecriture'}} lus sur les
    objets de lot du serveur. complet : le catalogue entier (--fictif --ecriture); sinon un outil déclaré mais absent du
    processus est seulement indisponible."""
    problems = []
    for nom, (_, outil) in registre.outils.items():
        # Obligatoire pour une préparation, interdite ailleurs : une préparation nouvelle n'est jamais en go par oubli.
        if (outil.type == 'preparation') != (outil.confirmation is not None) or \
                (outil.confirmation is not None and outil.confirmation not in CONFIRMATIONS):
            problems.append(f'{nom} : confirmation « {outil.confirmation} » pour un outil {outil.type}')
    published = {tool['name']: tool for tool in catalogue}
    for nom in published:
        if nom not in registre.outils:
            problems.append(f'outil sans propriétaire : {nom}')
        elif registre.outils[nom][1].type in ('photo', 'depot'):
            problems.append(f'outil {registre.outils[nom][1].type} publié : {nom}')
    if complet:
        problems += [f'outil du registre inconnu du serveur : {nom}' for nom, (_, outil) in registre.outils.items()
                     if nom not in published and outil.type not in ('photo', 'depot')]
    by_tool = {}
    for lot, parts in lots.items():
        for nom in parts['preparations']:
            by_tool[nom] = ('preparation', lot)
        for nom in parts['gestion']:
            by_tool[nom] = ('lot', lot)
        by_tool[parts['ecriture']] = ('ecriture', lot)
    for nom, tool in published.items():
        if nom not in registre.outils:
            continue
        _, outil = registre.outils[nom]
        writes = tool['annotations']['readOnlyHint'] is False
        if (outil.type == 'ecriture') != writes or writes != nom.startswith('WR_SAVE_'):
            problems.append(f'type d\'écriture discordant : {nom}')
        kind, lot = by_tool.get(nom, (None, None))
        if outil.type in ('preparation', 'lot') and (kind, lot) != (outil.type, outil.lot):
            problems.append(f'{nom} : déclaré {outil.type} du lot {outil.lot}, serveur : {kind or "hors lot"} {lot or ""}'.rstrip())
        if outil.type == 'lecture' and kind is not None:
            problems.append(f'{nom} : déclaré lecture, serveur : {kind} du lot {lot}')
        if outil.type == 'ecriture' and outil.lot != lot:
            problems.append(f'{nom} : écriture du lot {lot}, déclarée {outil.lot}')
    return problems


def confirmation(outil):
    """Étiquette affichée (FR-005), calculée sur le type de l'outil et, pour une préparation, sur sa confirmation (spec 002)."""
    if outil.type == 'preparation':
        return {'go': 'lot, go dans le chat', 'fenetre': 'lot, fenêtre du Mac'}[outil.confirmation]
    return {'lecture': 'aucune', 'lot': 'aucune (gestion du lot)', 'ecriture': 'fenêtre du Mac',
            'photo': 'aucune (photo lue sur le Mac)',
            'depot': 'aucune (dépôt choisi par Ugo dans l’app, D104)'}[outil.type]


def acces(registre, ident):
    """Section « Accès aux données » : fonction pure du registre (FR-004, SC-006)."""
    agent = registre.agent(ident)
    if agent is None:
        return {}
    preparations = {}
    for outil in agent.outils:
        if outil.type == 'preparation':
            preparations.setdefault(outil.lot, []).append(outil.nom)
    recus = [{'de': de, 'outils': list(p.outils), 'sources': list(p.sources), 'donnees': list(p.donnees)}
             for de, p in registre.partages_recus(ident)]
    donnes = [{'vers': list(p.vers), 'outils': list(p.outils), 'sources': list(p.sources), 'donnees': list(p.donnees)}
              for p in agent.partages]
    return {'lectures': [o.nom for o in agent.outils if o.type in ('lecture', 'photo')] + [s.nom for s in agent.sources],
            'preparations': preparations,
            'ecritures': [o.nom for o in agent.outils if o.type in ('ecriture', 'depot')],
            'partages_recus': recus, 'partages_donnes': donnes,
            'regles_affaires': list(registre.portee(ident))}


def construire():
    from moneta.agent.registre import AGENT as moneta
    return Registre((moneta,))


def __getattr__(nom):
    # Construit à la première lecture : moneta.agent.registre importe ce module avant d'avoir son AGENT.
    if nom == 'REGISTRE':
        globals()['REGISTRE'] = construire()
        return globals()['REGISTRE']
    raise AttributeError(nom)
