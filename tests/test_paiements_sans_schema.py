"""Spec 011 de Smith, récits 2 et 4, rejoués sur Moneta (spec 013, repris de TestFinancesSansSchema et TestDojoInaccessible de
test_frontiere_paiements.py de Smith) : Finances ne connaît que le contrat de la lecture, jamais le schéma de Dojo; une lecture
qui échoue reste une erreur, jamais un zéro ni une liste partielle (EF-009).

Le cas de la colonne renommée de Smith (seule la lecture s'adapte) n'a plus d'objet ici : Moneta ne contient aucune requête vers
une table de Dojo, ce que test_frontiere_moneta garde. Tout est fictif.
"""
from datetime import date
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cas_paiements import monde
from socle.lots import ToolError

JOUR = date(2030, 6, 15)


class TestFinancesSansSchema(unittest.TestCase):
    def lire_tout(self):
        from moneta.agent.sources import detail_annee, revenus
        from moneta.finances.finances import report
        return report(Path(os.environ['MONETA_FINANCES_DB']), 2030, cash=True), revenus(JOUR), detail_annee(JOUR, 'revenus')

    def test_valeurs_fixes_sans_aucune_base_de_dojo(self):
        from moneta.agent import sources
        from moneta.finances import finances
        with monde({'base': 'absente'}) as dossier:
            self.assertFalse((dossier / 'dojo.db').exists())
            totaux = lambda annee: ({'comptant': 700, 'virement': 1205, 'non_precise': 100}, {'comptant': 1, 'virement': 2, 'non_precise': 1})
            lignes = lambda annee: [('2030-01-15', 'Famille Exemple', 1205), ('2030-02-01', None, 100)]
            with patch.object(finances, 'totaux_annee', totaux), patch.object(sources, 'totaux_annee', totaux), \
                    patch.object(sources, 'lignes_annee', lignes):
                rapport, valeur, detail = self.lire_tout()
            self.assertFalse((dossier / 'dojo.db').exists())
        self.assertEqual(rapport['revenus_dojo']['par_mode'], {'comptant': 7.0, 'virement': 12.05, 'non_precise': 1.0})
        self.assertEqual(rapport['revenus_dojo']['paiements'], 4)
        self.assertEqual(valeur['valeur'], 13.05)
        self.assertEqual(detail['lignes'], [['2030-01-15', 'Paiement · Famille Exemple', 12.05], ['2030-02-01', 'Paiement', 1.0]])


class TestDojoInaccessible(unittest.TestCase):
    def test_finances_leve_une_erreur(self):
        from moneta.agent.sources import detail_annee, revenus
        from moneta.finances.finances import report
        for base in ('absente', 'sans_table', 'verrouillee'):
            with monde({'base': base}) as dossier:
                for nom, lire in (('report', lambda: report(dossier / 'finances.db', 2030)), ('revenus', lambda: revenus(JOUR)),
                                  ('detail_annee', lambda: detail_annee(JOUR, 'revenus'))):
                    with self.subTest(base=base, lecture=nom), self.assertRaises(ToolError) as refus:
                        lire()
                    self.assertIn('Paiements du Dojo illisibles', str(refus.exception))


if __name__ == '__main__':
    unittest.main()
