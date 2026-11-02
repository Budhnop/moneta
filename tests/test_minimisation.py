"""Canary: free text never reaches the model unless the question asks for it (Finances, repris de test_minimisation.py de Smith
f9c1599, spec 013 T037). Les blocs de Smith deviennent des tests; leurs assertions sont celles de Smith, mot pour mot."""
from contextlib import closing
from datetime import date
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from outils_tests import FausseFenetre
from moneta.outils.serveur import McpServer
from moneta.outils.mcp_finances import FinancesReads
from moneta.finances.finances import add_expense, add_income
from outils_tests import seed_fictive, seed_fictive_finances

CANARY = 'CANARI'
TODAY = date.today().isoformat()


def call(server, name, arguments):
    reply = server.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': name, 'arguments': arguments}})['result']
    assert not reply['isError'], (name, reply)
    return reply['structuredContent']


def leaks(data):
    return CANARY in json.dumps(data, ensure_ascii=False)


class TestMinimisation(unittest.TestCase):
    def test_lectures_finances_sans_canari(self):
        # Les lectures de Finances du premier bloc de Smith, sans le Dojo ni le calendrier (restés chez Smith).
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'MONETA_HOME': folder}):
            folder = Path(folder)
            dojo, ledger = seed_fictive(folder), seed_fictive_finances(folder)
            with closing(sqlite3.connect(ledger)) as con, con:
                con.execute("INSERT INTO audit_log(action, cible_type, cible_id, details, source, resultat) VALUES "
                            "('completer_depense', 'depense', 1, 'fournisseur: — -> CANARI', 'assistant', 'succes')")
            add_expense(ledger, {'date': TODAY, 'montant_cents': 2000, 'categorie': '8811',
                                 'fournisseur': 'CANARI inc.', 'description': 'CANARI privé'})
            add_income(ledger, {'date': TODAY, 'montant_cents': 5000, 'type': 'kimono', 'description': 'Kimono de CANARI'})
            server = McpServer(modules=[FinancesReads()])
            quiet = [call(server, 'finances_depenses', {}), call(server, 'finances_depenses', {'incompletes': True}),
                     call(server, 'finances_revenus', {}), call(server, 'finances_rapport', {'annee': date.today().year}),
                     call(server, 'finances_historique', {'nombre': 20})]
            assert not [q for q in quiet if leaks(q)], [q for q in quiet if leaks(q)]
            expense = call(server, 'finances_depenses', {})['depenses'][0]
            assert expense['manque'] == ['piece', 'mode'] and expense['montant'] == 20.0
            # Asked for, the same reads still answer: the canary proves the data was there all along.
            assert leaks(call(server, 'finances_depenses', {'details': True}))
            assert leaks(call(server, 'finances_revenus', {'details': True}))

    # Spec 005 (D081, FR-011, SC-005): the points of the Cobalt are read without Autoriser, and nothing else of the Perso account
    # comes with them: no description, no card preview, no file name, no balance in dollars.
    def test_points_sans_texte_du_compte_perso(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'MONETA_HOME': folder}):
            folder = Path(folder)
            seed_fictive(folder)
            ledger = seed_fictive_finances(folder)
            with closing(sqlite3.connect(ledger)) as con, con:
                con.execute("INSERT INTO comptes_bancaires(compte_id, empreinte, apercu, type) VALUES (2, ?, '…CANARI', 'carte')", ('a' * 64,))
                for n, (du, au, before) in enumerate((('2026-01-01', '2026-01-31', 1_000), ('2026-02-01', '2026-02-28', 1_500),
                                                      ('2026-03-01', '2026-03-31', 2_000)), 1):
                    con.execute("INSERT INTO imports(fichier, empreinte, format, source, du, au, lignes, nouvelles) "
                                "VALUES (?, ?, 'pdf', 'cobalt', ?, ?, 1, 1)", (f'CANARI-{n}.pdf', str(n) * 64, du, au))
                    con.execute("INSERT INTO soldes_releves(import_id, compte_bancaire_id, du, au, solde_debut_cents, solde_fin_cents) "
                                "VALUES (?, 1, ?, ?, 7777701, 7777702)", (n, du, au))
                    con.execute("INSERT INTO points_releves(import_id, compte_bancaire_id, du, au, precedent, gagnes, utilises, nouveau, concorde) "
                                "VALUES (?, 1, ?, ?, ?, 500, 0, ?, 1)", (n, du, au, before, before + 500))
                    con.execute("INSERT INTO operations(compte_bancaire_id, import_id, cle, date, montant_cents, description) "
                                "VALUES (1, ?, ?, ?, -4321, 'Épicerie CANARI')", (n, str(n).ljust(64, 'k'), du))
                con.execute("INSERT INTO objectifs_points(cible, date_visee) VALUES (100000, '2030-01-01')")
            server = McpServer(modules=[FinancesReads()])
            with FausseFenetre(reponse=False) as fenetre:
                for arguments in ({}, {'historique': True}):
                    found = call(server, 'points_japon', arguments)
                    text = json.dumps(found, ensure_ascii=False)
                    assert found['solde'] == {'points': 2_500, 'au': '2026-03-31'}, found
                    assert not leaks(found) and not any(x in text for x in ('77777', '4321', '.pdf')), text
            assert fenetre.nombre == 0, fenetre.ouvertures

    # Spec 004 (FR-004, récit 1.4): the holder of each operation is kept for the check only. No read of the bank operations gives
    # the additional card, its preview or any holder field, and none of their parameters filters by holder.
    def test_operations_sans_titulaire(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'MONETA_HOME': folder}):
            folder = Path(folder)
            seed_fictive(folder)
            ledger = seed_fictive_finances(folder)
            year = date.today().year
            with closing(sqlite3.connect(ledger)) as con, con:
                con.execute("INSERT INTO comptes_bancaires(compte_id, empreinte, apercu, type) VALUES (2, ?, '…9876', 'carte')", ('a' * 64,))
                con.execute("INSERT INTO cartes_additionnelles(compte_bancaire_id, empreinte, apercu) VALUES (1, ?, '…CANARI')", ('c' * 64,))
                con.execute("INSERT INTO imports(fichier, empreinte, format, lignes, nouvelles) VALUES ('x.ofx', ?, 'ofx', 2, 2)", ('b' * 64,))
                for n, holder in ((1, None), (2, 1)):
                    con.execute("INSERT INTO operations(compte_bancaire_id, import_id, cle, date, montant_cents, description, carte_additionnelle_id) "
                                "VALUES (1, 1, ?, ?, -1500, 'Achat fictif', ?)", (str(n) * 64, f'{year}-01-0{n}', holder))
            server = McpServer(modules=[FinancesReads()])
            reads = [call(server, 'banque_operations', {'compte': 'Perso'}),
                     call(server, 'banque_operations', {'compte': 'Perso', 'inclure_texte_autrui': True}),
                     call(server, 'banque_a_classer', {}), call(server, 'banque_resume', {'annee': year}),
                     call(server, 'banque_resume', {'annee': year, 'compte': 'Perso', 'mois': 1})]
            for found in reads:
                text = json.dumps(found, ensure_ascii=False)
                assert not leaks(found) and 'carte_additionnelle' not in text and 'titulaire' not in text, text
            assert [len(r['operations']) for r in reads[:3]] == [2, 2, 2], reads
            assert reads[3]['a_classer']['sorties'] == 30.0, reads[3]  # both holders together
            tools = {t['name']: t for t in server.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'})['result']['tools']}
            for name in ('banque_operations', 'banque_a_classer', 'banque_resume'):
                schema = json.dumps(tools[name]['inputSchema'], ensure_ascii=False)
                assert 'titulaire' not in schema and 'carte' not in schema, (name, schema)

    # Spec 004 (SC-006, FR-014, FR-015): the state of the budget gives totals by category and month only, without a window
    # (spec 002, R11). Markers
    # in the descriptions, in the additional card's preview and in both cards' footprints never come out; nor an amount of 2027.
    def test_budget_par_categorie_et_mois(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'MONETA_HOME': folder}):
            from moneta.outils import mcp_finances
            folder = Path(folder)
            seed_fictive(folder)
            ledger = seed_fictive_finances(folder)
            with closing(sqlite3.connect(ledger)) as con, con:
                con.execute("INSERT INTO comptes_bancaires(compte_id, empreinte, apercu, type) VALUES (2, ?, '…9876', 'carte')", ('CANARI'.ljust(64, 'a'),))
                con.execute("INSERT INTO cartes_additionnelles(compte_bancaire_id, empreinte, apercu) VALUES (1, ?, '…CANARI')", ('CANARI'.ljust(64, 'c'),))
                con.execute("INSERT INTO imports(fichier, empreinte, format, lignes, nouvelles) VALUES ('CANARI.ofx', ?, 'ofx', 2, 2)", ('b' * 64,))
                for n, (holder, kind) in enumerate(((None, 'perso'), (1, 'perso'), (1, None)), 1):
                    con.execute("INSERT INTO operations(compte_bancaire_id, import_id, cle, date, montant_cents, description, carte_additionnelle_id, "
                                "classe, categorie_perso_id) VALUES (1, 1, ?, '2026-03-0' || ?, -1500, 'Épicerie CANARI', ?, ?, ?)",
                                (str(n) * 64, n, holder, kind, 2 if kind else None))
                con.execute("INSERT INTO budgets(categorie_perso_id, debut, montant_cents) VALUES (2, '2026-01', 50000)")
                con.execute("INSERT INTO budgets(categorie_perso_id, debut, montant_cents) VALUES (2, '2027-01', 7777777)")
            server = McpServer(modules=[FinancesReads()])
            with FausseFenetre(reponse=False) as fenetre:
                for arguments in ({'annee': 2026, 'mois': 3}, {'annee': 2026}, {'annee': 2026, 'historique': True}):
                    found = call(server, 'budget_etat', arguments)
                    text = json.dumps(found, ensure_ascii=False)
                    assert not leaks(found) and not any(x in text for x in ('77777', 'carte_additionnelle', '"titulaire', '"description', '.ofx')), text
                assert found['categories'][0]['depense'] == 30.0, found  # both holders together
            assert fenetre.nombre == 0, fenetre.ouvertures
            # A key the script would add outside the whitelist is removed by the server, at every level.
            planted = {'periode': {'annee': 2026, 'mois': 3}, 'categories': [{'categorie': 'epicerie', 'description': 'CANARI'}],
                       'operations': [{'id': 1}], 'avertissements': {'incomplet': [{'apercu': '…9876', 'mois': '2026-03', 'raison': 'x', 'numero': 'CANARI'}]}}
            with patch.object(mcp_finances, 'run', lambda action, argument=None: planted):
                found = call(server, 'budget_etat', {'annee': 2026, 'mois': 3})
            assert not leaks(found) and 'operations' not in found and found['categories'] == [{'categorie': 'epicerie'}], found


if __name__ == '__main__':
    unittest.main()
