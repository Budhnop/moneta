"""Spec 013 T038 : rejoue sur Moneta les cas capturés sur Smith f9c1599 (tests/references/cas_moneta, seul cadran_moneta renommé), sans
ancien code ni régénération des JSON. Mêmes chiffres, mêmes résultats, mêmes lignes en base (récit 2.1 à 2.4, CS-003).

Deux parties restent chez Smith et y sont rejouées : l'outil regles_affaires (les règles d'affaires sont à Agent Smith) et la liste
entière des outils de Smith. Ici, le catalogue compare les outils de Finances, regles_affaires retiré.

Option A (choisie par Ugo le 2026-11-02) : le monde de T011 contient deux paiements aux modes « troc » et « comptant » entouré
d'espaces, écrits sans contrainte CHECK. Le vrai smith-lecture-paiements refuse cette base, à raison (copie vérifiée de T024) :
test_le_vrai_programme_refuse_ce_monde le prouve, jamais un zéro. Les cas qui lisent les paiements reçoivent alors les réponses de la
spec 011 figées sur f9c1599 (tests/paiements_figes.py); le vrai programme est éprouvé sur des bases valides par les autres tests.
"""
from datetime import date
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cas_moneta import JOUEURS, monde

REFERENCES = Path(__file__).resolve().parent / 'references' / 'cas_moneta'
LECTURES = {'finances_reference': 1, 'finances_depenses': 4, 'finances_revenus': 3, 'finances_rapport': 3,
            'finances_historique': 2, 'banque_operations': 3, 'banque_a_classer': 2, 'banque_resume': 2,
            'budget_etat': 3, 'points_japon': 2, 'factures_a_classer': 1, 'releves_manquants': 3, 'regles_affaires': 1}
ATTENDUS = {
    'lectures': tuple(f'{outil} / {i}' for outil, nombre in LECTURES.items() for i in range(1, nombre + 1)),
    'ecritures': ('depense_preparer', 'depense_completer_preparer', 'depense_annulation_preparer', 'revenu_preparer',
                  'revenu_annulation_preparer', 'releve_import_preparer', 'operation_classer_preparer', 'regle_preparer',
                  'regle_retirer_preparer', 'objectif_points_preparer', 'objectif_points_retirer_preparer',
                  'budget_preparer', 'budget_retirer_preparer', 'lot combine', 'plafond fenetre', 'facture liee'),
    'sources': ('revenus_dojo_annee', 'depenses_dojo_annee', 'cadran_moneta', 'points_solde', 'points_objectif',
                'revenus_dojo_annee_total', 'depenses_dojo_annee_total'),
    'paiements': tuple(f'{fonction} / {annee}' for fonction in ('totaux_annee', 'lignes_annee') for annee in (2029, 2030, 2031)),
    'rapports': ('sans comptant', 'avec comptant'), 'catalogue': ('tools/list',),
}
# Rejoués chez Smith seulement (voir la docstring).
CHEZ_SMITH = {('lectures', 'regles_affaires / 1')}
# Jour de la capture : SQLite date seul cree_le (DEFAULT datetime('now')), que monde() ne fige pas; la référence
# l'a donc gardé. Le jour réel du rejeu lui est ramené, et lui seul.
JOUR_CAPTURE = '2026-11-02'


def lire(famille):
    return json.loads((REFERENCES / f'{famille}.json').read_text(encoding='utf-8'))


class TestMoneta(unittest.TestCase):
    def test_le_jeu_est_complet(self):
        self.assertEqual(set(JOUEURS), set(ATTENDUS))
        self.assertEqual({p.stem for p in REFERENCES.glob('*.json')}, set(ATTENDUS))
        for famille, noms in ATTENDUS.items():
            with self.subTest(famille=famille):
                contenu = lire(famille)
                self.assertEqual((contenu['source'], contenu['famille']), ('f9c1599', famille))
                self.assertCountEqual([cas['nom'] for cas in contenu['cas']], noms)
                for cas in contenu['cas']:
                    self.assertEqual(set(cas), {'nom', 'entree', 'attendu'})
                    self.assertEqual(cas['entree']['today'], '2030-06-15')

    def test_comptant_exclu_et_montre(self):
        # D058 : sans inclure_comptant, le rapport exclut le comptant et dit combien; les cas le contiennent bien.
        exclus = []
        for cas in lire('lectures')['cas']:
            if cas['entree']['outil'] == 'finances_rapport' and not cas['entree']['arguments'].get('inclure_comptant'):
                self.assertFalse(cas['attendu']['comptant']['inclus'])
                exclus.append(cas['attendu']['comptant']['exclu']['paiements_dojo']['nombre'])
        self.assertTrue(any(exclus), exclus)  # au moins un rapport montre du comptant exclu (2030)
        sans = next(cas for cas in lire('rapports')['cas'] if cas['nom'] == 'sans comptant')
        self.assertGreater(sans['attendu']['resultat']['comptant']['exclu']['total'], 0) if 'comptant' in sans['attendu']['resultat'] \
            else self.assertIn('comptant', sans['attendu']['html'].lower())

    def test_le_vrai_programme_refuse_ce_monde(self):
        import os
        from moneta import lecture_dojo
        from outils_tests import SMITH
        from socle.lots import ToolError
        with monde({'today': '2030-06-15'}):
            os.environ['MONETA_LECTURE_PAIEMENTS'] = str(SMITH / 'bin' / 'smith-lecture-paiements')
            for fonction in (lecture_dojo.totaux_annee, lecture_dojo.lignes_annee):
                with self.subTest(fonction=fonction.__name__), self.assertRaises(ToolError) as refus:
                    fonction(2030)
                self.assertIn('Paiements du Dojo illisibles', str(refus.exception))

    def rejouer(self, famille):
        for cas in lire(famille)['cas']:
            if (famille, cas['nom']) in CHEZ_SMITH:
                continue
            with self.subTest(cas=cas['nom']):
                sortie = JOUEURS[famille](cas['entree'])
                attendu = cas['attendu']
                if famille == 'catalogue':
                    attendu = {'finances': [t for t in attendu['finances'] if t['name'] != 'regles_affaires']}
                # Compare aussi les types numériques (1 et 1.0 ne sont pas interchangeables).
                obtenu = json.dumps(sortie, ensure_ascii=False).replace(date.today().isoformat(), JOUR_CAPTURE)
                self.assertEqual(obtenu, json.dumps(attendu, ensure_ascii=False))


for _famille in ATTENDUS:
    setattr(TestMoneta, f'test_{_famille}', lambda self, famille=_famille: self.rejouer(famille))


if __name__ == '__main__':
    unittest.main()
