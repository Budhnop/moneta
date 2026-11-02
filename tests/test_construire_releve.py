"""bin/moneta-construire-releve (spec 013, T066) : le lecteur de relevés construit et signé dans $MONETA_HOME/apps d'un dossier
fictif, jamais copié de l'ancien lecteur de Smith (apps/agent-smith-releve). Les caches de swiftc vont dans un dossier temporaire,
jamais dans ~/Library/Caches ni dans le dépôt."""
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
CONSTRUIRE = ROOT / 'bin' / 'moneta-construire-releve'


@unittest.skipUnless(sys.platform == 'darwin' and shutil.which('swiftc') and shutil.which('codesign'), 'macOS avec swiftc')
class TestConstruireReleve(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='moneta-construire-')
        cls.dossier = Path(cls.temp.name).resolve()
        cls.home = cls.dossier / 'donnees' / 'moneta'
        cls.ancien = cls.dossier / 'donnees' / 'smith' / 'apps' / 'agent-smith-releve'
        cls.ancien.parent.mkdir(parents=True)
        cls.ancien.write_bytes(b'#!/bin/sh\necho ancien lecteur\n')
        cls.ancien.chmod(0o700)
        (cls.home / 'apps').mkdir(parents=True, mode=0o700)
        (cls.home / 'apps' / 'moneta-releve').write_bytes(b'#!/bin/sh\necho vieux\n')  # remplacé d'un coup
        cls.fait = cls.construire(cls.home)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    @classmethod
    def construire(cls, home):
        caches = cls.dossier / 'caches'
        caches.mkdir(exist_ok=True)
        env = dict(os.environ, MONETA_HOME=str(home), UGOD_HOME=str(cls.ancien.parents[1]), TMPDIR=str(caches),
                   CLANG_MODULE_CACHE_PATH=str(caches / 'modules'), SWIFT_MODULECACHE_PATH=str(caches / 'modules'))
        return subprocess.run([str(CONSTRUIRE)], capture_output=True, text=True, timeout=600, env=env,
                              stdin=subprocess.DEVNULL)

    def test_construit_signe_et_en_place(self):
        self.assertEqual(self.fait.returncode, 0, self.fait.stdout + self.fait.stderr)
        lecteur = self.home / 'apps' / 'moneta-releve'
        self.assertIn(f'OK : {lecteur}', self.fait.stdout)
        info = lecteur.lstat()
        self.assertTrue(stat.S_ISREG(info.st_mode))
        self.assertEqual(stat.S_IMODE(info.st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((self.home / 'apps').stat().st_mode), 0o700)
        self.assertEqual(sorted(p.name for p in (self.home / 'apps').iterdir()), ['moneta-releve'])  # aucun .neuf laissé
        verifie = subprocess.run(['/usr/bin/codesign', '--verify', '--strict', str(lecteur)], capture_output=True)
        self.assertEqual(verifie.returncode, 0, verifie.stderr)

    def test_jamais_l_ancien_lecteur(self):
        lecteur = self.home / 'apps' / 'moneta-releve'
        self.assertNotEqual(lecteur.read_bytes(), self.ancien.read_bytes())
        self.assertTrue(lecteur.read_bytes().startswith(b'\xcf\xfa\xed\xfe'))  # Mach-O 64 bits, compilé ici
        self.assertEqual(self.ancien.read_bytes(), b'#!/bin/sh\necho ancien lecteur\n')

    def test_le_lecteur_se_nomme_moneta_releve(self):
        fait = subprocess.run([str(self.home / 'apps' / 'moneta-releve')], capture_output=True, text=True, timeout=30)
        self.assertEqual(json.loads(fait.stdout), {'erreur': 'usage : moneta-releve <relevé.pdf>'})

    def test_un_dossier_apps_en_lien_est_refuse(self):
        home = self.dossier / 'lien' / 'moneta'
        home.mkdir(parents=True, mode=0o700)
        ailleurs = self.dossier / 'ailleurs'
        ailleurs.mkdir()
        (home / 'apps').symlink_to(ailleurs)
        fait = self.construire(home)
        self.assertNotEqual(fait.returncode, 0)
        self.assertIn('apps', fait.stderr)
        self.assertEqual(list(ailleurs.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
