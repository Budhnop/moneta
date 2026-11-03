"""La suite de conformité d'un agent (spec 014 de Smith, R3) : les règles de D107 en tests, lancées par l'agent dans ses propres tests.

cas(racine, module) rend une classe unittest.TestCase, une règle par méthode, que l'agent place dans tests/test_conformite.py.
Le code de l'agent est lu par ast; seul son paquet est importé (il se déclare), et rien d'autre n'est lancé : ni programme, ni
réseau, ni vraie donnée. conformite.toml, à la racine de l'agent, donne son profil, ses sorties du Mac et ses exceptions; une
exception sans raison ni décision, ou qui ne sert plus, fait échouer la suite (EF-007). La suite empêche l'oubli, pas la fraude.
"""
import ast
from dataclasses import dataclass
from datetime import datetime, timezone
import importlib
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess
import sys
import tempfile
import tomllib
import unittest

from socle import declaration, empreinte
from socle.chemins import write_private

REGLES = {
    'C1': 'une base, un propriétaire',
    'C2': "profil d'écriture",
    'C3': 'sorties du Mac',
    'C4': "texte d'autrui",
    'C5': 'nuit',
    'C6': 'socle intact',
    'C7': 'données',
    'C8': 'dépendances',
    'C9': 'hygiène des tests',
}
PROFILS = ('lecture', 'lot')
# Les destinations de nuit permises par profil (le profil versionné, à l'étape 5 : jamais Drive).
DESTINATIONS = {'lecture': ('DESTINATION', 'DISQUE'), 'lot': ('DESTINATION', 'DISQUE')}
SORTIES = ('drive',)  # une sortie directe du Mac, chacune listée avec sa décision
RESEAU = ('socket', 'ssl', 'http', 'urllib', 'smtplib', 'ftplib', 'poplib', 'imaplib', 'telnetlib', 'xmlrpc', 'socketserver',
          'webbrowser', 'asyncio')
RESEAU_SOUS = ('multiprocessing.connection', 'multiprocessing.managers')  # des sockets sous un module permis
PROGRAMMES_SORTIE = ('curl', 'wget', 'scp', 'sftp', 'ssh', 'rsync', 'rclone', 'nc', 'ftp', 'telnet', 'mail', 'sendmail', 'open',
                     'osascript')
# Un interpréteur en tête de commande cache le programme qu'il lance : jamais sans décision.
INTERPRETES = ('sh', 'bash', 'zsh', 'dash', 'ksh', 'csh', 'tcsh', 'fish', 'env', 'xargs', 'perl', 'ruby', 'node')
GIT_SORTIE = ('push', 'fetch', 'pull', 'clone', 'ls-remote', 'send-email', 'request-pull')
# Les dossiers synchronisés dans un nuage : une sortie du Mac, comme Google Drive.
DRIVE = re.compile(r'CloudStorage|GoogleDrive|Google Drive|Mobile Documents|CloudDocs|iCloud|Dropbox|OneDrive|Box Sync', re.I)
BIN_SORTIE = re.compile(r'(?<![\w.-])(?:/[\w/.-]*/)?(' + '|'.join(PROGRAMMES_SORTIE + INTERPRETES[:-3]) + r')(?=\s|$|;|\|)', re.M)
# Seuls pip et ses aides peuvent être dans .venv (venv --without-pip n'y met rien).
VENV_PERMIS = re.compile(r'^(?:pip|pip-\d[\w.]*\.dist-info|_distutils_hack|distutils-precedence\.pth|README(?:\.txt)?)$')
DEPENDANCES = ('requirements.txt', 'requirements-dev.txt', 'requirements-test.txt', 'Pipfile', 'poetry.lock', 'uv.lock',
               'setup.py', 'setup.cfg')
# Le nom de la tâche de nuit et du trousseau : « <préfixe>.<agent>.sauvegarde », le préfixe étant celui du modèle de tâche.
TROUSSEAU = re.compile(r'^[a-z][a-z0-9-]*(?:\.[a-z0-9-]+)+\.sauvegarde$')
APPELS = ('run', 'Popen', 'check_output', 'check_call', 'call')
PYTHON_NU = re.compile(r'(?<![/\w.$-])python3?(\.\d+)?(?![\w/.-])')
CHEMINS = ('join', 'joinpath', 'Path', 'PurePath', 'PosixPath')
TOUT = '*'  # une exception qui couvre tout un fichier pour sa règle
RESULTAT = '.conformite.json'
GIT = '/usr/bin/git'


class ConformiteInvalide(ValueError):
    """conformite.toml absent, illisible ou non conforme : aucune règle ne peut être jugée."""


@dataclass(frozen=True)
class Constat:
    regle: str
    fichier: str
    objet: str
    message: str

    def texte(self):
        return f'{self.regle} {self.fichier} ({self.objet}) : {self.message}'


def _texte(table, cle, ou):
    valeur = table.get(cle)
    if type(valeur) is not str or not valeur.strip():
        raise ConformiteInvalide(f'conformite.toml : {ou} sans « {cle} » (texte non vide).')
    return valeur.strip()


def charger(racine):
    """{profil, sorties: {nom: decision}, exceptions: [{regle, fichier, objet, raison, decision}]}; ConformiteInvalide sinon."""
    chemin = Path(racine) / 'conformite.toml'
    try:
        brut = tomllib.loads(chemin.read_text(encoding='utf-8'))
    except FileNotFoundError:
        raise ConformiteInvalide('conformite.toml absent à la racine de l’agent : profil, sorties et exceptions y sont écrits.') from None
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ConformiteInvalide(f'conformite.toml illisible : {exc}') from None
    inconnues = set(brut) - {'profil', 'sortie', 'exception'}
    if inconnues:
        raise ConformiteInvalide(f'conformite.toml : clés inconnues {", ".join(sorted(inconnues))}.')
    profil = brut.get('profil')
    if profil == 'versionne':
        raise ConformiteInvalide("conformite.toml : le profil versionné vient à l'étape 5, avec l'écriture versionnée du socle.")
    if profil not in PROFILS:
        raise ConformiteInvalide(f'conformite.toml : profil {profil!r}; attendu {" ou ".join(PROFILS)}.')
    sorties = {}
    for i, table in enumerate(brut.get('sortie', []), 1):
        if not isinstance(table, dict) or set(table) - {'nom', 'raison', 'decision'}:
            raise ConformiteInvalide(f'conformite.toml : sortie {i} non conforme (nom, raison, decision).')
        nom = _texte(table, 'nom', f'la sortie {i}')
        if nom not in SORTIES or nom in sorties:
            raise ConformiteInvalide(f'conformite.toml : sortie {nom!r} inconnue ou en double ({", ".join(SORTIES)}).')
        _texte(table, 'raison', f'la sortie {nom}')
        sorties[nom] = _texte(table, 'decision', f'la sortie {nom}')
    exceptions = []
    for i, table in enumerate(brut.get('exception', []), 1):
        if not isinstance(table, dict) or set(table) - {'regle', 'fichier', 'objet', 'raison', 'decision'}:
            raise ConformiteInvalide(f'conformite.toml : exception {i} non conforme (regle, fichier, objet, raison, decision).')
        une = {cle: _texte(table, cle, f"l'exception {i}") for cle in ('regle', 'fichier', 'objet', 'raison', 'decision')}
        if une['regle'] not in REGLES:
            raise ConformiteInvalide(f"conformite.toml : l'exception {i} vise la règle inconnue {une['regle']!r}.")
        exceptions.append(une)
    return {'profil': profil, 'sorties': sorties, 'exceptions': exceptions}


def _relatif(racine, chemin):
    return chemin.relative_to(racine).as_posix()


def _arbre(chemin):
    return ast.parse(chemin.read_text(encoding='utf-8'), filename=str(chemin))


def _objets(arbre):
    """(objet, noeud) de chaque noeud du fichier : l'objet est le nom de la fonction, de la classe (Classe.methode) ou de la
    variable de module qui le contient, sinon « <module> »."""
    def dans(noeud, nom):
        for enfant in ast.walk(noeud):
            yield nom, enfant
    for haut in arbre.body:
        if isinstance(haut, ast.ClassDef):
            yield haut.name, haut
            for corps in haut.body:
                nom = f'{haut.name}.{corps.name}' if isinstance(corps, (ast.FunctionDef, ast.AsyncFunctionDef)) else haut.name
                yield from dans(corps, nom)
            for base in haut.bases:
                yield from dans(base, haut.name)
        elif isinstance(haut, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield from dans(haut, haut.name)
        elif isinstance(haut, (ast.Assign, ast.AnnAssign)):
            cibles = haut.targets if isinstance(haut, ast.Assign) else [haut.target]
            nom = next((c.id for c in cibles if isinstance(c, ast.Name)), '<module>')
            yield from dans(haut, nom)
        else:
            yield from dans(haut, '<module>')


def _importes(noeud):
    if isinstance(noeud, ast.Import):
        return [a.name for a in noeud.names]
    if isinstance(noeud, ast.ImportFrom) and not noeud.level:
        return [noeud.module or '']
    return []


def _chaines(noeud):
    """Les morceaux constants d'un chemin, dans l'ordre : écrit avec / ou + (Path.home() / '_PersonalAgents' / 'smith'), ou par
    os.path.join, joinpath, Path(...)."""
    if isinstance(noeud, ast.BinOp) and isinstance(noeud.op, (ast.Div, ast.Add)):
        return _chaines(noeud.left) + _chaines(noeud.right)
    if isinstance(noeud, ast.Call) and getattr(noeud.func, 'attr', getattr(noeud.func, 'id', '')) in CHEMINS:
        base = _chaines(noeud.func.value) if isinstance(noeud.func, ast.Attribute) and noeud.func.attr == 'joinpath' else []
        return base + [m for a in noeud.args for m in _chaines(a)]
    if isinstance(noeud, ast.Constant) and isinstance(noeud.value, str):
        return [noeud.value]
    return []


def _commande(noeud):
    """Les éléments constants d'un appel subprocess.run([...]) écrit en liste littérale (None pour un élément calculé), sinon []."""
    if isinstance(noeud, ast.Call) and getattr(noeud.func, 'attr', getattr(noeud.func, 'id', '')) in APPELS and noeud.args \
            and isinstance(noeud.args[0], ast.List):
        return [e.value if isinstance(e, ast.Constant) and isinstance(e.value, str) else None for e in noeud.args[0].elts]
    return []


def _tete_de_commande(noeud):
    """Le programme d'un appel subprocess.run([...]) écrit en liste littérale, sinon None."""
    commande = _commande(noeud)
    return commande[0] if commande else None


def _sortie_de_commande(commande):
    """Pourquoi cette commande peut sortir du Mac, sinon None : un programme de sortie à n'importe quelle place (sh -c « curl … »,
    env curl), un interpréteur en tête, ou git qui parle à un dépôt distant."""
    if not commande or commande[0] is None:
        return None
    if Path(commande[0]).name in INTERPRETES:
        return f'interpréteur « {commande[0]} » en tête : le programme lancé ne se voit pas'
    for element in commande:
        for mot in (element or '').split():
            if Path(mot).name in PROGRAMMES_SORTIE:
                return f'programme qui sort du Mac « {mot} »'
    if Path(commande[0]).name == 'git' and any(e in GIT_SORTIE for e in commande[1:]):
        return 'git vers un dépôt distant'
    return None


def _textes_bin(racine):
    """(relatif, ligne) des programmes de bin/, commentaires exclus."""
    for programme in sorted((racine / 'bin').glob('*')):
        if programme.is_file() and not programme.is_symlink():
            relatif = _relatif(racine, programme)
            for ligne in programme.read_text(encoding='utf-8', errors='replace').splitlines()[1:]:
                if ligne.strip() and not ligne.lstrip().startswith('#'):
                    yield relatif, ligne


class Agent:
    """Ce que les règles lisent d'un agent : sa racine, son paquet, conformite.toml, ses fichiers et sa déclaration."""

    def __init__(self, racine, module):
        self.racine, self.module = Path(racine).resolve(), module
        self.nom, self.NOM = module, module.upper()
        self.config = charger(self.racine)
        self.paquet = self.racine / module
        self._arbres = {}

    def fichiers(self, *dossiers, profond=True):
        trouves = []
        for dossier in dossiers:
            base = self.racine / dossier
            motif = base.rglob('*.py') if profond else base.glob('*.py')
            trouves += [p for p in motif if '__pycache__' not in p.relative_to(self.racine).parts]
        return sorted(trouves)

    def arbre(self, chemin):
        if chemin not in self._arbres:
            self._arbres[chemin] = _arbre(chemin)
        return self._arbres[chemin]

    def noeuds(self, *dossiers, profond=True):
        """(fichier relatif, objet, noeud) de tout le code de ces dossiers."""
        for chemin in self.fichiers(*dossiers, profond=profond):
            for objet, noeud in _objets(self.arbre(chemin)):
                yield _relatif(self.racine, chemin), objet, noeud

    def declaration(self):
        paquet = importlib.import_module(self.module)
        fichier = Path(getattr(paquet, '__file__', None) or '/').resolve()
        if not fichier.is_relative_to(self.paquet):
            raise ValueError(f'paquet « {self.module} » chargé depuis {fichier.parent}, hors de la racine jugée ({self.racine}) : '
                             'PYTHONPATH vise une autre copie.')
        return declaration.actuelle()

    def autres(self):
        return [a for a in declaration.PAR if a != self.nom]


# Chaque règle rend ses constats, avant les exceptions.

def c1_base(agent):
    out = []
    d = agent.declaration()
    if d.nom != agent.nom:
        out.append(Constat('C1', 'declaration', 'nom', f'déclaré « {d.nom} », paquet « {agent.nom} » : un agent, un nom.'))
    reel = Path.home() / '_PersonalAgents' / 'donnees' / agent.nom
    if tuple(Path(c) for c in d.dossiers_reels) != (reel,):
        out.append(Constat('C1', 'declaration', 'dossiers_reels', f'le seul dossier réel est {reel}.'))
    donnees = Path(d.dossier_donnees()).resolve()
    for base in d.bases():
        if not Path(base).resolve().is_relative_to(donnees):
            out.append(Constat('C1', 'declaration', 'bases', f'base {base} hors de dossier_donnees() ({donnees}).'))
    autres = agent.autres()
    segment = re.compile(r'(?:_PersonalAgents|donnees)/(' + '|'.join(autres) + r')(?:/|$)')
    for fichier, objet, noeud in agent.noeuds(agent.module):
        morceaux = _chaines(noeud) if isinstance(noeud, (ast.BinOp, ast.Call)) else []
        if isinstance(noeud, ast.Call) and not {'_PersonalAgents', 'donnees'} & set(morceaux):
            morceaux = []  # un appel ne vise un autre agent que par le chemin des agents
        vise = next((m for m in morceaux if m in autres), None) if morceaux else None
        if vise is None and isinstance(noeud, ast.Constant) and isinstance(noeud.value, str):
            trouve = segment.search(noeud.value)
            vise = trouve.group(1) if trouve else None
        if vise is not None:
            out.append(Constat('C1', fichier, objet, f'chemin vers le dépôt ou les données de « {vise} » : une base, un propriétaire.'))
    for relatif, ligne in _textes_bin(agent.racine):
        trouve = segment.search(ligne)
        if trouve:
            out.append(Constat('C1', relatif, 'programme', f'chemin vers le dépôt ou les données de « {trouve.group(1)} ».'))
    return _uniques(out)


def c2_profil(agent):
    out = []
    d = agent.declaration()
    profil = agent.config['profil']
    for nom in d.noms_outils():
        info = d.outil(nom)
        if info is None:
            out.append(Constat('C2', 'declaration', nom, 'outil sans déclaration (outil(nom) rend None).'))
            continue
        if profil == 'lecture':
            if info.type != 'lecture':
                out.append(Constat('C2', 'declaration', nom, f'outil « {info.type} » dans un agent en profil lecture.'))
            continue
        if info.type in ('preparation', 'lot'):
            if info.lot not in d.regles.lots:
                out.append(Constat('C2', 'declaration', nom, f'outil de lot sans son lot dans regles.lots ({info.lot}).'))
        elif info.type != 'lecture' and not nom.startswith('WR_SAVE_') and nom not in d.regles.hors_wr:
            out.append(Constat('C2', 'declaration', nom, 'écriture sans le préfixe WR_SAVE_ ni hors_wr déclaré.'))
    declares = set(d.noms_outils())
    for fichier, objet, nom in _outils_servis(agent):
        if nom not in declares:
            out.append(Constat('C2', fichier, objet, f'outil servi « {nom} » absent du registre : ni type, ni lot, ni profil.'))
    if profil == 'lecture' and (d.regles.lots or d.regles.hors_wr):
        out.append(Constat('C2', 'declaration', 'regles', 'profil lecture : ni lot ni écriture hors WR_SAVE_.'))
    if profil == 'lot':
        if not d.regles.lots:
            out.append(Constat('C2', 'declaration', 'regles', 'profil lot sans lot déclaré.'))
        noms = set(d.noms_outils())
        for nom in d.regles.hors_wr:
            if nom not in noms:
                out.append(Constat('C2', 'declaration', nom, 'hors_wr pour un outil que l’agent ne déclare pas.'))
    return out


def _outils_servis(agent):
    """(fichier, objet, nom) des outils que le code sert : les 'name' des listes tools et les clés des dicts handlers."""
    for fichier, objet, noeud in agent.noeuds(agent.module):
        if not isinstance(noeud, (ast.Assign, ast.AnnAssign)):
            continue
        cibles = noeud.targets if isinstance(noeud, ast.Assign) else [noeud.target]
        noms = {getattr(c, 'id', getattr(c, 'attr', '')).lower() for c in cibles}
        if noeud.value is None:
            continue
        if 'tools' in noms or 'outils_servis' in noms:
            for d in ast.walk(noeud.value):
                if isinstance(d, ast.Dict):
                    for k, v in zip(d.keys, d.values):
                        if isinstance(k, ast.Constant) and k.value == 'name' and isinstance(v, ast.Constant) and isinstance(v.value, str):
                            yield fichier, objet, v.value
        if 'handlers' in noms and isinstance(noeud.value, ast.Dict):
            for k in noeud.value.keys:
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    yield fichier, objet, k.value


def c3_sorties(agent):
    out, drive = [], []
    d = agent.declaration()
    if not tuple(d.dossiers_sortie()):
        out.append(Constat('C3', 'declaration', 'dossiers_sortie', 'au moins un dossier de sortie déclaré.'))
    for fichier, objet, noeud in agent.noeuds(agent.module):
        for nom in _importes(noeud):
            if nom.split('.')[0] in RESEAU or nom.startswith(RESEAU_SOUS):
                out.append(Constat('C3', fichier, objet, f'import réseau « {nom} » : rien ne sort du Mac sans décision.'))
        pourquoi = _sortie_de_commande(_commande(noeud))
        if pourquoi is None and isinstance(noeud, ast.Constant) and isinstance(noeud.value, str) and noeud.value.startswith('/') \
                and Path(noeud.value).name in PROGRAMMES_SORTIE:
            pourquoi = f'programme qui sort du Mac « {noeud.value} »'
        if pourquoi is not None:
            out.append(Constat('C3', fichier, objet, pourquoi + '.'))
        if isinstance(noeud, ast.Constant) and isinstance(noeud.value, str):
            if DRIVE.search(noeud.value):
                drive.append(Constat('C3', fichier, objet, 'Google Drive (ou un autre nuage) est une sortie du Mac : la lister dans '
                                                         'conformite.toml.'))
    for relatif, ligne in _textes_bin(agent.racine):
        trouve = BIN_SORTIE.search(ligne)
        if trouve:
            out.append(Constat('C3', relatif, 'programme', f'programme qui sort du Mac ou interpréteur « {trouve.group(1)} ».'))
        if DRIVE.search(ligne):
            drive.append(Constat('C3', relatif, 'programme', 'dossier synchronisé dans un nuage : le lister dans conformite.toml.'))
    if 'drive' not in agent.config['sorties']:
        out += drive
    elif not drive:
        out.append(Constat('C3', 'conformite.toml', 'drive', 'sortie listée que le code ne fait plus : la retirer.'))
    return _uniques(out)


def c4_autrui(agent):
    out = []
    d = agent.declaration()
    if not d.textes.notice_autrui.strip():
        out.append(Constat('C4', 'declaration', 'notice_autrui', "texte vide : le modèle doit savoir ce qui vient d'autrui."))
    serveurs = 0
    for fichier, objet, noeud in agent.noeuds(agent.module):
        if isinstance(noeud, ast.ClassDef) and any(
                (isinstance(b, ast.Name) and b.id == 'ServeurBase') or (isinstance(b, ast.Attribute) and b.attr == 'ServeurBase')
                for b in noeud.bases):
            serveurs += 1
        if any(n.split('.')[:2] == ['socle', 'signal_texte'] for n in _importes(noeud)) or (
                isinstance(noeud, ast.ImportFrom) and noeud.module == 'socle' and any(a.name == 'signal_texte' for a in noeud.names)):
            out.append(Constat('C4', fichier, objet, 'le signal du texte d’autrui s’écrit par le cadre MCP du socle, jamais directement.'))
        if isinstance(noeud, ast.Constant) and isinstance(noeud.value, str) and 'texte-autrui' in noeud.value:
            out.append(Constat('C4', fichier, objet, 'fichier du signal nommé directement.'))
        if isinstance(noeud, (ast.Assign, ast.keyword)) and _sans_autrui_calcule(noeud):
            out.append(Constat('C4', fichier, objet, 'sans_autrui calculé : D090 veut une liste fermée, chaque outil jugé à la main '
                                                     '(un tuple de noms écrits).'))
    if not serveurs:
        out.append(Constat('C4', agent.module, 'serveur', 'aucun serveur sur socle.mcp.ServeurBase : le signal ne serait pas tenu.'))
    return _uniques(out)


def _sans_autrui_calcule(noeud):
    if isinstance(noeud, ast.Assign):
        nomme = any(getattr(c, 'id', getattr(c, 'attr', '')).lower() == 'sans_autrui' for c in noeud.targets)
    else:
        nomme = noeud.arg == 'sans_autrui'
    if not nomme:
        return False
    valeur = noeud.value
    if isinstance(valeur, ast.Call):
        return bool(valeur.args or valeur.keywords)  # tuple(OUTILS), oui; set() vide, rempli ensuite depuis une liste écrite, non
    if isinstance(valeur, (ast.Tuple, ast.List, ast.Set)):
        return not all(isinstance(e, ast.Constant) and isinstance(e.value, str) for e in valeur.elts)
    return isinstance(valeur, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp, ast.BinOp, ast.Starred))


def c5_nuit(agent):
    out = []
    taches = sorted((agent.racine / 'outils').glob(f'*.{agent.nom}.sauvegarde.plist'))
    if len(taches) != 1:
        return [Constat('C5', 'outils', 'tache', f'un seul modèle de tâche de nuit, outils/<préfixe>.{agent.nom}.sauvegarde.plist.')]
    label, relatif = taches[0].stem, _relatif(agent.racine, taches[0])
    try:
        tache = plistlib.loads(taches[0].read_bytes())
    except (OSError, plistlib.InvalidFileException, ValueError):
        return [Constat('C5', relatif, 'tache', 'modèle de tâche de nuit illisible.')]
    if tache.get('Label') != label or not TROUSSEAU.match(label):
        out.append(Constat('C5', relatif, 'Label', f'attendu {label}, le nom du fichier.'))
    programme = tache.get('ProgramArguments') or ['']
    if len(programme) != 1 or not str(programme[0]).startswith('/') \
            or not str(programme[0]).endswith(f'/bin/{agent.nom}-sauvegarde-quotidienne'):
        out.append(Constat('C5', relatif, 'ProgramArguments', f'chemin complet de bin/{agent.nom}-sauvegarde-quotidienne seulement.'))
    permises = {f'{agent.NOM}_SAUVEGARDE_{d}' for d in DESTINATIONS[agent.config['profil']]}
    for cle in sorted(set(tache.get('EnvironmentVariables') or {}) - permises):
        out.append(Constat('C5', relatif, cle, f'variable hors des destinations permises au profil {agent.config["profil"]}.'))
    script = agent.paquet / 'scripts' / 'sauvegarde_quotidienne.py'
    if not script.is_file():
        out.append(Constat('C5', _relatif(agent.racine, script), 'script', 'la nuit de l’agent est absente.'))
    service = None
    for fichier, objet, noeud in agent.noeuds(agent.module):
        if isinstance(noeud, ast.Assign) and objet == 'KEYCHAIN_SERVICE' and isinstance(noeud.value, ast.Constant):
            service = noeud.value.value
        if isinstance(noeud, ast.Constant) and isinstance(noeud.value, str):
            if TROUSSEAU.match(noeud.value) and noeud.value != label:
                out.append(Constat('C5', fichier, objet, f'trousseau d’un autre agent « {noeud.value} ».'))
            trouve = re.fullmatch(r'([A-Z]+)_SAUVEGARDE_[A-Z]+', noeud.value)
            if trouve and trouve.group(1) != agent.NOM:
                out.append(Constat('C5', fichier, objet, f'destination de nuit d’un autre agent « {noeud.value} ».'))
    if script.is_file() and service != label:
        out.append(Constat('C5', _relatif(agent.racine, script), 'KEYCHAIN_SERVICE', f'la phrase de passe est au trousseau sous {label}.'))
    return _uniques(out)


def c6_socle(agent):
    copie, chemin = agent.racine / 'socle', agent.racine / 'socle.epingle.json'
    try:
        ecarts = empreinte.comparer(copie, empreinte.lire_epingle(chemin))
    except (OSError, ValueError) as exc:
        return [Constat('C6', 'socle.epingle.json', 'epingle', str(exc))]
    out = [Constat('C6', 'socle', e, f'copie différente de l’épingle : bin/{agent.nom}-socle-epingler, jamais une retouche.')
           for e in ecarts]
    import socle
    if Path(socle.__file__).resolve().parent != copie.resolve():
        out.append(Constat('C6', 'socle', 'chemin', f'le socle chargé n’est pas la copie épinglée ({Path(socle.__file__).parent}).'))
    return out


def c7_donnees(agent):
    out = []
    chemins = agent.paquet / 'chemins.py'
    if not chemins.is_file() or not any(isinstance(n, ast.ImportFrom) and n.module == 'socle.chemins'
                                         and any(a.name == 'private_folder' for a in n.names) for n in ast.walk(agent.arbre(chemins))):
        out.append(Constat('C7', f'{agent.module}/chemins.py', 'private_folder', 'les dossiers se créent par socle.chemins.private_folder.'))
        return out
    module = importlib.import_module(f'{agent.module}.chemins')
    reel = Path.home() / '_PersonalAgents' / 'donnees' / agent.nom
    if Path(getattr(module, 'REAL_HOME', '')) != reel:
        out.append(Constat('C7', f'{agent.module}/chemins.py', 'REAL_HOME', f'attendu {reel}.'))
    variable = f'{agent.NOM}_HOME'
    avant = os.environ.get(variable)
    try:
        os.environ[variable] = str(Path.home() / '_PersonalAgents' / 'donnees' / f'{agent.nom}-essai-conformite')
        if Path(module.home()) != reel:
            out.append(Constat('C7', f'{agent.module}/chemins.py', 'home', f'{variable} sous ~/_PersonalAgents doit être ignorée.'))
    finally:
        if avant is None:
            os.environ.pop(variable, None)
        else:
            os.environ[variable] = avant
    try:
        verifier = importlib.import_module(f'{agent.module}.scripts.verifier')
        with tempfile.TemporaryDirectory(prefix='conformite-donnees-') as dossier:
            dossier = Path(dossier) / 'donnees'
            dossier.mkdir(mode=0o700)
            fichier = dossier / 'essai.db'
            fichier.write_bytes(b'')
            fichier.chmod(0o600)
            sain = verifier.donnees(dossier)['ok']
            fichier.chmod(0o644)
            ouvert = verifier.donnees(dossier)['ok']
        if not sain or ouvert:
            out.append(Constat('C7', f'{agent.module}/scripts/verifier.py', 'donnees', 'le vérificateur doit signaler un fichier en 644.'))
    except (ImportError, AttributeError, KeyError, TypeError) as exc:
        out.append(Constat('C7', f'{agent.module}/scripts/verifier.py', 'donnees', f'contrôle des droits introuvable ({exc}).'))
    return out


def c8_dependances(agent):
    out = []
    locaux = {p.stem for p in (agent.racine / 'tests').glob('*.py')}
    for dossier, permis in ((agent.module, set()), ('tests', locaux)):
        for fichier, objet, noeud in agent.noeuds(dossier):
            for nom in _importes(noeud):
                tete = nom.split('.')[0]
                if tete not in sys.stdlib_module_names and tete not in (agent.module, 'socle') and tete not in permis:
                    out.append(Constat('C8', fichier, objet, f'import « {nom} » hors bibliothèque standard (D108).'))
    for nom in DEPENDANCES:
        if (agent.racine / nom).exists():
            out.append(Constat('C8', nom, 'fichier', 'dépendance déclarée sans décision (D108).'))
    projet = agent.racine / 'pyproject.toml'
    if projet.is_file():
        try:
            projet_toml = tomllib.loads(projet.read_text(encoding='utf-8'))
            dependances = [*projet_toml.get('project', {}).get('dependencies', []),
                           *projet_toml.get('project', {}).get('optional-dependencies', {}),
                           *projet_toml.get('dependency-groups', {})]
        except (OSError, tomllib.TOMLDecodeError, TypeError):
            dependances = ['illisible']
        if dependances:
            out.append(Constat('C8', 'pyproject.toml', 'dependencies', 'dépendance déclarée sans décision (D108).'))
    for paquets in agent.racine.glob('.venv*/lib/python*/site-packages'):
        for entree in sorted(paquets.iterdir()):
            if not VENV_PERMIS.match(entree.name):
                out.append(Constat('C8', _relatif(agent.racine, paquets).split('/')[0], entree.name,
                                   'paquet ou .pth installé dans .venv sans décision (D108).'))
    return _uniques(out)


def c9_tests(agent):
    out = []
    tests = agent.racine / 'tests'
    for chemin in sorted(tests.glob('test_*.py')):
        relatif, arbre = _relatif(agent.racine, chemin), agent.arbre(chemin)
        principal = next((i for i, n in enumerate(arbre.body) if isinstance(n, ast.If) and '__name__' in ast.unparse(n.test)), None)
        if principal is None or 'unittest.main' not in ast.unparse(arbre.body[principal]):
            out.append(Constat('C9', relatif, '__main__', "sans if __name__ == '__main__': unittest.main(), run_all n'y lance rien."))
            continue
        avant = [n for n in arbre.body[:principal] if isinstance(n, (ast.ClassDef, ast.Assign))]
        apres = [n for n in arbre.body[principal + 1:] if isinstance(n, (ast.ClassDef, ast.Assign, ast.FunctionDef))]
        if not avant:
            out.append(Constat('C9', relatif, '<module>', 'aucune classe de tests avant if __name__.'))
        for n in apres:
            out.append(Constat('C9', relatif, getattr(n, 'name', '<module>'), 'déclaré après if __name__ : jamais lancé.'))
    run_all = tests / 'run_all.py'
    if not run_all.is_file():
        out.append(Constat('C9', 'tests/run_all.py', 'fichier', 'run_all.py lance chaque fichier dans un monde fictif.'))
    else:
        retirees, ecrit, utilisee = set(), False, False
        for objet, noeud in _objets(agent.arbre(run_all)):
            if isinstance(noeud, ast.Name) and noeud.id == 'RETIREES' and isinstance(noeud.ctx, ast.Load):
                utilisee = True
            if objet == 'RETIREES' and isinstance(noeud, ast.Constant) and isinstance(noeud.value, str):
                retirees.add(noeud.value)
            if (isinstance(noeud, ast.Call) and getattr(noeud.func, 'id', getattr(noeud.func, 'attr', '')) == 'ecrire_resultat'):
                ecrit = True
        variables = {n.value for _, _, n in agent.noeuds(agent.module)
                     if isinstance(n, ast.Constant) and isinstance(n.value, str) and re.fullmatch(rf'{agent.NOM}_[A-Z0-9_]+', n.value)}
        variables.add(f'{agent.NOM}_HOME')
        for variable in sorted(variables - retirees):
            out.append(Constat('C9', 'tests/run_all.py', 'RETIREES', f'{variable} mène aux vraies données : jamais héritée.'))
        if not ecrit:
            out.append(Constat('C9', 'tests/run_all.py', 'ecrire_resultat', f'le verdict de la suite va dans {RESULTAT} (smith-verifier).'))
        if not utilisee:
            out.append(Constat('C9', 'tests/run_all.py', 'RETIREES', 'déclarée mais jamais lue : les variables ne sont pas retirées.'))
    suite = tests / 'test_conformite.py'
    if not suite.is_file() or not any(isinstance(n, ast.Call) and getattr(n.func, 'id', getattr(n.func, 'attr', '')) == 'cas'
                                      for n in ast.walk(agent.arbre(suite))):
        out.append(Constat('C9', 'tests/test_conformite.py', 'cas', 'la suite de conformité du socle (conformite.cas) y est lancée.'))
    for fichier, objet, noeud in agent.noeuds(agent.module, 'tests'):
        if not isinstance(noeud, ast.Call):
            continue
        nom = getattr(noeud.func, 'attr', getattr(noeud.func, 'id', ''))
        tete = _tete_de_commande(noeud)
        if tete is not None and not tete.startswith('/'):
            out.append(Constat('C9', fichier, objet, f'programme « {tete} » sans chemin complet.'))
        if nom in ('system', 'popen') and isinstance(noeud.func, ast.Attribute) and getattr(noeud.func.value, 'id', '') == 'os':
            out.append(Constat('C9', fichier, objet, f'os.{nom} passe par un shell.'))
        if any(k.arg == 'shell' and isinstance(k.value, ast.Constant) and k.value.value for k in noeud.keywords):
            out.append(Constat('C9', fichier, objet, 'shell=True : un programme par son chemin complet, jamais par un shell.'))
    for programme in sorted((agent.racine / 'bin').glob('*')):
        texte = programme.read_text(encoding='utf-8', errors='replace')
        relatif = _relatif(agent.racine, programme)
        if not texte.startswith('#!/'):
            out.append(Constat('C9', relatif, 'shebang', 'interpréteur par son chemin complet.'))
        if PYTHON_NU.search('\n'.join(l for l in texte.splitlines()[1:] if not l.lstrip().startswith('#'))):
            out.append(Constat('C9', relatif, 'python', 'Python nu : celui du dépôt, $ROOT/.venv/bin/python.'))
    return _uniques(out)


def _uniques(constats):
    vus, out = set(), []
    for c in constats:
        if (c.regle, c.fichier, c.objet, c.message) not in vus:
            vus.add((c.regle, c.fichier, c.objet, c.message))
            out.append(c)
    return out


JUGES = {'C1': c1_base, 'C2': c2_profil, 'C3': c3_sorties, 'C4': c4_autrui, 'C5': c5_nuit, 'C6': c6_socle, 'C7': c7_donnees,
         'C8': c8_dependances, 'C9': c9_tests}


def _couvre(exception, constat):
    return exception['regle'] == constat.regle and exception['fichier'] == constat.fichier \
        and exception['objet'] in (constat.objet, TOUT)


def bilan(racine, module):
    """({règle: [constats restants]}, [exceptions qui ne servent plus]); ConformiteInvalide si conformite.toml ne va pas."""
    agent = Agent(racine, module)
    restants, servies = {}, set()
    for regle, juge in JUGES.items():
        try:
            constats = juge(agent)
        except ConformiteInvalide:
            raise
        except Exception as exc:  # une règle qui ne peut pas juger échoue, jamais ne passe
            constats = [Constat(regle, agent.module, 'jugement', f'règle impossible à juger : {type(exc).__name__}: {exc}')]
        restants[regle] = []
        for c in constats:
            couvrantes = [i for i, e in enumerate(agent.config['exceptions']) if _couvre(e, c)]
            servies.update(couvrantes)
            if not couvrantes:
                restants[regle].append(c)
    inutiles = [e for i, e in enumerate(agent.config['exceptions']) if i not in servies]
    return restants, inutiles


def cas(racine, module):
    """La classe de tests de la suite pour cet agent, une méthode par règle; à placer dans tests/test_conformite.py."""
    racine = Path(racine)

    class Conformite(unittest.TestCase):
        _bilan = None

        @classmethod
        def setUpClass(cls):
            try:
                cls._bilan = bilan(racine, module)
            except ConformiteInvalide as exc:
                cls._bilan = exc

        def juger(self, regle):
            if isinstance(self._bilan, ConformiteInvalide):
                self.fail(str(self._bilan))
            restants = self._bilan[0][regle]
            if restants:
                self.fail(f'{regle} ({REGLES[regle]}) non tenue :\n' + '\n'.join(c.texte() for c in restants)
                          + '\nCorriger le code, ou écrire une exception dans conformite.toml (regle, fichier, objet, raison, decision).')

        def test_exceptions(self):
            if isinstance(self._bilan, ConformiteInvalide):
                self.fail(str(self._bilan))
            inutiles = self._bilan[1]
            if inutiles:
                self.fail('Exceptions qui ne servent plus, à retirer de conformite.toml :\n'
                          + '\n'.join(f"{e['regle']} {e['fichier']} ({e['objet']})" for e in inutiles))

    for regle, juge in JUGES.items():
        nom = f'test_{juge.__name__}'
        setattr(Conformite, nom, lambda self, regle=regle: self.juger(regle))
        getattr(Conformite, nom).__name__ = nom
    Conformite.__name__ = Conformite.__qualname__ = f'Conformite{module.capitalize()}'
    return Conformite


def _git(racine, *commande):
    try:
        fait = subprocess.run([GIT, '-C', str(racine), *commande], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return fait.stdout if fait.returncode == 0 else None


def ecrire_resultat(racine, vert, lances=None, maintenant=None):
    """.conformite.json à la racine (hors Git), en 600 et d'un coup : version du socle épinglée, commit, arbre propre, verdict,
    date et exceptions. Aucune donnée métier; lu par smith-verifier, qui ne lance jamais la suite (R5).

    lances : {fichier de tests : nombre de tests passés}. Vert seulement si la suite de conformité a passé en entier
    (test_conformite.py, une méthode par règle et celle des exceptions) : sans elle, une suite verte ne prouve rien."""
    racine = Path(racine)
    vert = vert and bool(lances) and lances.get('test_conformite.py') == len(JUGES) + 1
    try:
        version = empreinte.lire_epingle(racine / 'socle.epingle.json')['version']
    except ValueError:
        version = None
    try:
        exceptions = [{k: e[k] for k in ('regle', 'fichier', 'objet', 'decision')} for e in charger(racine)['exceptions']]
    except ConformiteInvalide:
        exceptions, vert = [], False
    commit = _git(racine, 'rev-parse', 'HEAD')
    etat = _git(racine, 'status', '--porcelain')
    contenu = {'version': 1, 'socle': version, 'commit': commit.strip() if commit else None,
               'propre': etat is not None and not etat.strip(), 'verdict': 'vert' if vert else 'rouge',
               'date': (maintenant or datetime.now(timezone.utc)).strftime('%Y-%m-%dT%H:%M:%SZ'), 'exceptions': exceptions}
    return write_private(racine, RESULTAT, (json.dumps(contenu, ensure_ascii=False, indent=1) + '\n').encode('utf-8'))
