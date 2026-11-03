"""Les P2 de la revue T074 de Smith (docs/29_REVUE_013.md) qui touchent Moneta hors du déménagement : R8 (variables qui déplacent
la base ou le Drive), R9 (dossiers intermédiaires en 700), R11 (bin/moneta-construire-releve et MONETA_HOME). Rien n'écrit dans
les vraies données : les vrais chemins ne sont que calculés et affichés."""
import ast
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
PERSONNEL = Path.home() / '_PersonalAgents'
TETES = ('moneta_mcp.py', 'migrer.py', 'verifier.py', 'sauvegarde_quotidienne.py', 'rapport.py')


def python(code, **env):
    """code lancé dans un Python de Moneta, sans MONETA_HOME suivi sauf s'il est donné : rend sa sortie."""
    base = {k: v for k, v in os.environ.items() if not k.startswith(('MONETA_', 'UGOD_'))}
    base.update(PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE='1', **env)
    fait = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=60, env=base, cwd=ROOT)
    if fait.returncode:
        raise AssertionError(fait.stderr)
    return fait.stdout.strip()


class TestR8(unittest.TestCase):
    def test_drive_suivi_seulement_avec_moneta_home_suivi(self):
        code = 'from moneta import chemins; print(chemins.drive())'
        reel = str(Path.home() / 'Library/CloudStorage/GoogleDrive-ugo@dojod.ca/My Drive')
        self.assertEqual(python(code, MONETA_DRIVE_DOSSIER='/tmp/ailleurs'), reel)
        self.assertEqual(python(code, MONETA_DRIVE_DOSSIER='/tmp/ailleurs', MONETA_HOME=str(PERSONNEL / 'essai')), reel)
        self.assertEqual(python(code, MONETA_DRIVE_DOSSIER='/tmp/ailleurs', MONETA_HOME=os.environ['MONETA_HOME']), '/tmp/ailleurs')

    def test_environnement_reel_retire_les_variables_heritees(self):
        code = ('import os; from moneta import chemins; chemins.environnement_reel(); '
                'print(os.environ.get("MONETA_FINANCES_DB"), os.environ.get("MONETA_DRIVE_DOSSIER"), chemins.finances_db())')
        self.assertEqual(python(code, MONETA_FINANCES_DB='/tmp/autre.db', MONETA_DRIVE_DOSSIER='/tmp/ailleurs'),
                         f'None None {PERSONNEL / "donnees" / "moneta" / "database" / "finances.db"}')
        # Dans un monde fictif suivi, rien n'est retiré : les tests et --fictif s'en servent.
        self.assertEqual(python(code, MONETA_FINANCES_DB='/tmp/autre.db', MONETA_DRIVE_DOSSIER='/tmp/ailleurs',
                                MONETA_HOME=os.environ['MONETA_HOME']), '/tmp/autre.db /tmp/ailleurs /tmp/autre.db')

    def test_chaque_programme_de_tete_l_appelle_au_debut_de_main(self):
        for nom in TETES:
            with self.subTest(nom):
                arbre = ast.parse((ROOT / 'moneta' / 'scripts' / nom).read_text(encoding='utf-8'))
                main = next(n for n in arbre.body if isinstance(n, ast.FunctionDef) and n.name == 'main')
                appels = [n for n in ast.walk(main) if isinstance(n, ast.Call) and getattr(n.func, 'id', None) == 'environnement_reel']
                self.assertEqual(len(appels), 1, nom)
                # Avant toute lecture de la base ou du Drive.
                lectures = [n.lineno for n in ast.walk(main) if isinstance(n, ast.Call)
                            and getattr(n.func, 'id', None) in ('finances_db', 'drive', 'verifier', 'local_copies', 'execute_safe')]
                self.assertTrue(all(appels[0].lineno <= ligne for ligne in lectures), nom)


class TestR9(unittest.TestCase):
    def test_factures_import_chaque_niveau_en_700(self):
        home = Path(os.environ['MONETA_HOME'])
        shutil.rmtree(home / 'private', ignore_errors=True)
        python('from moneta import chemins; chemins.factures_import()', MONETA_HOME=str(home))
        for niveau in ('private', 'private/app', 'private/app/factures'):
            self.assertEqual(stat.S_IMODE((home / niveau).stat().st_mode), 0o700, niveau)


class TestR11(unittest.TestCase):
    def test_moneta_home_non_suivi_refuse(self):
        essai = PERSONNEL / 'essai-revue-t074' / 'moneta'
        self.assertFalse(essai.parent.exists())
        env = {k: v for k, v in os.environ.items() if not k.startswith(('MONETA_', 'UGOD_'))}
        fait = subprocess.run([str(ROOT / 'bin' / 'moneta-construire-releve')], capture_output=True, text=True, timeout=60,
                              env={**env, 'MONETA_HOME': str(essai)}, stdin=subprocess.DEVNULL)
        try:
            self.assertEqual(fait.returncode, 1, fait.stdout + fait.stderr)
            self.assertIn("n'est pas suivi", fait.stderr)
            self.assertNotIn('OK', fait.stdout)
            self.assertFalse(essai.parent.exists())
        finally:
            shutil.rmtree(essai.parent, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()
