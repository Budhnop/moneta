"""Où vit un agent, et s'il vit seul ou dans un écosystème (socle 2.0, spec 017 de Smith, D112).

Deux petits fichiers, lus sans jamais suivre un lien et refusés s'ils sont lisibles par d'autres :

- installation.toml, à la racine du code de l'agent, hors Git : racine = "<dossier des données de l'installation>" et
  ecosysteme = true|false. Le dossier réel de l'agent est racine/<nom>. Sans ce fichier, l'agent n'a aucun dossier réel et ne
  tourne que sur des données fictives : un worktree, qui ne l'a pas, ne peut jamais viser les vraies données.
- ecosysteme.toml, dans la racine : agents = [...] et, facultatif, [portable] mots = [...] pour la règle C11.

Le code du socle ne nomme aucun agent et aucun dossier personnel : tout vient de ces fichiers. installer() pose aussi une
marque (.socle-reel) dans la racine et dans le dossier réel; un dossier marqué, ou un dossier sous un dossier marqué, n'est
jamais pris pour du fictif, même par un agent non installé, même écrit par un lien (revue 017, S01, S02). La marque du dossier
réel nomme le code qui l'a installé : une autre copie du code (un worktree) n'y démarre jamais (S04).

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
# Dossiers que synchronise un nuage, sous le dossier personnel (revue 017, P02, S07). Documents et Bureau : iCloud peut les
# synchroniser sans que leur chemin change, donc refusés aussi. Un nom qui commence ainsi (« OneDrive - Entreprise ») compte.
NUAGES = (('Library', 'CloudStorage'), ('Library', 'Mobile Documents'), ('Documents',), ('Desktop',), ('Dropbox',),
          ('OneDrive',), ('Box',), ('Nextcloud',), ('Google Drive',), ('pCloud Drive',), ('Sync',))
NUAGES_PREFIXES = ('OneDrive', 'Dropbox', 'Nextcloud')

ABSENTE = 'Installation absente : {agent} ne démarre que sur des données fictives (bin/{agent}-installer).'
INTROUVABLE = 'Écosystème introuvable ou altéré à {racine} : {agent} refuse de démarrer seul sur ses vraies données.'
NON_INSCRIT = "{agent} n'est pas inscrit à l'écosystème de {racine}."
RACINE_PRISE = "La racine {racine} appartient à un écosystème : un agent seul ne s'y installe pas."
MARQUE_ETRANGERE = '{dossier} est un dossier réel : {agent} ne le prend jamais pour des données fictives.'
SANS_MARQUE = '{dossier} : marque .socle-reel absente ou altérée; réinstaller {agent} (bin/{agent}-installer).'
AUTRE_CODE = ('{dossier} a été installé par un autre code ({autre}) : {agent} ne démarre pas depuis {code}. '
              'Une autre copie du code (worktree) ne vise jamais les vraies données.')
BASE_REELLE = 'Base {base} dans un dossier réel, hors des données réelles de {agent} : refusée.'


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
    code: Path = None  # le dossier du code qui a lu installation.toml, liens résolus (S04)


@dataclass(frozen=True)
class Ecosysteme:
    agents: tuple
    mots: tuple = ()


def _lire_prive(dossier, nom, erreur, masque_dossier=0o022):
    """Les octets de dossier/nom, ou None s'il n'existe pas. Jamais à travers un lien; le fichier et son dossier appartiennent
    à l'utilisateur, le fichier n'est lisible que par lui, le dossier n'est écrivable que par lui (masque_dossier 0o077 : ni
    lisible ni traversable par d'autres, pour la racine des données, S06)."""
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
        if info.st_uid != os.getuid() or info.st_mode & masque_dossier:
            os.close(fd)
            qui = 'accessible' if masque_dossier & 0o044 else 'écrivable'
            raise erreur(f'Dossier {dossier} : il doit appartenir à l’utilisateur et n’être {qui} que par lui.')
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


def _textes(chemin):
    """Le chemin tel qu'écrit et liens résolus, casse repliée (APFS ignore la casse)."""
    return {os.path.abspath(chemin).casefold(), os.path.realpath(chemin).casefold()}


def _sous(texte, base):
    base = base.rstrip('/')
    return texte == base or texte.startswith(base + '/')


def _maisons():
    """Le dossier personnel de la variable HOME et celui du compte : un HOME changé ne cache pas un nuage (S07, R08 de la 015)."""
    maisons = {Path.home()}
    try:
        import pwd
        maisons.add(Path(pwd.getpwuid(os.getuid()).pw_dir))
    except (ImportError, KeyError, OSError):
        pass
    return maisons


def _dans_un_nuage(chemin):
    textes = _textes(chemin)
    for maison in _maisons():
        for base_maison in {str(maison).casefold(), os.path.realpath(maison).casefold()}:
            for t in textes:
                if not _sous(t, base_maison) or t == base_maison.rstrip('/'):
                    continue
                premier = t[len(base_maison.rstrip('/')) + 1:].split('/')
                for morceaux in NUAGES:
                    if [m.casefold() for m in premier[:len(morceaux)]] == [m.casefold() for m in morceaux]:
                        return True
                if any(premier[0].startswith(p.casefold()) for p in NUAGES_PREFIXES):
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
    return Installation(_racine_valide(brut['racine'], InstallationInvalide), brut['ecosysteme'],
                        Path(os.path.realpath(code)))


def dossier_reel(installation, nom):
    return None if installation is None else installation.racine / nom


def ecosysteme(racine):
    """L'écosystème de cette racine, relu à chaque appel, ou None sans fichier."""
    chemin = Path(racine) / FICHIER_ECOSYSTEME
    donnees = _lire_prive(racine, FICHIER_ECOSYSTEME, EcosystemeInvalide, masque_dossier=0o077)
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
    """Vrai si ce dossier, ou un de ses parents, porte la marque d'un dossier réel, le chemin pris tel qu'écrit et liens résolus
    (un lien vers un sous-dossier ou vers une base réelle reste réel, S02). Un fichier à plusieurs liens physiques est un doute :
    il peut être une base réelle, donc réel aussi."""
    chemin = Path(chemin).expanduser()
    for texte in (os.path.abspath(chemin), os.path.realpath(chemin)):
        courant = Path(texte)
        for dossier in (courant, *courant.parents):
            if os.path.lexists(dossier / MARQUE):
                return True
    try:
        info = os.stat(chemin)
    except OSError:
        return False
    return stat.S_ISREG(info.st_mode) and info.st_nlink > 1


def _texte_marque(code):
    return f'code = "{_echapper(str(code))}"\n'.encode('utf-8')


def code_de_la_marque(dossier):
    """Le code nommé par la marque de ce dossier réel, '' pour une marque vide (posée à la main), None sans marque.
    InstallationInvalide si elle ne peut être crue."""
    donnees = _lire_prive(dossier, MARQUE, InstallationInvalide)
    if donnees is None:
        return None
    if not donnees.strip():
        return ''
    brut = _toml(donnees, Path(dossier) / MARQUE, InstallationInvalide)
    if set(brut) != {'code'} or type(brut['code']) is not str or not os.path.isabs(brut['code']):
        raise InstallationInvalide(f'{Path(dossier) / MARQUE} : exactement code = "<dossier du code>".')
    return brut['code']


def _bases(declaration):
    try:
        return tuple(declaration.bases())
    except Exception as exc:
        raise EtatRefuse(f'Bases de {declaration.nom} illisibles : {exc}') from None


def _verifier_marques(declaration, inst):
    """Un agent installé ne démarre sur son dossier réel que si la racine et ce dossier portent leur marque, et que la marque
    du dossier nomme ce code-ci (S01, S04)."""
    reel = dossier_reel(inst, declaration.nom)
    if not os.path.lexists(inst.racine / MARQUE):
        raise EtatRefuse(SANS_MARQUE.format(dossier=inst.racine, agent=declaration.nom))
    try:
        code = code_de_la_marque(reel)
    except InstallationInvalide:
        code = None
    if not code:
        raise EtatRefuse(SANS_MARQUE.format(dossier=reel, agent=declaration.nom))
    if inst.code is None or os.path.realpath(code).casefold() != os.path.realpath(inst.code).casefold():
        raise EtatRefuse(AUTRE_CODE.format(dossier=reel, autre=code, agent=declaration.nom, code=inst.code))


def _dedans(chemin, dossier):
    from socle.confirmation import _inside  # au besoin : confirmation importe declaration, qui importe ce module
    return _inside(chemin, dossier)


def etat(declaration):
    """'fictif', 'seul' ou 'inscrit'; EtatRefuse quand l'agent ne doit pas démarrer sur ses vraies données."""
    inst = declaration.installation
    donnees = Path(declaration.dossier_donnees())
    reels = () if inst is None else (dossier_reel(inst, declaration.nom), *declaration.dossiers_reels)
    # Une base réelle hors des données réelles de l'agent (variable de base seule, lien) : jamais un état fictif (S03).
    for base in _bases(declaration):
        if est_reel(base) and not any(_dedans(base, reel) for reel in reels):
            raise EtatRefuse(BASE_REELLE.format(base=base, agent=declaration.nom))
    if inst is None:
        if est_reel(donnees):
            raise EtatRefuse(ABSENTE.format(agent=declaration.nom))
        return 'fictif'
    if not any(_dedans(donnees, reel) for reel in reels):
        if est_reel(donnees):
            raise EtatRefuse(MARQUE_ETRANGERE.format(dossier=donnees, agent=declaration.nom))
        return 'fictif'
    _verifier_marques(declaration, inst)
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


def installer(code, nom, racine, avec_ecosysteme=False, reprendre=False):
    """Écrit code/installation.toml (600) et prépare racine/nom (700); marque la racine et le dossier réel, ce dernier au nom
    de ce code. Un dossier déjà marqué par un autre code est refusé, sauf reprendre (le code a changé de place). Refus avant
    toute écriture."""
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
    # Comparés tels qu'écrits et liens résolus (/tmp et /private/tmp, un lien dans le chemin; P01).
    for a in _textes(racine):
        for b in _textes(code):
            if _sous(a, b) or _sous(b, a):
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
    vrai_code = Path(os.path.realpath(code))
    if os.path.lexists(dossier / MARQUE):
        try:
            deja = code_de_la_marque(dossier)
        except InstallationInvalide as exc:
            raise Refus(str(exc)) from None
        if deja and os.path.realpath(deja).casefold() != str(vrai_code).casefold() and not reprendre:
            raise Refus(f'{dossier} est déjà installé par {deja} : une autre copie du code ne le prend pas '
                        '(--reprendre seulement si ce code-ci le remplace).')
    if racine.exists():
        info = os.stat(racine)
        if info.st_mode & 0o077:
            raise Refus(f'Racine {racine} accessible par d’autres : chmod 700 d’abord.')
    private_folder(racine)
    if not os.path.lexists(racine / MARQUE):
        write_private(racine, MARQUE, b'')
    private_folder(dossier)
    write_private(dossier, MARQUE, _texte_marque(vrai_code))
    texte = f'racine = "{_echapper(str(racine))}"\necosysteme = {"true" if avec_ecosysteme else "false"}\n'
    write_private(code, FICHIER_INSTALLATION, texte.encode('utf-8'))
    return code / FICHIER_INSTALLATION


def _echapper(texte):
    return texte.replace('\\', '\\\\').replace('"', '\\"')
