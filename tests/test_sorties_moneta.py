"""Les sorties directes de Moneta (spec 013, T041, R13, EF-014) : son dossier de données, et seulement trois portes vers Drive.

Les trois sorties de D109 : le dépôt d'une facture (outils.factures.deposer), son classement à l'enregistrement
(outils.factures.move_all, que outils.lot défait au besoin par undo) et l'image de la nuit (scripts.sauvegarde_quotidienne.offsite).

1. Un inventaire fermé : chaque appel qui écrit un fichier dans moneta/ est relevé avec sa fonction, et doit y être déjà, avec
   sa raison (données, temporaire, Drive, fictif ou le dépôt lui-même). Un nouvel endroit qui écrit fait échouer le test, fichier
   et fonction nommés : on l'ajoute ici après l'avoir relu, ou on le fait passer par une des trois portes.
2. Sur un Drive fictif, un parcours complet (dépôt, classement, rapport, nuit) ne change que « à classer », le dossier de l'année
   des factures et le dossier de données; le rapport va dans exports/.

Les écritures du socle (journal commun, signal du texte d'autrui, copies privées, migrations) sont gardées par ses propres tests
(R2, R3); ici, sur données fictives, elles restent dans le dossier de données.
"""
import ast
from datetime import date
import hashlib
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from moneta.outils import factures
from socle import confirmation

confirmation.AUTOMATIC = True  # données fictives seulement : aucune fenêtre sur le Mac

ROOT = Path(__file__).resolve().parents[1]
FACTURES = 'moneta/outils/factures.py'
NUIT = 'moneta/scripts/sauvegarde_quotidienne.py'
PORTES = {(FACTURES, 'deposer'), (FACTURES, 'move_all'), (NUIT, 'offsite')}
# Les aides des portes vers Drive : chacune n'est appelée que par ses portes.
AIDES = {(FACTURES, '_year'): {(FACTURES, 'move_all')},
         (FACTURES, 'undo'): {(FACTURES, 'move_all'), ('moneta/outils/lot.py', 'undo')},
         # La rétention des images de la nuit, à la destination de Drive et sur le disque : celles de Moneta seulement.
         (NUIT, 'prune_images'): {(NUIT, 'retention'), (NUIT, 'to_disk')}}
INVENTAIRE = {
    # Le dossier de données (MONETA_HOME) : la base, et les rapports dans exports/.
    ('moneta/finances/finances.py', 'connect'): 'données',
    ('moneta/outils/rapport.py', 'write_report'): 'données',
    ('moneta/scripts/migrer.py', 'main'): 'données',
    ('moneta/chemins.py', 'factures_import'): 'données',
    ('moneta/outils/serveur.py', '__init__'): 'données',
    ('moneta/outils/serveur.py', 'deposit_receipt'): 'données',
    # La nuit (T064) : copie locale, journal, registre des pièces et rétention des copies, dans le dossier de données.
    ('moneta/scripts/sauvegarde_quotidienne.py', 'local_copies'): 'données',
    ('moneta/scripts/sauvegarde_quotidienne.py', 'remove_leftovers'): 'données',
    ('moneta/scripts/sauvegarde_quotidienne.py', 'prune_daily'): 'données',
    ('moneta/scripts/sauvegarde_quotidienne.py', 'prune_copies'): 'données',
    ('moneta/scripts/sauvegarde_quotidienne.py', 'log_path'): 'données',
    ('moneta/scripts/sauvegarde_quotidienne.py', 'log'): 'données',
    ('moneta/scripts/sauvegarde_quotidienne.py', 'registry'): 'données',
    ('moneta/scripts/sauvegarde_quotidienne.py', 'save_registry'): 'données',
    # Le contenu de l'image, préparé dans un dossier temporaire avant d'être chiffré (socle.offsite_backup.create_backup).
    ('moneta/scripts/sauvegarde_quotidienne.py', 'stage'): 'temporaire',
    ('moneta/outils/csv_export.py', 'export_csv'): 'temporaire',
    # Le dossier temporaire du Mac, effacé en sortie (copies privées pour sips et le lecteur de relevés).
    (FACTURES, 'preview'): 'temporaire',
    (FACTURES, '_private_copy'): 'temporaire',
    (FACTURES, '_check_readable'): 'temporaire',
    (FACTURES, '_convert'): 'temporaire',
    (FACTURES, 'purge_deposit_copies'): 'temporaire',
    ('moneta/finances/releves/extraction.py', 'pages'): 'temporaire',
    # Drive : les portes et leurs aides.
    (FACTURES, 'deposer'): 'Drive',
    (FACTURES, 'move_all'): 'Drive',
    (FACTURES, '_year'): 'Drive',
    (FACTURES, 'undo'): 'Drive',
    (NUIT, 'prune_images'): 'Drive',
    # Le monde de bin/moneta-mcp --fictif, dans un dossier temporaire.
    ('moneta/scripts/moneta_mcp.py', 'serveur_fictif'): 'fictif',
    ('moneta/scripts/moneta_mcp.py', 'main'): 'fictif',
    # bin/moneta-demenager, lancé par Ugo dans le terminal (T062) : il déplace des fichiers entre donnees/smith/, donnees/moneta/
    # et le journal commun, jamais ailleurs; --repeter travaille sur un dossier fictif et des copies temporaires.
    ('moneta/scripts/demenager.py', '_creer'): 'déménagement',
    ('moneta/scripts/demenager.py', '_deplacer'): 'déménagement',
    ('moneta/scripts/demenager.py', '_poser'): 'déménagement',  # renommage sans écraser (revue T074, R1)
    ('moneta/scripts/demenager.py', '_journal_commence'): 'fictif',
    ('moneta/scripts/demenager.py', '_repeter_fusion'): 'fictif',
    ('moneta/scripts/demenager.py', 'go'): 'fictif',  # la cible qui apparaît pendant le mot, dans la répétition
    ('moneta/scripts/demenager.py', 'journal_illisible'): 'fictif',
    ('moneta/scripts/demenager.py', 'lien_d_arrivee'): 'fictif',
    ('moneta/scripts/demenager.py', '_repeter_refus'): 'fictif',
    ('moneta/scripts/demenager.py', 'essai'): 'fictif',
    ('moneta/scripts/demenager.py', 'sans_derniere_migration'): 'fictif',
    ('moneta/scripts/demenager.py', '_repeter_echecs'): 'fictif',
    ('moneta/scripts/demenager.py', 'copie'): 'fictif',
    ('moneta/scripts/demenager.py', '_ecriture_fictive'): 'fictif',
    # bin/moneta-socle-epingler, lancé à la main : il remplace socle/ dans le dépôt de Moneta lui-même.
    ('moneta/scripts/socle_epingler.py', 'extraire'): 'dépôt',
    ('moneta/scripts/socle_epingler.py', 'epingler'): 'dépôt',
}
ECRIT = {'write_text', 'write_bytes', 'mkdir', 'makedirs', 'rename', 'renames', 'unlink', 'remove', 'rmdir', 'rmtree', 'touch',
         'chmod', 'lchmod', 'chown', 'copy', 'copy2', 'copyfile', 'copytree', 'copymode', 'copystat', 'move', 'symlink',
         'symlink_to', 'hardlink_to', 'link', 'utime', 'truncate', 'mkfifo', 'mknod', 'extractall', 'extract',
         'write_private', 'private_folder', 'mkdtemp', 'mkstemp', 'TemporaryDirectory', 'NamedTemporaryFile',
         'TemporaryFile', 'SpooledTemporaryFile', 'migrate', 'backup', 'savez', 'dump'}
DRAPEAUX = {'O_WRONLY', 'O_RDWR', 'O_CREAT', 'O_TRUNC', 'O_APPEND'}


def nom(appel):
    return getattr(appel.func, 'attr', getattr(appel.func, 'id', ''))


def texte(noeud):
    if isinstance(noeud, ast.Constant) and isinstance(noeud.value, str):
        return noeud.value
    if isinstance(noeud, ast.JoinedStr):
        return ''.join(texte(v) or '' for v in noeud.values)
    if isinstance(noeud, ast.BinOp) and isinstance(noeud.op, ast.Add):
        gauche, droite = texte(noeud.left), texte(noeud.right)
        return None if gauche is None and droite is None else (gauche or '') + (droite or '')
    return None


def ecrit(appel):
    """Le nom de l'écriture faite par cet appel, ou None."""
    quoi = nom(appel)
    if quoi in ECRIT:
        if quoi == 'dump' and not (isinstance(appel.func, ast.Attribute) and getattr(appel.func.value, 'id', '') == 'json'):
            return None
        return quoi
    if quoi == 'replace' and (len(appel.args) == 1 or getattr(getattr(appel.func, 'value', None), 'id', '') == 'os'):
        return quoi
    if quoi in ('open', 'fdopen'):
        if quoi == 'open' and getattr(getattr(appel.func, 'value', None), 'id', '') == 'os':
            drapeaux = {n.attr for a in appel.args[1:2] for n in ast.walk(a) if isinstance(n, ast.Attribute)}
            return 'os.open en écriture' if drapeaux & DRAPEAUX else None
        mode = [k.value for k in appel.keywords if k.arg == 'mode'] or appel.args[1 if quoi == 'fdopen' or not
                isinstance(appel.func, ast.Attribute) else 0:][:1]
        valeur = texte(mode[0]) if mode else 'r'
        if valeur is None or set(valeur) & set('wax+'):
            return f'{quoi} en écriture'
        return None
    if quoi == 'connect' and getattr(getattr(appel.func, 'value', None), 'id', '') == 'sqlite3':
        cible = texte(appel.args[0]) if appel.args else None
        return None if cible and 'mode=ro' in cible else 'sqlite3.connect en écriture'
    return None


def sites(texte_source, fichier):
    """{(fichier, fonction) : écritures}, la fonction la plus proche (ou <module>)."""
    trouve = {}

    def parcourir(noeud, fonction):
        for enfant in ast.iter_child_nodes(noeud):
            ici = enfant.name if isinstance(enfant, (ast.FunctionDef, ast.AsyncFunctionDef)) else fonction
            if isinstance(enfant, ast.Call) and (quoi := ecrit(enfant)):
                trouve.setdefault((fichier, fonction), set()).add(quoi)
            parcourir(enfant, ici)
    parcourir(ast.parse(texte_source), '<module>')
    return trouve


def appels(texte_source, fichier, cibles):
    """Les fonctions de ce fichier qui appellent l'une des cibles (par son nom, direct ou par attribut)."""
    trouve = set()

    def parcourir(noeud, fonction):
        for enfant in ast.iter_child_nodes(noeud):
            ici = enfant.name if isinstance(enfant, (ast.FunctionDef, ast.AsyncFunctionDef)) else fonction
            if isinstance(enfant, ast.Call) and nom(enfant) in cibles:
                trouve.add((fichier, fonction))
            parcourir(enfant, ici)
    parcourir(ast.parse(texte_source), '<module>')
    return trouve


def code():
    return {str(c.relative_to(ROOT)): c.read_text(encoding='utf-8') for c in sorted((ROOT / 'moneta').rglob('*.py'))}


def png(graine):
    from test_factures import png as image
    return image(graine)


class TestInventaire(unittest.TestCase):
    def test_chaque_ecriture_est_connue(self):
        trouve = {}
        for fichier, source in code().items():
            trouve.update(sites(source, fichier))
        inconnues = sorted(f'{f} : {fn} ({", ".join(sorted(q))})' for (f, fn), q in trouve.items() if (f, fn) not in INVENTAIRE)
        self.assertEqual(inconnues, [])
        # L'inventaire ne garde rien qui n'écrit plus : chaque ligne a été relue.
        self.assertEqual(sorted(set(INVENTAIRE) - set(trouve)), [])

    def test_drive_seulement_par_les_portes(self):
        drive = {site for site, raison in INVENTAIRE.items() if raison == 'Drive'}
        self.assertEqual(drive - PORTES, set(AIDES))
        sources = code()
        for (fichier, aide), permis in AIDES.items():
            with self.subTest(aide=aide):
                appelants = set().union(*(appels(s, f, {aide}) for f, s in sources.items()))
                self.assertLessEqual(appelants, permis)
        # Le Drive ne s'atteint que par chemins.drive() : depuis factures, dans la déclaration qui le nomme au socle, et dans la
        # nuit, qui refuse un disque placé dans un dossier synchronisé (socle.offsite_backup.check_disk).
        lecteurs = {f for f, s in sources.items() if appels(s, f, {'drive'}) and f != 'moneta/chemins.py'}
        self.assertEqual(lecteurs, {FACTURES, 'moneta/declaration.py', NUIT})

    def test_violations_plantees(self):
        for source in ("def f(p):\n    p.write_text('x')\n", "def f(p):\n    open(p, 'w').write('x')\n",
                       "def f(p):\n    open(p, mode='ab')\n", "def f(p):\n    p.open('w')\n",
                       "def f(p):\n    os.open(p, os.O_WRONLY | os.O_CREAT)\n", "def f(p):\n    shutil.copy(p, q)\n",
                       "def f(p):\n    os.replace(p, q)\n", "def f(p):\n    p.replace(q)\n", "def f(p):\n    p.mkdir()\n",
                       "def f(p):\n    sqlite3.connect(p)\n", "def f(p):\n    json.dump(x, open(p, 'w'))\n",
                       "def f(p):\n    write_private(p, 'n', b'x')\n", "def f(p):\n    open(p, m)\n",
                       "def deposer(p):\n    p.unlink()\n"):
            with self.subTest(source=source):
                trouve = sites(source, 'moneta/planté.py')
                self.assertTrue(trouve)
                self.assertTrue(all(f == 'moneta/planté.py' and (f, fn) not in INVENTAIRE for f, fn in trouve))

    def test_ce_qui_ne_compte_pas(self):
        for source in ("def f(p):\n    open(p).read()\n", "def f(p):\n    p.open('rb')\n", "def f(s):\n    s.replace('a', 'b')\n",
                       "def f(p):\n    os.open(p, os.O_RDONLY | os.O_NOFOLLOW)\n",
                       "def f(p):\n    sqlite3.connect(f'file:{p}?mode=ro', uri=True)\n", "def f(x):\n    json.dumps(x)\n"):
            with self.subTest(source=source):
                self.assertEqual(sites(source, 'moneta/permis.py'), {})


def instantane(racine):
    """{chemin relatif : empreinte} de chaque fichier, et les dossiers (empreinte None)."""
    vu = {}
    for chemin in Path(racine).rglob('*'):
        relatif = str(chemin.relative_to(racine))
        vu[relatif] = hashlib.sha256(chemin.read_bytes()).hexdigest() if chemin.is_file() else None
    return vu


class TestParcours(unittest.TestCase):
    """Un monde fictif : monde/home (MONETA_HOME), monde/drive (le Drive) et un dossier voisin qui ne doit jamais changer."""

    def setUp(self):
        from moneta.outils.mcp_finances import VARIABLE, FinancesReads, finances_lot
        from moneta.outils.serveur import McpServer
        from outils_tests import SourceStore, seed_fictive_finances
        self.temp = tempfile.TemporaryDirectory(prefix='sorties-')
        self.monde = Path(self.temp.name)
        self.home, self.drive = self.monde / 'home', self.monde / 'drive'
        factures_drive = self.drive / 'DojoD' / '05 Administration' / 'Factures'
        (factures_drive / 'à classer').mkdir(parents=True)
        (self.drive / 'DojoD' / '05 Administration' / 'Sauvegardes').mkdir()
        (self.drive / 'DojoD' / '00 À classer').mkdir()
        (self.drive / 'DojoD' / '00 À classer' / 'note.txt').write_text('voisin\n', encoding='utf-8')
        (self.monde / 'voisin').mkdir()
        (self.monde / 'voisin' / 'garde.txt').write_text('voisin\n', encoding='utf-8')
        self.env = patch.dict(os.environ, {'MONETA_HOME': str(self.home), 'MONETA_DRIVE_DOSSIER': str(self.drive)})
        self.env.start()
        self.ledger = seed_fictive_finances(self.home / 'database')
        self.variable = VARIABLE
        self.server = McpServer(modules=[FinancesReads(), finances_lot(SourceStore(self.ledger, self.home / 'backups',
                                                                                    variable=VARIABLE))])

    def tearDown(self):
        self.env.stop()
        os.environ.pop(self.variable, None)
        self.temp.cleanup()

    def appeler(self, outil, arguments=None):
        reponse = self.server.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                                      'params': {'name': outil, 'arguments': arguments or {}}})['result']
        self.assertFalse(reponse['isError'], (outil, reponse))
        return reponse['structuredContent']

    def parcours(self, nuit):
        avant = instantane(self.monde)
        jour = date.today().isoformat()
        # Dépôt d'une facture, comme « Ajouter une facture » de l'app.
        depot = factures.deposer(png(7))['nom']
        # Classement à l'enregistrement.
        self.appeler('depense_preparer', {'date': jour, 'montant': 45.99, 'categorie': '8811', 'facture': depot})
        self.appeler('WR_SAVE_FINANCES_BD', {'code': self.appeler('finances_lot_preparer')['code']})
        # Rapport imprimable.
        rapport = Path(self.appeler('WR_SAVE_RAPPORT_FICHIER', {'annee': date.today().year})['fichier'])
        self.assertEqual(rapport.parent, self.home / 'exports')
        self.assertTrue(rapport.is_file())
        if nuit:
            from moneta.scripts import sauvegarde_quotidienne
            # Phrase de passe fictive : un test ne lit jamais le trousseau.
            with patch.object(sauvegarde_quotidienne, 'keychain_passphrase', return_value='phrase-de-test-fictive'):
                sauvegarde_quotidienne.offsite(destination=self.drive / 'DojoD' / '05 Administration' / 'Sauvegardes')

        apres = instantane(self.monde)
        changes = {c for c in set(avant) | set(apres) if avant.get(c, 0) != apres.get(c, 0)}
        classee = f'drive/DojoD/05 Administration/Factures/{jour[:4]}/{jour}_45.99.png'
        self.assertIn(classee, apres)
        self.assertNotIn(f'drive/DojoD/05 Administration/Factures/à classer/{depot}', apres)
        sorties = ('home', 'drive/DojoD/05 Administration/Factures/à classer',
                   f'drive/DojoD/05 Administration/Factures/{jour[:4]}',
                   *(('drive/DojoD/05 Administration/Sauvegardes',) if nuit else ()))
        ailleurs = sorted(c for c in changes if not any(c == s or c.startswith(s + '/') for s in sorties))
        self.assertEqual(ailleurs, [])
        self.assertEqual((self.monde / 'voisin' / 'garde.txt').read_text(encoding='utf-8'), 'voisin\n')

    def test_depot_classement_et_rapport_ne_changent_que_leurs_sorties(self):
        self.parcours(nuit=False)

    @unittest.skipUnless(sys.platform == 'darwin' and shutil.which('hdiutil'), 'hdiutil requis')
    def test_avec_la_nuit(self):
        self.parcours(nuit=True)


if __name__ == '__main__':
    unittest.main()
