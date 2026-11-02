"""Le catalogue de Moneta dans Claude Desktop (spec 013 de Smith, T045, EF-003, CS-002).

bin/moneta-mcp --fictif --ecriture publie exactement les outils de Finances capturés sur Smith f9c1599 (T011), dans le même ordre et
avec les mêmes schémas, regles_affaires retiré (il reste à Agent Smith). Aucun outil de Smith en double. Règle d'Ugo : tout outil qui
écrit s'appelle WR_SAVE_…, en MAJUSCULES, seulement ceux-là, et son titre est son nom.
"""
import json
import os
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parent.parent
CAPTURE = json.loads((ROOT / 'tests' / 'references' / 'cas_moneta' / 'catalogue.json').read_text(encoding='utf-8'))['cas'][0]['attendu']


def outils(*options):
    messages = [{'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2025-06-18'}},
                {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'}]
    fait = subprocess.run([str(ROOT / 'bin' / 'moneta-mcp'), *options], cwd=ROOT, env=dict(os.environ), timeout=60,
                          input=''.join(json.dumps(m) + '\n' for m in messages), capture_output=True, text=True)
    if fait.returncode:
        raise AssertionError(fait.stderr[-2000:])
    return [json.loads(ligne) for ligne in fait.stdout.splitlines()][1]['result']['tools']


class TestCatalogueMoneta(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tools = outils('--fictif', '--ecriture')

    def test_noms_et_schemas_identiques_a_la_capture(self):
        attendu = [t for t in CAPTURE['finances'] if t['name'] != 'regles_affaires']
        self.assertEqual(len(attendu), 32)
        publie = json.dumps([{'name': t['name'], 'inputSchema': t['inputSchema']} for t in self.tools], ensure_ascii=False)
        # La capture remplace le prénom des textes du code (cas_moneta.normaliser), seule différence permise.
        self.assertEqual(publie.replace('Ugo', 'Personne Exemple'), json.dumps(attendu, ensure_ascii=False))

    def test_aucun_outil_de_smith_en_double(self):
        finances = {t['name'] for t in CAPTURE['finances']}
        smith = set(CAPTURE['smith']) - finances | {'regles_affaires'}
        self.assertEqual(smith & {t['name'] for t in self.tools}, set())
        self.assertNotIn('facture_deposer', {t['name'] for t in self.tools})

    def test_majuscules_exactement_pour_les_outils_qui_ecrivent(self):
        for tool in self.tools:
            writes = tool['annotations']['readOnlyHint'] is False
            self.assertEqual(tool['name'].isupper(), writes, tool['name'])
            self.assertEqual(tool['name'].startswith('WR_SAVE_'), writes, tool['name'])
            if writes:
                self.assertEqual((tool['title'], tool['annotations']['title']), (tool['name'], tool['name']))
        self.assertEqual(sorted(t['name'] for t in self.tools if t['name'].isupper()),
                         ['WR_SAVE_FINANCES_BD', 'WR_SAVE_RAPPORT_FICHIER'])


if __name__ == '__main__':
    unittest.main()
