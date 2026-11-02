"""La politique de confirmation sur le lot Finances, go ou fenêtre (repris des parties Finances de test_politique.py de Smith
f9c1599, spec 013 : les cas du lot Dojo et des calendriers y restent). Même horloge, même signal, même journal fictifs."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import moneta  # noqa: F401  déclare Moneta au socle
from socle import journal_ecritures, politique, signal_texte

MAINTENANT = datetime(2026, 10, 24, 19, 0, 0, tzinfo=timezone.utc)


def etape(outil, operation, **resume):
    return {'outil': outil, 'operation': operation, 'resume': resume}


def classement():
    return etape('operation_classer_preparer', 'classer', operation_bancaire=12, categorie='8810')


class Base(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix='moneta-politique-'))
        self.signal = self.home / 'signaux' / 'texte-autrui.json'
        self.env = patch.dict(os.environ, {'MONETA_HOME': str(self.home), 'UGOD_SIGNAL_TEXTE': str(self.signal)})
        self.env.start()
        signal_texte.inscrire(maintenant=MAINTENANT - timedelta(minutes=31))  # ancien : rien

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.home)

    def decider(self, lot, etapes):
        return politique.decider(lot, etapes, maintenant=MAINTENANT, version_app='9')

    def raisons(self, lot, etapes):
        return list(self.decider(lot, etapes).raisons)

    def ecrit(self, montant):
        decision = politique.Decision('go', (), Decimal(montant), 1)
        jour = MAINTENANT.astimezone().replace(tzinfo=None, hour=10)
        ident = journal_ecritures.intention('WR_SAVE_FINANCES_BD', 'finances_lot', decision, [], maintenant=jour)
        journal_ecritures.resultat(ident, 'enregistre', maintenant=jour)


class TestGo(Base):
    def test_le_classement_du_scenario_1(self):
        decision = self.decider('finances_lot', [classement()])
        self.assertEqual((decision.mode, decision.raisons, decision.etapes), ('go', (), 1))

    def test_la_table_de_r2_entiere(self):
        # Une étape de chaque préparation go passe seule (montants nuls ou petits).
        from moneta.agent.droits import REGISTRE
        vus = 0
        for nom, (_, outil) in REGISTRE.outils.items():
            if outil.type != 'preparation' or outil.lot != 'finances_lot':
                continue
            vus += 1
            mode = self.decider(outil.lot, [etape(nom, 'x', montant=10.0)]).mode
            self.assertEqual(mode, outil.confirmation, nom)
        self.assertGreater(vus, 0)


class TestRaisons(Base):
    def test_suppression(self):
        for outil in ('depense_annulation_preparer', 'budget_retirer_preparer'):
            self.assertEqual(self.raisons('finances_lot', [etape(outil, 'x')]), ['suppression'], outil)

    def test_plafond_montant(self):
        finances = [etape('depense_preparer', 'depense', montant=-700.0), etape('revenu_preparer', 'revenu', montant=300.01)]
        decision = self.decider('finances_lot', finances)
        self.assertEqual((decision.raisons, decision.montant), (('plafond_montant',), Decimal('1000.01')))
        # Seules les opérations qui inscrivent de l'argent comptent.
        self.assertEqual(self.raisons('finances_lot', [etape('budget_preparer', 'budget', montant=5000.0)]), [])

    def test_combinees_dans_l_ordre(self):
        signal_texte.inscrire(maintenant=MAINTENANT - timedelta(minutes=1))
        finances = [etape('depense_annulation_preparer', 'annulation'), etape('depense_preparer', 'depense', montant=2000.0),
                    etape('revenu_preparer', 'revenu', montant='x')]
        self.assertEqual(self.raisons('finances_lot', finances),
                         ['texte_autrui_recent', 'suppression', 'plafond_montant', 'doute'])


class TestCorrectionsRelecture(Base):
    def test_plafond_du_jour(self):
        self.ecrit('600')
        self.ecrit('500')
        self.assertEqual(self.raisons('finances_lot', [etape('depense_preparer', 'depense', montant=1.0)]), ['plafond_jour'])

    def test_ce_que_compte_le_plafond(self):
        # F11 (D091) : un classement en dépense du dojo compte la dépense qu'il crée. Un classement perso ne compte rien.
        t2125 = etape('operation_classer_preparer', 'classer', operation_bancaire=12, montant=-900.0, depense_t2125=900.0)
        perso = etape('operation_classer_preparer', 'classer', operation_bancaire=13, montant=-900.0)
        self.assertEqual(self.raisons('finances_lot', [t2125, etape('depense_preparer', 'depense', montant=200.0)]),
                         ['plafond_montant'])
        self.assertEqual(self.raisons('finances_lot', [perso, etape('depense_preparer', 'depense', montant=200.0)]), [])
        for valeur in ('900', None, float('nan')):
            illisible = etape('operation_classer_preparer', 'classer', operation_bancaire=12, montant=-900.0, depense_t2125=valeur)
            self.assertEqual(self.raisons('finances_lot', [illisible]), ['doute'], valeur)

    def test_une_regle_ouvre_la_fenetre(self):
        # F11 : une règle classe d'un coup, et classera les relevés à venir : la fenêtre, avec sa propre raison.
        regle = etape('regle_preparer', 'regle', motif='COMMERCE', categorie='8810')
        self.assertEqual(self.raisons('finances_lot', [regle]), ['reglage'])


if __name__ == '__main__':
    unittest.main()
