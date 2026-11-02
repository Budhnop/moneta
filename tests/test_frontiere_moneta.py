"""Frontière de Moneta vers Smith (spec 013, T039, récit 3.5) : le code de Moneta ne connaît ni le code ni les données de Smith.

Tout moneta/ et bin/ : aucun import hors de la bibliothèque standard, de moneta et du socle (donc ni core, ni agents, ni scripts,
ni aucun module de Smith); aucun chemin vers dojo.db, donnees/smith ou le dépôt de Smith; aucune variable UGOD_DB; aucune requête
SQL vers une table de Dojo. Les tables visées viennent des migrations de Dojo de Smith (tests/outils_tests.dojo_migre), moins
celles de Finances. Exceptions : la constante PROGRAMME de moneta/lecture_dojo.py, le programme fermé de Smith; et les chemins de
Smith de moneta/scripts/demenager.py (T062), qui déplace par nature les fichiers de donnees/smith/ et lit le commit de Smith dans
son dépôt (contrat demenagement). Même là, aucun import de Smith, aucun dojo.db, aucune requête vers une table de Dojo.
"""
import ast
from contextlib import closing
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from outils_tests import FOLDERS, dojo_migre
from socle.migrations import migrate

ROOT = Path(__file__).resolve().parents[1]
PERMIS = {'moneta', 'socle'}
EXCEPTION = ('moneta/lecture_dojo.py', 'PROGRAMME')
DEMENAGEMENT = 'moneta/scripts/demenager.py'
CHEMINS_DE_SMITH = ('chaîne donnees/smith', 'chemin de Smith ')
CHAINES = ('donnees/smith', 'UGOD_DB')
# Un segment de chemin du dépôt de Smith : « smith » seul, ou son code « assistant ».
SMITH = re.compile(r'(^|/)(smith|assistant)(/|$)')


def tables(db):
    with closing(sqlite3.connect(db)) as con:
        return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}


def tables_de_dojo():
    with tempfile.TemporaryDirectory(prefix='frontiere-') as dossier:
        dojo = tables(dojo_migre(dossier))
        finances = Path(dossier) / 'finances.db'
        migrate(finances, Path(dossier) / 'backups', FOLDERS['finances'])
        return dojo - tables(finances)


def fichiers():
    return sorted([*(ROOT / 'moneta').rglob('*.py'), *(c for c in (ROOT / 'bin').iterdir() if c.is_file())])


def module_de(noeud, nom):
    """Le module absolu d'un « from … import », import relatif compris."""
    if not noeud.level:
        return noeud.module or ''
    parents = Path(nom).parent.parts
    parents = parents[:len(parents) - (noeud.level - 1)]
    return '.'.join([*parents, *([noeud.module] if noeud.module else [])])


def plier(noeud):
    """Le texte d'une chaîne, y compris un f-string ou des morceaux collés par +; None pour le reste."""
    if isinstance(noeud, ast.Constant) and isinstance(noeud.value, str):
        return noeud.value
    if isinstance(noeud, ast.JoinedStr):
        return ' '.join(p for p in map(plier, noeud.values) if p)
    if isinstance(noeud, ast.BinOp) and isinstance(noeud.op, (ast.Add, ast.Div)):
        gauche, droite = plier(noeud.left), plier(noeud.right)
        if gauche is None and droite is None:
            return None
        return (gauche or ' ') + ('/' if isinstance(noeud.op, ast.Div) else '') + (droite or ' ')
    return None


def exception(arbre, nom):
    """Les nœuds de la seule affectation permise (PROGRAMME de lecture_dojo), à ne pas lire."""
    if nom != EXCEPTION[0]:
        return set()
    affectations = [n for n in arbre.body if isinstance(n, ast.Assign)
                    and [getattr(c, 'id', None) for c in n.targets] == [EXCEPTION[1]]]
    return {id(n) for a in affectations[:1] for n in ast.walk(a.value)}


def permis_au_demenagement(violation):
    """Vrai pour un chemin de Smith dans demenager.py seulement."""
    fichier, _, quoi = violation.partition(' : ')
    return fichier == DEMENAGEMENT and quoi.startswith(CHEMINS_DE_SMITH)


def requetes(visees):
    motif = '|'.join(map(re.escape, sorted(visees)))
    nom_sql = rf'''["'`\[(]*(?:\w+\.)?["'`\[]?(?:{motif})\b'''
    liste = r'''(?:[\w."'`\[\]]+(?:\s+(?:AS\s+)?\w+)?\s*,\s*)*'''
    return re.compile(rf'''\b(?:(?:FROM|JOIN)\s+{liste}|(?:INTO|UPDATE|ON)\s+|(?:ALTER|DROP)\s+TABLE\s+(?:IF\s+EXISTS\s+)?'''
                      rf'''|table_x?info\(\s*){nom_sql}''', re.I)


def chaines(valeur, sql):
    trouve = set()
    for chaine in CHAINES:
        if chaine in valeur:
            trouve.add(f'chaîne {chaine}')
    if re.search(r'(^|/)dojo\.db$', valeur.strip()):
        trouve.add('chemin dojo.db')
    if SMITH.search(valeur.strip()):
        trouve.add(f'chemin de Smith {valeur.strip()!r}')
    if re.search(r'\bATTACH\s+(DATABASE\s+)?\S', valeur):
        trouve.add('SQL : ATTACH')
    for requete in sql.findall(valeur):
        trouve.add(f'SQL : {requete}')
    return trouve


def analyser(texte, nom, visees):
    """Les violations de la frontière dans un fichier de Moneta, chacune préfixée de son nom."""
    sql = requetes(visees)
    if not nom.endswith('.py'):
        # Un script de bin/ : du zsh, lu ligne à ligne et mot à mot.
        trouve = set()
        for ligne in texte.splitlines():
            for mot in re.split(r'''[\s"'=;]+''', ligne):
                trouve |= chaines(mot, sql)
            trouve |= {t for t in chaines(ligne, sql) if not t.startswith('chemin')}
        return sorted(f'{nom} : {t}' for t in trouve)
    arbre = ast.parse(texte)
    sautes, trouve = exception(arbre, nom), set()
    for noeud in ast.walk(arbre):
        if isinstance(noeud, ast.Import):
            for alias in noeud.names:
                racine = alias.name.split('.')[0]
                if racine not in PERMIS and racine not in sys.stdlib_module_names:
                    trouve.add(f'import {alias.name}')
        elif isinstance(noeud, ast.ImportFrom):
            module = module_de(noeud, nom)
            if module.split('.')[0] not in PERMIS and module.split('.')[0] not in sys.stdlib_module_names:
                trouve.add(f'import {module}')
        elif isinstance(noeud, ast.Call) and getattr(noeud.func, 'attr', getattr(noeud.func, 'id', '')) in (
                'import_module', '__import__'):
            cible = plier(noeud.args[0]) if noeud.args else None
            if cible is None or cible.split('.')[0] not in PERMIS | set(sys.stdlib_module_names):
                trouve.add(f'import dynamique {cible!r}')
    for noeud in ast.walk(arbre):
        if id(noeud) in sautes:
            continue
        valeur = plier(noeud)
        if valeur is not None:
            trouve |= chaines(valeur, sql)
    return sorted(f'{nom} : {t}' for t in trouve)


class TestFrontiere(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.visees = tables_de_dojo()

    def test_les_tables_visees_viennent_des_migrations_de_smith(self):
        self.assertLessEqual({'paiements', 'familles', 'membres'}, self.visees)
        self.assertNotIn('depenses', self.visees)
        self.assertNotIn('audit_log', self.visees)

    def test_le_perimetre_existe(self):
        noms = {str(c.relative_to(ROOT)) for c in fichiers()}
        self.assertLessEqual({'moneta/lecture_dojo.py', 'moneta/finances/finances.py', 'moneta/outils/factures.py',
                              'moneta/scripts/moneta_mcp.py', 'bin/moneta-mcp', 'bin/moneta-construire-releve'}, noms)

    def test_moneta_ne_connait_pas_smith(self):
        violations = [v for c in fichiers()
                      for v in analyser(c.read_text(encoding='utf-8'), str(c.relative_to(ROOT)), self.visees)
                      if not permis_au_demenagement(v)]
        self.assertEqual(violations, [])

    def test_le_demenagement_ne_permet_que_les_chemins(self):
        texte = (ROOT / DEMENAGEMENT).read_text(encoding='utf-8')
        self.assertTrue(any(permis_au_demenagement(v) for v in analyser(texte, DEMENAGEMENT, self.visees)))
        for ajout in ("from core.paths import dojo_db\n", "p = home() / 'database' / 'dojo.db'\n",
                      "q = 'SELECT * FROM familles'\n", "v = 'UGOD_DB'\n"):
            with self.subTest(ajout=ajout):
                self.assertTrue([v for v in analyser(texte + ajout, DEMENAGEMENT, self.visees) if not permis_au_demenagement(v)])
        self.assertFalse(permis_au_demenagement("moneta/autre.py : chaîne donnees/smith"))

    def test_l_exception_est_seulement_la_constante(self):
        texte = (ROOT / EXCEPTION[0]).read_text(encoding='utf-8')
        self.assertIn("PROGRAMME = Path.home() / '_PersonalAgents' / 'smith' / 'bin' / 'smith-lecture-paiements'", texte)
        for ajout in ("AUTRE = Path.home() / '_PersonalAgents' / 'smith' / 'assistant'\n",
                      "PROGRAMME = Path.home() / '_PersonalAgents' / 'smith'\nPROGRAMME = '/x/smith/dojo.db'\n"):
            with self.subTest(ajout=ajout):
                self.assertTrue(analyser(texte + ajout, EXCEPTION[0], self.visees))
        self.assertTrue(analyser("PROGRAMME = Path.home() / '_PersonalAgents' / 'smith' / 'bin'\n",
                                 'moneta/autre.py', self.visees))

    def test_violations_plantees(self):
        for texte in ("from core.paths import dojo_db\n",
                      "import core.tools\n",
                      "from agents.dojo import paiements\n",
                      "import agents\n",
                      "from scripts.smith_mcp import seed_fictive\n",
                      "import scripts.verifier\n",
                      "from finances import finances\n",
                      "from app.noyau import agents\n",
                      "m = importlib.import_module('core.tools')\n",
                      "m = __import__('agents.dojo')\n",
                      "m = importlib.import_module(nom)\n",
                      "p = home() / 'database' / 'dojo.db'\n",
                      "p = '/un/chemin/dojo.db'\n",
                      "p = Path.home() / '_PersonalAgents/donnees/smith'\n",
                      "p = Path.home() / '_PersonalAgents' / 'donnees' / 'smith' / 'x'\n",
                      "x = os.environ['UGOD_DB']\n",
                      "sys.path.insert(0, str(Path.home() / '_PersonalAgents' / 'smith' / 'assistant'))\n",
                      "p = '~/_PersonalAgents/smith/bin/smith-verifier'\n",
                      "c = 'SELECT montant FROM paiements'\n",
                      "c = 'SELECT 1 FROM p ' + 'LEFT JOIN familles f'\n",
                      "c = f'SELECT {x} FROM membres WHERE 1'\n",
                      "c = 'PRAGMA table_info(paiements)'\n",
                      "c = 'update familles set nom=1'\n",
                      "c = 'SELECT * FROM main.paiements'\n",
                      "c = \"ATTACH 'x' AS d\"\n"):
            with self.subTest(texte=texte):
                violations = analyser(texte, 'moneta/finances/planté.py', self.visees)
                self.assertTrue(violations)
                self.assertTrue(all(v.startswith('moneta/finances/planté.py : ') for v in violations))

    def test_violations_plantees_dans_bin(self):
        for texte in ('exec "$HOME/_PersonalAgents/smith/.venv/bin/python" x\n',
                      'DB="$HOME/_PersonalAgents/donnees/smith/database/dojo.db"\n',
                      'UGOD_DB=/tmp/x exec x\n',
                      'sqlite3 x "SELECT * FROM paiements"\n'):
            with self.subTest(texte=texte):
                violations = analyser(texte, 'bin/moneta-planté', self.visees)
                self.assertTrue(violations)
                self.assertTrue(all(v.startswith('bin/moneta-planté : ') for v in violations))

    def test_ce_qui_reste_permis(self):
        for texte in ("s = 'dojo.db, paiements reçus, par date de paiement'\n",
                      "n = 'Revenus = paiements de dojo.db + autres revenus de finances.db'\n",
                      "lectures = ('smith:paiements_dojo',)\n",
                      "DEPOT_PREFIX = 'smith-depot-'\n",
                      "c = 'SELECT * FROM depenses'\n", "c = 'SELECT * FROM audit_log'\n",
                      "from moneta import chemins\nfrom socle.lots import ToolError\nimport json\n",
                      "from . import chemins\n",
                      "m = importlib.import_module('moneta.finances')\n"):
            with self.subTest(texte=texte):
                self.assertEqual(analyser(texte, 'moneta/finances/permis.py', self.visees), [])


if __name__ == '__main__':
    unittest.main()
