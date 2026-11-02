"""Les règles d'affaires (D073) restent à Agent Smith; ce qu'elles disent de Finances suit le code de Moneta (repris des parties
Finances de test_regles.py de Smith f9c1599, spec 013 T037). Le texte est lu dans le dépôt de Smith (MONETA_TESTS_SMITH)."""
import os
from pathlib import Path
import re
import unittest

from moneta import regles
from moneta.outils import factures
from socle import politique

SMITH = Path(os.environ.get('MONETA_TESTS_SMITH') or Path.home() / '_PersonalAgents' / 'smith').expanduser()
RULES = SMITH / 'assistant' / 'core' / 'ressources' / 'regles' / 'regles-affaires.md'


def version(text):
    found = re.search(r'version (\d+)$', text.partition('\n')[0].strip())
    return int(found.group(1)) if found else None


class TestRegles(unittest.TestCase):
    def setUp(self):
        self.text = RULES.read_text(encoding='utf-8')

    def test_version_7_go_or_window_and_reads_without_autoriser(self):
        # D085, D089, D090 (spec 002): the go or the window, as the policy decides; no « Autoriser » left to ask for.
        self.assertEqual(version(self.text), 8)
        self.assertIn('plus de 1 000 $ écrits sans fenêtre dans la journée', self.text)
        self.assertEqual(regles.RAISONS['plafond_jour'], "Plus de 1 000 $ écrits sans fenêtre aujourd'hui.")
        self.assertIn("Compte comme texte d'autrui tout ce qu'un outil ne garantit pas", self.text)
        self.assertIn('passe sur le go, sans fenêtre (D085)', self.text)
        self.assertIn(f'plus de {regles.MONTANT_MAX:,.0f} $ ou plus de {regles.ETAPES_MAX} étapes'.replace(',', ' '), self.text)
        self.assertIn('un texte d\'autrui rendu dans les 30 dernières minutes', self.text)
        self.assertEqual(politique.signal_texte.AGE_SIGNAL, 30 * 60)
        self.assertIn('`inclure_texte_autrui`', self.text)
        self.assertNotIn('Autoriser', self.text)

    def test_points_pdf_statements_additional_card_and_budget(self):
        # D081, D080 and D082: the points, the PDF statements, the additional card and the budget.
        from moneta.outils.mcp_finances import HANDLERS
        self.assertIn('budget_etat', HANDLERS)
        self.assertIn('La carte additionnelle de sa conjointe sur la Cobalt', self.text)
        self.assertIn('jamais séparées par titulaire (D082)', self.text)
        self.assertIn('Budget familial : seulement quand Ugo le demande.', self.text)
        self.assertIn('ne propose jamais de budget et ne donne aucun conseil', self.text)
        self.assertIn('points_japon', HANDLERS)
        self.assertIn("l'objectif du voyage au Japon (`points_japon`) : aucun montant en dollars", self.text)
        self.assertIn('Ugo dépose le relevé PDF (ou OFX/QFX) dans le dossier imports', self.text)
        self.assertNotIn('seulement des fichiers OFX', self.text)

    def test_what_the_rules_name_matches_the_code(self):
        # Names and folders the code enforces: a change on one side without the other fails here.
        self.assertIn(f'« {factures.INBOX} »', self.text)


if __name__ == '__main__':
    unittest.main()
