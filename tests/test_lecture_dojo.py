"""moneta/lecture_dojo.py (spec 013, contrat lecture-paiements-moneta) : les paiements de Dojo, par le programme fermé de Smith.

Un programme fictif tient lieu de bin/smith-lecture-paiements (MONETA_LECTURE_PAIEMENTS, suivi sur données fictives seulement).
Chaque écart de forme, un programme absent, lent ou en erreur : ToolError « Paiements du Dojo illisibles », jamais un zéro.
"""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import moneta  # noqa: F401  déclare Moneta : fictitious() en dépend
from moneta import lecture_dojo
from socle import confirmation
from socle.lots import ToolError

TOTAUX = {'version': 1, 'commande': 'totaux', 'annee': 2030,
          'montants': {'comptant': 7000, 'virement': 12050, 'non_precise': 0},
          'nombres': {'comptant': 1, 'virement': 2, 'non_precise': 0}}
LIGNES = {'version': 1, 'commande': 'lignes', 'annee': 2030,
          'lignes': [['2030-01-15', 'Famille Exemple', 6025], ['2030-02-01', None, 5000]]}


class Programme:
    """Un faux smith-lecture-paiements : rend sortie telle quelle, avec ce code, après attente secondes."""

    def __init__(self, sortie='', code=0, attente=0, stderr=''):
        self.dossier = tempfile.TemporaryDirectory(prefix='faux-paiements-')
        self.chemin = Path(self.dossier.name) / 'smith-lecture-paiements'
        self.chemin.write_text(f'#!{sys.executable}\nimport sys, time\ntime.sleep({attente})\n'
                               f'sys.stdout.write({sortie!r})\nsys.stderr.write({stderr!r})\nsys.exit({code})\n', encoding='utf-8')
        self.chemin.chmod(0o700)
        self.arguments = Path(self.dossier.name) / 'arguments'

    def __enter__(self):
        self.env = patch.dict(os.environ, {'MONETA_LECTURE_PAIEMENTS': str(self.chemin)})
        self.env.__enter__()
        return self

    def __exit__(self, *exc):
        self.env.__exit__(*exc)
        self.dossier.cleanup()


def ligne(valeur):
    return json.dumps(valeur, ensure_ascii=False) + '\n'


class TestLectureDojo(unittest.TestCase):
    def test_monde_fictif(self):
        self.assertTrue(confirmation.fictitious())

    def test_memes_tuples_que_la_spec_011(self):
        with Programme(ligne(TOTAUX)):
            self.assertEqual(lecture_dojo.totaux_annee(2030), (TOTAUX['montants'], TOTAUX['nombres']))
        with Programme(ligne(LIGNES)):
            self.assertEqual(lecture_dojo.lignes_annee(2030), [('2030-01-15', 'Famille Exemple', 6025), ('2030-02-01', None, 5000)])
        with Programme(ligne({**LIGNES, 'lignes': []})):
            self.assertEqual(lecture_dojo.lignes_annee(2030), [])

    def test_ecarts_de_forme_refuses(self):
        montants = TOTAUX['montants']
        ecarts = {
            'totaux': [
                {**TOTAUX, 'version': 2}, {**TOTAUX, 'commande': 'lignes'}, {**TOTAUX, 'annee': 2029}, {**TOTAUX, 'annee': '2030'},
                {**TOTAUX, 'en_trop': 1}, {k: v for k, v in TOTAUX.items() if k != 'nombres'},
                {**TOTAUX, 'montants': {**montants, 'comptant': -1}}, {**TOTAUX, 'montants': {**montants, 'comptant': 70.0}},
                {**TOTAUX, 'montants': {**montants, 'comptant': True}}, {**TOTAUX, 'montants': {**montants, 'troc': 0}},
                {**TOTAUX, 'montants': {k: v for k, v in montants.items() if k != 'virement'}},
                {**TOTAUX, 'nombres': {**TOTAUX['nombres'], 'virement': '2'}}, {**TOTAUX, 'montants': [7000, 12050, 0]},
                {**TOTAUX, 'version': True}, [TOTAUX], None, 'texte'],
            'lignes': [
                {**LIGNES, 'lignes': [['2029-12-31', None, 100]]}, {**LIGNES, 'lignes': [['2030-02-30', None, 100]]},
                {**LIGNES, 'lignes': [['2030-1-15', None, 100]]}, {**LIGNES, 'lignes': [['2030-01-15', 7, 100]]},
                {**LIGNES, 'lignes': [['2030-01-15', None, -100]]}, {**LIGNES, 'lignes': [['2030-01-15', None, 1.5]]},
                {**LIGNES, 'lignes': [['2030-01-15', None]]}, {**LIGNES, 'lignes': [['2030-01-15', None, 100, 'x']]},
                {**LIGNES, 'lignes': [{'date': '2030-01-15', 'famille': None, 'cents': 100}]}, {**LIGNES, 'lignes': None},
                {**LIGNES, 'commande': 'totaux'}, {**LIGNES, 'en_trop': []}, {**LIGNES, 'lignes': [['2030-01-15', None, False]]}],
        }
        for commande, valeurs in ecarts.items():
            fonction = lecture_dojo.totaux_annee if commande == 'totaux' else lecture_dojo.lignes_annee
            for valeur in valeurs:
                with self.subTest(commande=commande, valeur=valeur), Programme(ligne(valeur)):
                    with self.assertRaises(ToolError) as refus:
                        fonction(2030)
                    self.assertIn('Paiements du Dojo illisibles', str(refus.exception))

    def test_sortie_illisible_refusee(self):
        for sortie in ('', 'pas du json\n', ligne(TOTAUX) + ligne(TOTAUX), ligne(TOTAUX).strip() + ' {}\n', 'x' * 3_000_000):
            with self.subTest(sortie=sortie[:40]), Programme(sortie):
                with self.assertRaises(ToolError) as refus:
                    lecture_dojo.totaux_annee(2030)
                self.assertIn('Paiements du Dojo illisibles', str(refus.exception))

    def test_programme_en_erreur_absent_ou_lent(self):
        cas = {'code non nul': Programme(ligne(TOTAUX), code=1, stderr='Lecture des paiements refusée : base.'),
               'code 2': Programme('', code=2), 'lent': Programme(ligne(TOTAUX), attente=5)}
        for nom, programme in cas.items():
            with self.subTest(nom), programme, patch.object(lecture_dojo, 'DELAI', 1):
                with self.assertRaises(ToolError) as refus:
                    lecture_dojo.totaux_annee(2030)
                self.assertIn('Paiements du Dojo illisibles', str(refus.exception))
                self.assertNotIn('base.', str(refus.exception))
        with patch.dict(os.environ, {'MONETA_LECTURE_PAIEMENTS': '/nulle/part/smith-lecture-paiements'}):
            with self.assertRaises(ToolError) as refus:
                lecture_dojo.lignes_annee(2030)
            self.assertIn('Paiements du Dojo illisibles', str(refus.exception))

    def test_annee_invalide_rien_lance(self):
        with Programme(ligne(TOTAUX)) as programme:
            for annee in (1999, 2101, '2030', 2030.0, True, None):
                with self.subTest(annee=annee), patch.object(lecture_dojo, 'lancer') as lancer:
                    with self.assertRaises(ValueError):
                        lecture_dojo.totaux_annee(annee)
                    lancer.assert_not_called()
            del programme

    def test_appel_ferme(self):
        with Programme(ligne(TOTAUX)) as programme, patch('subprocess.run', wraps=__import__('subprocess').run) as run:
            lecture_dojo.totaux_annee(2030)
        commande, options = run.call_args.args[0], run.call_args.kwargs
        self.assertEqual(commande, [str(programme.chemin), 'totaux', '2030'])
        self.assertIs(options.get('shell'), False)

    def test_programme_reel_hors_fictif(self):
        self.assertEqual(lecture_dojo.PROGRAMME, Path.home() / '_PersonalAgents' / 'smith' / 'bin' / 'smith-lecture-paiements')
        with Programme(ligne(TOTAUX)) as programme:
            self.assertEqual(lecture_dojo.programme(), programme.chemin)
            with patch.object(confirmation, 'fictitious', return_value=False):
                self.assertEqual(lecture_dojo.programme(), lecture_dojo.PROGRAMME)


if __name__ == '__main__':
    unittest.main()
