"""moneta-releve : texte et positions d'un relevé PDF fictif, refus des PDF chiffrés, numérisés ou trop longs.
Le programme est construit dans un MONETA_HOME temporaire, jamais dans le vrai dossier des apps."""
from cache_swift import compile_ou_cache
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from releves_fictifs import CHIFFRER_SWIFT, pdf

SOURCE = Path(__file__).resolve().parents[1] / 'moneta' / 'releve' / 'main.swift'


@unittest.skipUnless(sys.platform == 'darwin' and shutil.which('swiftc'), 'PDFKit : macOS avec swiftc')
class TestExtracteur(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.folder = Path(cls.temp.name)
        cls.env = patch.dict(os.environ, {'MONETA_HOME': str(cls.folder / 'home')})
        cls.env.start()
        cls.program = cls.folder / 'home' / 'apps' / 'moneta-releve'
        cls.program.parent.mkdir(parents=True)
        compile_ou_cache(SOURCE, cls.program, ['-O'])

    @classmethod
    def tearDownClass(cls):
        cls.env.stop()
        cls.temp.cleanup()

    def run_on(self, data, name='releve.pdf'):
        path = self.folder / name
        path.write_bytes(data)
        done = subprocess.run([str(self.program), str(path)], capture_output=True, text=True, timeout=30)
        return done.returncode, json.loads(done.stdout.strip().splitlines()[-1])

    def test_segments_keep_their_columns_in_reading_order(self):
        code, reply = self.run_on(pdf([
            [(40, 60, 'Relevé fictif — Banque Exemple'), (40, 100, '2026-01-05'), (300, 100, 'Café Démo'), (500, 100, '-4,50'),
             (40, 120, '2026-01-06'), (120, 120, 'Épicerie Zoé (test)'), (500, 120, '23,10')],
            [(40, 60, 'Page deux')]]))
        self.assertEqual(code, 0, reply)
        self.assertEqual([p['numero'] for p in reply['pages']], [1, 2])
        self.assertEqual((reply['pages'][0]['largeur'], reply['pages'][0]['hauteur']), (612, 792))
        found = [(l['x'], l['texte']) for l in reply['pages'][0]['lignes']]
        self.assertEqual(found, [(40, 'Relevé fictif — Banque Exemple'), (40, '2026-01-05'), (300, 'Café Démo'), (500, '-4,50'),
                                 (40, '2026-01-06'), (120, 'Épicerie Zoé (test)'), (500, '23,10')])
        rows = [l['y'] for l in reply['pages'][0]['lignes']]
        self.assertEqual(len(set(rows[1:4])), 1)
        self.assertLess(rows[0], rows[1])
        self.assertLess(rows[3], rows[4])
        self.assertEqual(reply['pages'][1]['lignes'][0]['texte'], 'Page deux')

    def test_a_dense_twelve_page_statement_reads_in_under_ten_seconds(self):
        page = [(40, 40 + 14 * n, f'2026-01-{n % 28 + 1:02d}') for n in range(50)] + \
               [(120, 40 + 14 * n, f'Commerce fictif numéro {n}') for n in range(50)] + \
               [(500, 40 + 14 * n, f'{n},{n % 100:02d}') for n in range(50)]
        started = time.monotonic()
        code, reply = self.run_on(pdf([page] * 12))
        self.assertEqual(code, 0, reply)
        self.assertEqual(sum(len(p['lignes']) for p in reply['pages']), 12 * 150)
        self.assertLess(time.monotonic() - started, 10)

    def test_a_scanned_page_is_refused(self):
        code, reply = self.run_on(pdf([[(40, 60, 'Texte')], []]))
        self.assertEqual(code, 1)
        self.assertIn('page 2 sans texte', reply['erreur'])

    def test_more_than_fifty_pages_is_refused(self):
        code, reply = self.run_on(pdf([[(40, 60, f'Page {n}')] for n in range(51)]))
        self.assertEqual(code, 1)
        self.assertIn('plus de 50 pages', reply['erreur'])
        code, reply = self.run_on(pdf([[(40, 60, f'Page {n}')] for n in range(50)]))
        self.assertEqual(code, 0, reply)

    def test_an_encrypted_pdf_is_refused(self):
        helper = self.folder / 'chiffrer'
        script = self.folder / 'chiffrer.swift'
        script.write_text(CHIFFRER_SWIFT)
        compile_ou_cache(script, helper, [])
        plain, locked = self.folder / 'clair.pdf', self.folder / 'chiffre.pdf'
        plain.write_bytes(pdf([[(40, 60, 'Secret fictif')]]))
        subprocess.run([str(helper), str(plain), str(locked)], check=True, timeout=60)
        code, reply = self.run_on(locked.read_bytes())
        self.assertEqual(code, 1)
        self.assertIn('chiffré', reply['erreur'])

    def test_not_a_pdf_is_refused(self):
        code, reply = self.run_on(b'OFXHEADER:100\n<OFX></OFX>', 'faux.pdf')
        self.assertEqual(code, 1)
        self.assertIn('illisible', reply['erreur'])


if __name__ == '__main__':
    unittest.main()
