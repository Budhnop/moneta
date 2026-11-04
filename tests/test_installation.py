"""L'installation de Moneta (spec 017) : sans installation.toml ni MONETA_HOME, aucun dossier réel; avec une installation, le
dossier réel est <racine>/moneta; bin/moneta-installer refuse une racine relative et une installation déjà là. Tout se passe
dans des dossiers temporaires : jamais d'installation.toml à la racine du code."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from moneta import chemins
from socle import installation


class TestInstallation(unittest.TestCase):
    def setUp(self):
        avant = os.environ.pop('MONETA_HOME')
        self.addCleanup(os.environ.__setitem__, 'MONETA_HOME', avant)

    def test_sans_installation_ni_variable_home_refuse(self):
        with mock.patch.object(chemins, 'REAL_HOME', None), self.assertRaises(installation.EtatRefuse) as ctx:
            chemins.home()
        self.assertEqual(str(ctx.exception), installation.ABSENTE.format(agent='moneta'))

    def test_avec_installation_home_rend_racine_moneta(self):
        with tempfile.TemporaryDirectory() as temp:
            code = Path(temp) / 'code'
            code.mkdir()
            racine = Path(temp).resolve() / 'racine'
            racine.mkdir(mode=0o700)
            installation.installer(code, 'moneta', str(racine), False)
            reel = installation.dossier_reel(installation.lire(code), 'moneta')
            self.assertEqual(reel, racine / 'moneta')
            with mock.patch.object(chemins, 'REAL_HOME', reel):
                self.assertEqual(chemins.home(), racine / 'moneta')

    def test_variables_heritees_retirees_seulement_sur_les_vraies_donnees(self):
        with tempfile.TemporaryDirectory() as temp:
            for reel, attendu in ((Path(temp) / 'moneta', False), (None, True)):
                with mock.patch.object(chemins, 'REAL_HOME', reel), mock.patch.dict(os.environ, {'MONETA_FINANCES_DB': '/ailleurs/f.db'}):
                    if reel is None:
                        os.environ['MONETA_HOME'] = temp  # fictif suivi : rien n'est retiré
                    chemins.environnement_reel()
                    self.assertEqual('MONETA_FINANCES_DB' in os.environ, attendu)

    def test_installateur_refuse_racine_relative_et_installation_existante(self):
        with tempfile.TemporaryDirectory() as temp:
            code = Path(temp) / 'code'
            (code / 'moneta' / 'scripts').mkdir(parents=True)
            (code / 'moneta' / 'scripts' / 'installer.py').write_bytes((chemins.PAQUET / 'scripts' / 'installer.py').read_bytes())
            (code / 'moneta' / '__init__.py').touch()
            (code / 'moneta' / 'scripts' / '__init__.py').touch()
            os.symlink(chemins.CODE / 'socle', code / 'socle')
            racine = Path(temp).resolve() / 'racine'
            racine.mkdir(mode=0o700)
            env = {k: v for k, v in os.environ.items() if k != 'PYTHONPATH'}
            env['PYTHONDONTWRITEBYTECODE'] = '1'

            def lancer(*arguments):
                return subprocess.run([sys.executable, str(code / 'moneta' / 'scripts' / 'installer.py'), *arguments],
                                      capture_output=True, text=True, env=env, cwd=temp, stdin=subprocess.DEVNULL)
            relatif = lancer('--racine', 'relative/ici')
            self.assertEqual(relatif.returncode, 1)
            self.assertFalse((code / 'installation.toml').exists())
            self.assertEqual(lancer('--racine', str(racine)).returncode, 0)
            self.assertTrue((code / 'installation.toml').is_file())
            deux = lancer('--racine', str(racine))
            self.assertEqual(deux.returncode, 1)
            self.assertIn('Refusé', deux.stderr)


if __name__ == '__main__':
    unittest.main()
