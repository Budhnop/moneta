"""Sources et cadran de Moneta (repris des parties Finances de test_sources.py et test_cadrans.py de Smith f9c1599, spec 013 T037).
Valeurs attendues faites à la main sur des bases fictives; les paiements passent par le vrai programme de Smith."""
from contextlib import closing
from datetime import date
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from moneta.agent import sources as finances
from outils_tests import FOLDERS, migrate, seed_database

AUJOURDHUI = date(2026, 10, 21)  # un mercredi : sa semaine va du 19 au 25 octobre
JOUR = date(2026, 10, 30)


class Base:
    def __enter__(self):
        self.dossier = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {})
        self.env.start()
        self.db = seed_database(self.dossier.name)
        return self

    def __exit__(self, *exc):
        self.env.stop()
        self.dossier.cleanup()


class TestSourcesFinances(unittest.TestCase):
    def test_totaux_du_compte_dojo_sans_autoriser(self):
        from moneta.agent.sources import lire
        from moneta.finances.finances import add_expense, add_income
        with Base() as base:
            ledger = Path(base.dossier.name) / 'finances.db'
            migrate(ledger, Path(base.dossier.name) / 'backups', FOLDERS['finances'])
            os.environ['MONETA_FINANCES_DB'] = str(ledger)
            self.assertEqual(lire('points_solde', AUJOURDHUI), {'type': 'nombre', 'indisponible': 'aucun relevé Cobalt'})
            self.assertEqual(lire('points_objectif', AUJOURDHUI), {'type': 'nombre', 'indisponible': 'aucun objectif'})
            with closing(sqlite3.connect(ledger)) as con, con:
                code = con.execute('SELECT code FROM categories WHERE pct_deductible IS NOT NULL ORDER BY code').fetchone()[0]
                perso = con.execute("SELECT id FROM comptes WHERE nom='Perso'").fetchone()
                perso = perso[0] if perso else con.execute("INSERT INTO comptes(nom) VALUES ('Perso')").lastrowid
                banque = con.execute("INSERT INTO comptes_bancaires(compte_id, empreinte, apercu, type) VALUES (?, ?, '…9999', 'banque')",
                                     (perso, 'a' * 64)).lastrowid
                envoi = con.execute("INSERT INTO imports(fichier, empreinte, format, lignes, nouvelles) VALUES ('p.ofx', ?, 'ofx', 1, 1)",
                                    ('b' * 64,)).lastrowid
                con.execute("INSERT INTO operations(compte_bancaire_id, import_id, cle, date, montant_cents, description) "
                            "VALUES (?, ?, ?, ?, -999999, 'Épicerie fictive')", (banque, envoi, 'c' * 64, f'{date.today().year}-02-03'))
            annee = date.today().year
            add_expense(ledger, {'date': f'{annee}-03-04', 'montant_cents': 12345, 'categorie': code, 'description': 'Dépense fictive'})
            add_expense(ledger, {'date': f'{annee - 1}-03-04', 'montant_cents': 50000, 'categorie': code})
            add_income(ledger, {'date': f'{annee}-04-05', 'montant_cents': 2500, 'type': 'equipement', 'mode': 'virement', 'description': 'Revenu fictif'})
            add_income(ledger, {'date': f'{annee}-04-06', 'montant_cents': 1000, 'type': 'equipement', 'mode': 'comptant'})
            # Dépense et revenu saisis au compte Perso : jamais dans les totaux du compte Dojo.
            add_expense(ledger, {'date': f'{annee}-03-05', 'montant_cents': 77777, 'categorie': code, 'compte': 'Perso'})
            add_income(ledger, {'date': f'{annee}-04-07', 'montant_cents': 8888, 'type': 'autre', 'mode': 'virement', 'compte': 'Perso'})
            with closing(sqlite3.connect(base.db)) as con, con:
                famille = con.execute("INSERT INTO familles(nom) VALUES ('Famille P')").lastrowid
                session = con.execute('SELECT id FROM sessions WHERE actif=1').fetchone()[0]
                con.execute("INSERT INTO paiements(famille_id, session_id, montant, date_paiement, mode) VALUES (?, ?, 80, ?, 'virement')",
                            (famille, session, f'{annee}-05-06'))
                con.execute("INSERT INTO paiements(famille_id, session_id, montant, date_paiement, mode) VALUES (?, ?, 40, ?, 'comptant')",
                            (famille, session, f'{annee}-05-07'))
            # 80 $ de paiements + 25 $ d'autres revenus; le comptant (40 $ et 10 $) reste hors du total (D058).
            self.assertEqual(lire('revenus_dojo_annee_total'), {'type': 'nombre', 'valeur': 105.0})
            # Dépenses du compte Dojo de l'année seulement; l'opération Perso n'y entre jamais.
            self.assertEqual(lire('depenses_dojo_annee_total'), {'type': 'nombre', 'valeur': 123.45})
            # Détails : mêmes exclusions, descriptions et paiements du dojo; aucune écriture par la lecture.
            avant = (base.db.read_bytes(), ledger.read_bytes())
            self.assertEqual(lire('depenses_dojo_annee'), {'type': 'liste', 'colonnes': ['date', 'description', 'montant'],
                                                        'lignes': [[f'{annee}-03-04', 'Dépense fictive', 123.45]]})
            self.assertEqual(lire('revenus_dojo_annee'), {'type': 'liste', 'colonnes': ['date', 'description', 'montant'],
                                                       'lignes': [[f'{annee}-04-05', 'Revenu fictif', 25.0],
                                                                  [f'{annee}-05-06', 'Paiement · Famille P', 80.0]]})
            self.assertEqual(lire('revenus_dojo_annee', date(2001, 1, 1))['lignes'], [])
            self.assertEqual((base.db.read_bytes(), ledger.read_bytes()), avant)


class TestCadrans(unittest.TestCase):
    def verifier(self, valeur):
        self.assertEqual(valeur['colonnes'], ['nom', 'fait', 'total'])
        self.assertEqual(len(valeur['lignes']), 3)
        self.assertNotIn('CANARI', json.dumps(valeur))
        for nom, fait, total in valeur['lignes']:
            self.assertIsInstance(nom, str)
            self.assertTrue(fait is None or type(fait) is int)
            self.assertTrue(total is None or type(total) is int)

    def test_finances_sql_sans_texte(self):
        def connexion(*args, **kwargs):
            con = sqlite3.connect(':memory:')
            con.executescript("CREATE TABLE operations(id, date, classe, compte_bancaire_id, import_id, categorie_perso_id, depense_id); INSERT INTO operations(id, date, classe, compte_bancaire_id, import_id) VALUES (1, '2026-01-01', NULL, 1, 1), (2, '2026-02-01', 'CANARI', 1, 1), (3, '2025-01-01', NULL, 1, 1); CREATE TABLE categories_perso(id); CREATE TABLE depenses(id, categorie_id); CREATE TABLE categories(id); CREATE TABLE comptes(id); INSERT INTO comptes VALUES (1); CREATE TABLE comptes_bancaires(id, compte_id); INSERT INTO comptes_bancaires VALUES (1, 1); CREATE TABLE imports(id, du, au, format); INSERT INTO imports VALUES (1, '2026-01-01', '2026-02-01', 'ofx'); CREATE TABLE soldes_releves(compte_bancaire_id, du, au, solde_debut_cents, solde_fin_cents); INSERT INTO soldes_releves VALUES (1, '2026-02-02', '2026-03-01', 0, 0);")
            return con
        with patch.object(finances, 'connect', side_effect=connexion), patch.object(finances, 'inbox', return_value=['CANARI'] * 3):
            valeur = finances.cadran_finances(JOUR)
            self.verifier(valeur)
            self.assertEqual([l[1:] for l in valeur['lignes']], [[1, 2], [3, None], [2, None]])
            with patch.object(finances, 'inbox', side_effect=ValueError('CANARI')):
                valeur = finances.cadran_finances(JOUR)
                self.verifier(valeur)
                self.assertEqual([l[1:] for l in valeur['lignes']], [[1, 2], [None, None], [2, None]])

    def test_paiements_illisibles_jamais_zero(self):
        # Spec 013 : sans réponse de Dojo, la source le dit; jamais un total à 0.
        from socle.lots import ToolError
        with patch.dict(os.environ, {'MONETA_LECTURE_PAIEMENTS': '/nulle/part'}):
            with self.assertRaises(ToolError):
                finances.lire('revenus_dojo_annee_total', JOUR)


if __name__ == '__main__':
    unittest.main()
