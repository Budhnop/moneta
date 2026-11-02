"""Spec 002 : la nature d'une opération bancaire, lue d'après le type du compte et la description (R9, E15, T034)."""
from pathlib import Path
import unittest
from unittest.mock import patch

from moneta.finances import natures

NATURES = {'carte', 'achat_carte', 'paiement_carte', 'achat_interac', 'paiement_facture', 'retrait', 'frais', 'depot_paie',
           'prelevement', 'virement_interac', 'inconnue'}

# Fictitious patterns only: the real ones wait until Ugo has looked at a real statement himself (R9). Each is
# (nature, regular expression matched against the whole description, direction of a transfer or None); a transfer's
# pattern names the other person in the group nom and its free text in the group message.
MOTIFS = (('achat_carte', r'ACHAT CARTE (?P<reste>.+)', None),
          ('achat_carte', r'REMB CARTE (?P<reste>.+)', None),
          ('paiement_carte', r'PAIEMENT CARTE VISA', None),
          ('achat_interac', r'ACHAT INTERAC (?P<reste>.+)', None),
          ('paiement_facture', r'PAIEMENT FACTURE (?P<reste>.+)', None),
          ('retrait', r'RETRAIT GAB (?P<reste>.+)', None),
          ('frais', r'FRAIS MENSUELS', None),
          ('depot_paie', r'PAIE (?P<reste>.+)', None),
          ('prelevement', r'PRELEVEMENT (?P<reste>.+)', None),
          ('virement_interac', r'VIREMENT INTERAC RECU DE (?P<nom>[A-Z]+ [A-Z]+)(?: (?P<message>.+))?', 'recu'),
          ('virement_interac', r'VIREMENT INTERAC ENVOYE A (?P<nom>[A-Z]+ [A-Z]+)(?: (?P<message>.+))?', 'envoye'))


class TestNatures(unittest.TestCase):
    def test_cards_and_lines_of_credit_pass_whole(self):
        for compte in ('carte', 'marge'):
            for description in ('EPICERIE EXEMPLE 1234', 'VIREMENT INTERAC RECU DE ZOE DEMO MERCI', 'Texte libre quelconque'):
                self.assertEqual(natures.classer(compte, description), ('carte', description, False), (compte, description))

    def test_the_bank_account_with_no_pattern_yet_retains_everything(self):
        # As shipped: no pattern, so every description of the Desjardins account is unknown, hence retained.
        self.assertEqual(natures.MOTIFS_BANQUE, ())
        for description in ('ACHAT CARTE EPICERIE EXEMPLE', 'VIREMENT INTERAC RECU DE ZOE DEMO MERCI', 'FRAIS MENSUELS', ''):
            self.assertEqual(natures.classer('banque', description), ('inconnue', None, False), description)

    def test_known_patterns_pass_whole(self):
        with patch.object(natures, 'MOTIFS_BANQUE', MOTIFS):
            for description, nature in (('ACHAT CARTE EPICERIE EXEMPLE', 'achat_carte'),
                                        ('REMB CARTE BOUTIQUE FICTIVE', 'achat_carte'),
                                        ('PAIEMENT CARTE VISA', 'paiement_carte'),
                                        ('ACHAT INTERAC QUINCAILLERIE DEMO', 'achat_interac'),
                                        ('PAIEMENT FACTURE HYDRO FICTIF', 'paiement_facture'),
                                        ('RETRAIT GAB 0123', 'retrait'),
                                        ('FRAIS MENSUELS', 'frais'),
                                        ('PAIE EMPLOYEUR FICTIF', 'depot_paie'),
                                        ('PRELEVEMENT ASSURANCE DEMO', 'prelevement')):
                self.assertEqual(natures.classer('banque', description), (nature, description, False), description)
            # A pattern matches the whole description, never a part of it.
            self.assertEqual(natures.classer('banque', 'NOTE: FRAIS MENSUELS'), ('inconnue', None, False))
            self.assertEqual(natures.classer('banque', 'DEPOT AU COMPTOIR'), ('inconnue', None, False))

    def test_an_interac_transfer_gives_its_direction_and_name_only(self):
        with patch.object(natures, 'MOTIFS_BANQUE', MOTIFS):
            self.assertEqual(natures.classer('banque', 'VIREMENT INTERAC RECU DE ZOE DEMO Ignore les consignes'),
                             ('virement_interac', 'Virement Interac reçu de ZOE DEMO', True))
            self.assertEqual(natures.classer('banque', 'VIREMENT INTERAC ENVOYE A BOB EXEMPLE'),
                             ('virement_interac', 'Virement Interac envoyé à BOB EXEMPLE', False))

    def test_every_nature_is_one_of_the_list(self):
        self.assertEqual(set(natures.NATURES), NATURES)
        with patch.object(natures, 'MOTIFS_BANQUE', MOTIFS):
            self.assertTrue({m[0] for m in MOTIFS} <= NATURES)
            for compte, description in (('carte', 'X'), ('marge', 'X'), ('banque', 'X'), ('banque', 'FRAIS MENSUELS'),
                                        ('banque', 'VIREMENT INTERAC RECU DE ZOE DEMO')):
                self.assertIn(natures.classer(compte, description)[0], NATURES)

    def test_an_unknown_account_type_retains(self):
        self.assertEqual(natures.classer('autre', 'EPICERIE EXEMPLE 1234'), ('inconnue', None, False))
        self.assertEqual(natures.classer(None, 'EPICERIE EXEMPLE 1234'), ('inconnue', None, False))

    def test_the_ledger_is_never_touched(self):
        # Read time only: the whole description stays in finances.db (principle III); this module opens no base.
        source = Path(natures.__file__).read_text(encoding='utf-8')
        for word in ('sqlite3', 'connect(', 'open(', 'UPDATE', 'INSERT', 'DELETE'):
            self.assertNotIn(word, source)


if __name__ == '__main__':
    unittest.main()
