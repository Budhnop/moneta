"""Où vit un agent, et s'il vit seul ou dans un écosystème (socle 2.0, spec 017 de Smith, D112).

Deux petits fichiers, lus sans jamais suivre un lien et refusés s'ils sont lisibles par d'autres :

- installation.toml, à la racine du code de l'agent, hors Git : racine = "<dossier des données de l'installation>" et
  ecosysteme = true|false. Le dossier réel de l'agent est racine/<nom>. Sans ce fichier, l'agent n'a aucun dossier réel et ne
  tourne que sur des données fictives : un worktree, qui ne l'a pas, ne peut jamais viser les vraies données.
- ecosysteme.toml, dans la racine : agents = [...] et, facultatif, [portable] mots = [...] pour la règle C11.

Le code du socle ne nomme aucun agent et aucun dossier personnel : tout vient de ces fichiers. installer() pose aussi une
marque (.socle-reel) dans le dossier réel; un dossier marqué n'est jamais pris pour du fictif, même par un agent non installé.

N'importe declaration et confirmation qu'au besoin, dans les fonctions : declaration importe ce module.
"""
from dataclasses import dataclass
import os
from pathlib import Path
import re
import stat
import tomllib

from socle.chemins import private_folder, write_private

NOM = re.compile(r'^[a-z]{2,20}$')
FICHIER_INSTALLATION = 'installation.toml'
FICHIER_ECOSYSTEME = 'ecosysteme.toml'
MARQUE = '.socle-reel'
TAILLE_MAX = 64 * 1024
NUAGES = (('Library', 'CloudStorage'), ('Library', 'Mobile Documents'))

ABSENTE = 'Installation absente : {agent} ne démarre que sur des données fictives (bin/{agent}-installer).'
INTROUVABLE = 'Écosystème introuvable ou altéré à {racine} : {agent} refuse de démarrer seul sur ses vraies données.'
NON_INSCRIT = "{agent} n'est pas inscrit à l'écosystème de {racine}."
RACINE_PRISE = "La racine {racine} appartient à un écosystème : un agent seul ne s'y installe pas."
MARQUE_ETRANGERE = '{dossier} est un dossier réel : {agent} ne le prend jamais pour des données fictives.'


class InstallationInvalide(OSError):
    """installation.toml existe mais ne peut pas être cru."""


class EcosystemeInvalide(OSError):
    """ecosysteme.toml existe mais ne peut pas être cru."""


class EtatRefuse(OSError):
    """L'agent ne doit pas démarrer sur ses vraies données; le message dit pourquoi, en français."""


class Refus(Exception):
    """installer() refuse; rien n'est écrit."""


@dataclass(frozen=True)
class Installation:
    racine: Path
    ecosysteme: bool


@dataclass(frozen=True)
class Ecosysteme:
    agents: tuple
    mots: tuple = ()


def _lire_prive(dossier, nom, erreur):
    """Les octets de dossier/nom, ou None s'il n'existe pas. Jamais à travers un lien; le fichier et son dossier appartiennent
    à l'utilisateur, le fichier n'est lisible que par lui, le dossier n'est écrivable que par lui."""
    try:
        fd_dossier = os.open(dossier, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise erreur(f'Dossier {dossier} illisible ou remplacé par un lien.') from exc
    try:
        try:
            fd = os.open(nom, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd_dossier)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise erreur(f'{Path(dossier) / nom} illisible ou remplacé par un lien.') from exc
        info = os.fstat(fd_dossier)
        if info.st_uid != os.getuid() or info.st_mode & 0o022:
            os.close(fd)
            raise erreur(f'Dossier {dossier} : il doit appartenir à l’utilisateur et n’être écrivable que par lui.')
        with os.fdopen(fd, 'rb') as fichier:
            info = os.fstat(fichier.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise erreur(f'{Path(dossier) / nom} : un fichier ordinaire, à l’utilisateur, en 600.')
            donnees = fichier.read(TAILLE_MAX + 1)
        if len(donnees) > TAILLE_MAX:
            raise erreur(f'{Path(dossier) / nom} trop grand.')
        return donnees
    finally:
        os.close(fd_dossier)


def _toml(donnees, chemin, erreur):
    try:
        return tomllib.loads(donnees.decode('utf-8'))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise erreur(f'{chemin} illisible : {exc}') from None


def _dans_un_nuage(chemin):
    textes = {os.path.abspath(chemin).casefold(), os.path.realpath(chemin).casefold()}
    maison = Path.home()
    for morceaux in NUAGES:
        nuage = maison.joinpath(*morceaux)
        for base in {str(nuage).casefold(), os.path.realpath(nuage).casefold()}:
            if any(t == base or t.startswith(base.rstrip('/') + '/') for t in textes):
                return True
    return False


def _racine_valide(racine, erreur):
    if type(racine) is not str or not racine or not os.path.isabs(racine):
        raise erreur('racine : un chemin absolu.')
    if _dans_un_nuage(racine):
        raise erreur(f'racine {racine} dans un dossier synchronisé par un nuage : refusée.')
    return Path(os.path.normpath(racine))


def lire(code):
    """L'installation de l'agent dont le code est dans `code`, ou None sans fichier."""
    chemin = Path(code) / FICHIER_INSTALLATION
    donnees = _lire_prive(code, FICHIER_INSTALLATION, InstallationInvalide)
    if donnees is None:
        return None
    brut = _toml(donnees, chemin, InstallationInvalide)
    if set(brut) != {'racine', 'ecosysteme'}:
        raise InstallationInvalide(f'{chemin} : exactement racine et ecosysteme.')
    if type(brut['ecosysteme']) is not bool:
        raise InstallationInvalide(f'{chemin} : ecosysteme vaut true ou false.')
    return Installation(_racine_valide(brut['racine'], InstallationInvalide), brut['ecosysteme'])


def dossier_reel(installation, nom):
    return None if installation is None else installation.racine / nom


def ecosysteme(racine):
    """L'écosystème de cette racine, relu à chaque appel, ou None sans fichier."""
    chemin = Path(racine) / FICHIER_ECOSYSTEME
    donnees = _lire_prive(racine, FICHIER_ECOSYSTEME, EcosystemeInvalide)
    if donnees is None:
        return None
    brut = _toml(donnees, chemin, EcosystemeInvalide)
    if not set(brut) <= {'agents', 'portable'} or 'agents' not in brut:
        raise EcosystemeInvalide(f'{chemin} : agents, et facultativement [portable].')
    agents = brut['agents']
    if not isinstance(agents, list) or not agents or not all(type(a) is str and NOM.match(a) for a in agents) \
            or len(set(agents)) != len(agents):
        raise EcosystemeInvalide(f'{chemin} : agents est une liste de noms (a-z, 2 à 20 lettres), sans doublon.')
    portable = brut.get('portable', {})
    if not isinstance(portable, dict) or not set(portable) <= {'mots'}:
        raise EcosystemeInvalide(f'{chemin} : [portable] ne contient que mots.')
    mots = portable.get('mots', [])
    if not isinstance(mots, list) or not all(type(m) is str and m.strip() for m in mots):
        raise EcosystemeInvalide(f'{chemin} : [portable] mots est une liste de textes non vides.')
    return Ecosysteme(tuple(agents), tuple(mots))


def est_reel(chemin):
    """Vrai si ce dossier, ou un de ses parents, porte la marque d'un dossier réel posée par installer()."""
    courant = Path(os.path.abspath(Path(chemin).expanduser()))
    for dossier in (courant, *courant.parents):
        if os.path.lexists(dossier / MARQUE):
            return True
    return False


def _dedans(chemin, dossier):
    from socle.confirmation import _inside  # au besoin : confirmation importe declaration, qui importe ce module
    return _inside(chemin, dossier)


def etat(declaration):
    """'fictif', 'seul' ou 'inscrit'; EtatRefuse quand l'agent ne doit pas démarrer sur ses vraies données."""
    inst = declaration.installation
    donnees = Path(declaration.dossier_donnees())
    if inst is None:
        if est_reel(donnees):
            raise EtatRefuse(ABSENTE.format(agent=declaration.nom))
        return 'fictif'
    if not any(_dedans(donnees, reel) for reel in (dossier_reel(inst, declaration.nom), *declaration.dossiers_reels)):
        if est_reel(donnees):
            raise EtatRefuse(MARQUE_ETRANGERE.format(dossier=donnees, agent=declaration.nom))
        return 'fictif'
    try:
        eco = ecosysteme(inst.racine)
    except EcosystemeInvalide:
        raise EtatRefuse(INTROUVABLE.format(racine=inst.racine, agent=declaration.nom)) from None
    if not inst.ecosysteme:
        if eco is not None:
            raise EtatRefuse(RACINE_PRISE.format(racine=inst.racine))
        return 'seul'
    if eco is None:
        raise EtatRefuse(INTROUVABLE.format(racine=inst.racine, agent=declaration.nom))
    if declaration.nom not in eco.agents:
        raise EtatRefuse(NON_INSCRIT.format(agent=declaration.nom, racine=inst.racine))
    return 'inscrit'


def dossier_commun(sous_dossier):
    """Le dossier du signal ou du journal de l'agent déclaré : dans la racine pour un agent inscrit, sinon dans ses données.
    Sans déclaration, DeclarationAbsente; un état refusé lève EtatRefuse : les deux sont des OSError, donc un doute."""
    from socle.declaration import actuelle
    agent = actuelle()
    if etat(agent) == 'inscrit':
        return agent.installation.racine / sous_dossier
    return Path(agent.dossier_donnees()) / sous_dossier


def ecrivain_connu(nom):
    """Vrai si `nom` peut écrire le signal ou le journal que lit l'agent déclaré. Tout doute rend faux.

    Inscrit : un agent de l'écosystème (relu à chaque appel). Seul : lui-même. Fictif : les agents d'un ecosysteme.toml posé
    à côté de son dossier de données (bancs d'essai), sinon lui-même."""
    try:
        if type(nom) is not str or not NOM.match(nom):
            return False
        from socle.declaration import actuelle
        agent = actuelle()
        mode = etat(agent)
        if mode == 'inscrit':
            return nom in ecosysteme(agent.installation.racine).agents
        if mode == 'fictif':
            eco = ecosysteme(Path(os.path.realpath(agent.dossier_donnees())).parent)
            if eco is not None:
                return nom in eco.agents
        return nom == agent.nom
    except Exception:
        return False


def installer(code, nom, racine, avec_ecosysteme=False):
    """Écrit code/installation.toml (600) et prépare racine/nom (700, marqué réel). Refus avant toute écriture."""
    if type(nom) is not str or not NOM.match(nom):
        raise Refus(f'Nom {nom!r} : a-z, 2 à 20 lettres.')
    try:
        racine = _racine_valide(str(racine), Refus)
    except Refus:
        raise
    code = Path(os.path.abspath(code))
    if not code.is_dir() or code.is_symlink():
        raise Refus(f'Dossier du code introuvable : {code}.')
    if os.path.lexists(code / FICHIER_INSTALLATION):
        raise Refus(f'{code / FICHIER_INSTALLATION} existe déjà : une installation ne se réécrit pas.')
    for a, b in ((racine, code), (code, racine)):
        if str(a).casefold() == str(b).casefold() or str(a).casefold().startswith(str(b).casefold().rstrip('/') + '/'):
            raise Refus('Le code et les données ne se mélangent pas : racine hors du dépôt, dépôt hors de la racine.')
    try:
        eco = ecosysteme(racine)
    except EcosystemeInvalide as exc:
        raise Refus(str(exc)) from None
    if avec_ecosysteme and (eco is None or nom not in eco.agents):
        raise Refus(f'Ajouter « {nom} » à {racine / FICHIER_ECOSYSTEME} avant d’installer l’agent dans l’écosystème.')
    if not avec_ecosysteme and eco is not None:
        raise Refus(RACINE_PRISE.format(racine=racine))
    dossier = racine / nom
    if dossier.is_symlink() or racine.is_symlink():
        raise Refus(f'{dossier} : jamais à travers un lien.')
    private_folder(dossier)
    if not os.path.lexists(dossier / MARQUE):
        write_private(dossier, MARQUE, b'')
    texte = f'racine = "{_echapper(str(racine))}"\necosysteme = {"true" if avec_ecosysteme else "false"}\n'
    write_private(code, FICHIER_INSTALLATION, texte.encode('utf-8'))
    return code / FICHIER_INSTALLATION


def _echapper(texte):
    return texte.replace('\\', '\\\\').replace('"', '\\"')
