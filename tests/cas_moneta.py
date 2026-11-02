"""Spec 013 T038 : les joueurs des cas capturés sur Smith f9c1599 (tests/references/cas_moneta), rejoués par le code de Moneta.

Repris de assistant/tests/capturer_cas_moneta.py de Smith, sans la capture : les JSON ne sont jamais régénérés. Les joueurs emploient
le code de Moneta, ses vrais gestionnaires et sous-processus; le Dojo fictif est fait par les migrations de Smith, et ses paiements
sont lus par le vrai bin/smith-lecture-paiements (récit 2.4). Seules l'horloge et la fenêtre du Mac sont simulées. Aucun accès aux
données réelles.
"""
from contextlib import closing, contextmanager, ExitStack
from datetime import date, datetime
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
from unittest.mock import patch

sys.dont_write_bytecode = True
SOURCE = 'f9c1599'
REFERENCES = Path(__file__).resolve().parent / 'references' / 'cas_moneta'
JOUR = '2030-06-15'
PAIEMENTS_FIGES = Path(__file__).resolve().parent / 'paiements_figes.py'


def normaliser(value, dossier):
    """Masques techniques seulement; les dates métier et identifiants SQL restent comparés.

    Ugo est un nom présent dans les textes du code, jamais une donnée lue. Son
    remplacement explicite permet de livrer des références sans nom personnel réel.
    """
    text = json.dumps(value, ensure_ascii=False)
    text = text.replace(str(dossier.resolve()), '<dossier>').replace(str(dossier), '<dossier>')
    text = text.replace('Ugo', 'Personne Exemple')
    text = re.sub(r'\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?', '<horodatage>', text)
    text = re.sub(r'généré le \d{4}-\d{2}-\d{2} à \d{2}:\d{2}', 'généré le <horodatage>', text)
    # Les réponses MCP contiennent aussi une copie JSON dans content[].text.
    text = re.sub(r'(\\?"code\\?": \\?")[0-9a-f]{6}', r'\1<code>', text)
    text = re.sub(r'(\\?"id\\?": \\?")[0-9a-f]{8}', r'\1<lot>', text)
    return json.loads(text)


def ofx(nouveau=False):
    """Folio, transactions et fournisseurs inventés, deux comptes distincts."""
    sections = []
    for suffix, lines in [('EOP', [('P1', '-84.12', 'EPICERIE EXEMPLE'), ('P2', '-45.00', 'FOURNISSEUR FICTIF')]),
                          ('ES1', [('D1', '320.00', 'DEPOT FICTIF'), ('D2', '-6.95', 'FRAIS FICTIFS')])]:
        if nouveau:
            lines = [('N' + suffix, '-12.34', 'ACHAT EXEMPLE')]
        transactions = ''.join(f'<STMTTRN><TRNTYPE>OTHER<DTPOSTED>20300502120000<TRNAMT>{amount}'
                               f'<FITID>{ident}<NAME>{name}</STMTTRN>\n' for ident, amount, name in lines)
        sections.append(f'<STMTRS><CURDEF>CAD<BANKACCTFROM><BANKID>815<ACCTID>815-30000-0123456-{suffix}'
                        f'<ACCTTYPE>CHECKING</BANKACCTFROM><BANKTRANLIST><DTSTART>20300501<DTEND>20300531\n'
                        f'{transactions}</BANKTRANLIST></STMTRS>')
    return ('OFXHEADER:100\nDATA:OFXSGML\nVERSION:102\nCHARSET:1252\n\n<OFX><BANKMSGSRSV1><STMTTRNRS>'
            + ''.join(sections) + '</STMTTRNRS></BANKMSGSRSV1></OFX>').encode('cp1252')


def appeler(server, outil, arguments=None):
    reply = server.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                           'params': {'name': outil, 'arguments': arguments or {}}})['result']
    if reply.get('isError'):
        raise AssertionError((outil, arguments, reply))
    return reply['structuredContent']


@contextmanager
def monde(entree):
    today = date.fromisoformat(entree['today'])

    class Jour(date):
        @classmethod
        def today(cls):
            return cls(today.year, today.month, today.day)

    with tempfile.TemporaryDirectory(prefix='cas-moneta-') as nom, ExitStack() as stack:
        dossier = Path(nom)
        env = {'MONETA_HOME': str(dossier / 'home'), 'MONETA_FINANCES_DB': str(dossier / 'finances.db'),
               'MONETA_DRIVE_DOSSIER': str(dossier / 'drive'), 'PYTHONDONTWRITEBYTECODE': '1'}
        stack.enter_context(patch.dict(os.environ, env))
        for variable in ('UGOD_SIGNAL_TEXTE', 'UGOD_SAUVEGARDE_DESTINATION'):
            os.environ.pop(variable, None)
        for path in ('home/imports', 'drive'):
            (dossier / path).mkdir(parents=True)
        from moneta.outils import executeur as executor, factures
        from moneta.outils import mcp_finances
        from moneta.outils.lot import SourceStore
        from moneta.outils.serveur import McpServer
        from moneta.finances import finances
        from moneta.agent import sources
        from socle import signal_texte
        from socle.migrations import available
        from outils_tests import FOLDERS, FausseFenetre, dojo_migre
        for module in (mcp_finances, finances, sources):
            stack.enter_context(patch.object(module, 'date', Jour))
        # Conserve build_command, liste blanche et execute_safe; fixe seulement date.today
        # avant les imports du programme enfant, y compris lors de l'enregistrement du lot.
        original = executor.build_command
        def commande(action, arguments):
            cmd = original(action, arguments)
            bootstrap = ('import datetime,runpy,sys\n'
                         'class Jour(datetime.date):\n'
                         ' @classmethod\n'
                         f' def today(cls): return cls.fromisoformat({entree["today"]!r})\n'
                         'datetime.date=Jour\n'
                         'sys.argv=sys.argv[1:]\n'
                         'runpy.run_path(sys.argv[0],run_name="__main__")\n')
            return [cmd[0], '-c', bootstrap, *cmd[1:]]
        stack.enter_context(patch.object(executor, 'build_command', commande))
        fenetre = stack.enter_context(FausseFenetre())
        # Le Dojo par le code de Smith (UGOD_HOME, UGOD_DB et le programme de lecture pointés sur ce dossier fictif).
        dojo_migre(dossier)
        with closing(sqlite3.connect(dossier / 'finances.db')) as con, con:
            for _, _, migration in available(FOLDERS['finances']):
                migration.appliquer(con)
        with closing(sqlite3.connect(dossier / 'dojo.db')) as con, con:
            con.execute("INSERT INTO familles(id,nom,cree_le) VALUES (1,'Famille Exemple','2030-01-01')")
            con.execute("INSERT INTO sessions(id,nom) VALUES (1,'Session Fictive')")
            con.execute('PRAGMA ignore_check_constraints=ON')
            con.executemany('INSERT INTO paiements(famille_id,session_id,date_paiement,montant,mode) VALUES (1,1,?,?,?)',
                            [('2030-05-01', 12.345, 'comptant'), ('2030-05-02', 2.675, 'virement'),
                             ('2030-05-02', 1.225, None), ('2030-05-03', 0.015, 'troc'),
                             ('2030-05-04', 5.0, ' comptant '), ('2029-12-31', 19.995, 'virement')])
        # Option A (T038) : le vrai programme de Smith refuse ce monde (deux modes hors CHECK); les réponses de la spec 011
        # figées sur f9c1599 viennent de tests/paiements_figes.py. test_non_regression_moneta vérifie ce refus.
        os.environ['MONETA_LECTURE_PAIEMENTS'] = str(PAIEMENTS_FIGES)
        db = dossier / 'finances.db'
        finances.add_expense(db, {'date': '2030-05-02', 'montant_cents': 8525, 'categorie': '8523',
                                 'fournisseur': 'Restaurant Exemple', 'description': 'Repas fictif'})
        finances.add_expense(db, {'date': '2030-05-03', 'montant_cents': 12345, 'categorie': '9281', 'pct': 25,
                                 'fournisseur': 'Garage Fictif', 'mode': 'carte', 'piece': 'FICTIF-2'})
        finances.add_expense(db, {'date': '2030-05-04', 'montant_cents': 900, 'categorie': '8811'})
        finances.cancel_expense(db, {'id': 3})
        for mode, cents in [('virement', 1234), ('comptant', 2345), (None, 3456)]:
            finances.add_income(db, {'date': '2030-05-01', 'montant_cents': cents, 'type': 'autre',
                                    'description': 'Revenu Fictif', **({'mode': mode} if mode else {})})
        finances.add_income(db, {'date': '2030-05-01', 'montant_cents': 999, 'type': 'guide'})
        finances.cancel_income(db, {'id': 4})
        finances.set_goal(db, {'cible': 120000, 'date_visee': '2032-05-01'}, today=today)
        finances.set_budget(db, {'categorie': 'epicerie', 'montant_cents': 80000, 'debut': '2030-01'}, today=today)
        for name, new in [('initial.ofx', False), ('nouveau.ofx', True)]:
            (dossier / 'home/imports' / name).write_bytes(ofx(new))
        request = {'fichier': 'initial.ofx', 'comptes': {'…3456 EOP': 'Perso', '…3456 ES1': 'Dojo'}}
        analyse = finances.READS['import_analyser'](db, request)
        finances.WRITES['importer'](db, {**request, 'empreinte': analyse['empreinte']})
        finances.WRITES['regle'](db, {'motif': 'EPICERIE EXEMPLE', 'categorie': 'epicerie'})
        # Relevés Cobalt synthétiques : solde et rythme non vides, avec un mois manquant.
        with closing(sqlite3.connect(db)) as con, con:
            card = con.execute("INSERT INTO comptes_bancaires(compte_id,empreinte,apercu,type) "
                               "VALUES (2,?,'…1001','carte')", ('c' * 64,)).lastrowid
            for i, (du, au) in enumerate([('2030-02-01', '2030-02-28'), ('2030-04-01', '2030-04-30')]):
                ident = con.execute("INSERT INTO imports(fichier,empreinte,format,source,du,au,lignes,nouvelles) "
                                    "VALUES (?,?,'pdf','cobalt',?,?,0,0)",
                                    (f'points-fictifs-{i}.pdf', str(i) * 64, du, au)).lastrowid
                con.execute('INSERT INTO soldes_releves(import_id,compte_bancaire_id,du,au,solde_debut_cents,solde_fin_cents) '
                            'VALUES (?,?,?,?,0,0)', (ident, card, du, au))
                con.execute('INSERT INTO points_releves(import_id,compte_bancaire_id,du,au,precedent,gagnes,utilises,nouveau,concorde) '
                            'VALUES (?,?,?,?,?,2000,0,?,1)', (ident, card, du, au, 10000 + i * 2000, 12000 + i * 2000))
        inbox = dossier / 'drive' / factures.INBOX
        inbox.mkdir(parents=True)
        (inbox / 'facture-exemple.pdf').write_bytes(b'%PDF-1.4\nFacture fictive\n')
        # Date de dépôt fixe : factures_a_classer montre l'heure de modification du fichier.
        depose = datetime(2030, 6, 14, 9, 30).timestamp()
        os.utime(inbox / 'facture-exemple.pdf', (depose, depose))
        signal_texte.creer_initial()
        lot = mcp_finances.finances_lot(SourceStore(db, dossier / 'backups', variable=mcp_finances.VARIABLE))
        server = McpServer(modules=[mcp_finances.FinancesReads(), lot])
        yield dossier, server, lot, fenetre


def lignes_base(db):
    with closing(sqlite3.connect(db)) as con:
        con.row_factory = sqlite3.Row
        tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        return {table: [dict(r) for r in con.execute(f'SELECT * FROM "{table}" ORDER BY rowid')] for table in tables}


def jouer(famille, entree):
    with monde(entree) as (dossier, server, lot, fenetre):
        if famille == 'lectures':
            sortie = appeler(server, entree['outil'], entree['arguments'])
        elif famille == 'ecritures':
            preparations = [appeler(server, outil, args) for outil, args in entree['actions']]
            resume = appeler(server, 'finances_lot_preparer')
            resultat = appeler(server, 'WR_SAVE_FINANCES_BD', {'code': resume['code']})
            sortie = {'preparations': preparations, 'recapitulatif': resume, 'ecriture': resultat,
                      'fenetres': fenetre.ouvertures, 'base_apres': lignes_base(dossier / 'finances.db')}
        elif famille == 'rapports':
            resultat = appeler(server, 'WR_SAVE_RAPPORT_FICHIER', entree['arguments'])
            sortie = {'resultat': resultat, 'html': Path(resultat['fichier']).read_text(encoding='utf-8'),
                      'fenetres': fenetre.ouvertures}
        elif famille == 'sources':
            from moneta.agent.sources import lire
            sortie = lire(entree['source'], today=date.fromisoformat(entree['today']))
        elif famille == 'paiements':
            from moneta import lecture_dojo
            sortie = getattr(lecture_dojo, entree['fonction'])(entree['annee'])
        elif famille == 'catalogue':
            # Seule la partie Finances est à Moneta; la liste entière de Smith se rejoue chez Smith.
            catalogue = server.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'})['result']['tools']
            sortie = {'finances': [{'name': t['name'], 'inputSchema': t['inputSchema']} for t in catalogue]}
        else:
            raise AssertionError(famille)
        return normaliser(sortie, dossier)


def cas_lectures():
    lectures = {
        'finances_reference': [{}],
        'finances_depenses': [{}, {'annee': 2030, 'mois': 5, 'categorie': '8523', 'details': True},
                              {'annee': 2030, 'incompletes': True}, {'annulees': True, 'inclure_texte_autrui': True}],
        'finances_revenus': [{}, {'annee': 2030, 'mois': 5, 'type': 'autre', 'details': True},
                             {'annules': True, 'inclure_comptant': True, 'details': True}],
        'finances_rapport': [{'annee': 2030}, {'annee': 2030, 'inclure_comptant': True}, {'annee': 2029}],
        'finances_historique': [{}, {'nombre': 2}],
        'banque_operations': [{}, {'annee': 2030, 'mois': 5, 'compte': 'Perso', 'inclure_texte_autrui': True},
                              {'annee': 2030, 'statut': 'classee', 'categorie': 'epicerie'}],
        'banque_a_classer': [{}, {'annee': 2030, 'mois': 5, 'compte': 'Dojo', 'inclure_texte_autrui': True}],
        'banque_resume': [{'annee': 2030}, {'annee': 2030, 'mois': 5, 'compte': 'Perso'}],
        'budget_etat': [{'annee': 2030}, {'annee': 2030, 'mois': 5, 'historique': True}, {'annee': 2030, 'mois': 6}],
        'points_japon': [{}, {'historique': True}],
        'factures_a_classer': [{}], 'releves_manquants': [{}, {'compte': 'Perso'}, {'compte': 'Dojo'}],
        'regles_affaires': [{}],
    }
    return [(f'{outil} / {i}', {'today': JOUR, 'outil': outil, 'arguments': args})
            for outil, variants in lectures.items() for i, args in enumerate(variants, 1)]


def cas_ecritures():
    actions = [
        ('depense_preparer', {'date': '2030-05-05', 'montant': 85.25, 'categorie': '8523', 'fournisseur': 'Restaurant Fictif'}),
        ('depense_completer_preparer', {'id': 1, 'fournisseur': 'Restaurant Exemple', 'description': 'Repas Exemple', 'mode': 'carte', 'piece': 'EXEMPLE-1'}),
        ('depense_annulation_preparer', {'id': 1}),
        ('revenu_preparer', {'date': '2030-05-05', 'montant': 25.50, 'type': 'kimono', 'mode': 'comptant', 'description': 'Vente Fictive'}),
        ('revenu_annulation_preparer', {'id': 1}),
        ('releve_import_preparer', {'fichier': 'nouveau.ofx'}),
        ('operation_classer_preparer', {'id': 2, 'categorie': '9281', 'pourcentage': 25}),
        ('regle_preparer', {'motif': 'FOURNISSEUR FICTIF', 'categorie': '8811'}),
        ('regle_retirer_preparer', {'id': 1}),
        ('objectif_points_preparer', {'cible': 140000, 'date_visee': '2033-01-01'}),
        ('objectif_points_retirer_preparer', {}),
        ('budget_preparer', {'categorie': 'epicerie', 'montant': 850, 'debut': '2030-06'}),
        ('budget_retirer_preparer', {'categorie': 'epicerie', 'debut': '2030-06'}),
    ]
    cas = [(outil, {'today': JOUR, 'actions': [[outil, args]]}) for outil, args in actions]
    cas += [('lot combine', {'today': JOUR, 'actions': [list(actions[0]), list(actions[3])]}),
            ('plafond fenetre', {'today': JOUR, 'actions': [['depense_preparer', {
                'date': '2030-05-05', 'montant': 1000.01, 'categorie': '8811', 'mode': 'virement'}]]}),
            ('facture liee', {'today': JOUR, 'actions': [['depense_preparer', {
                'date': '2030-05-05', 'montant': 12.34, 'categorie': '8811', 'facture': 'facture-exemple.pdf'}]]})]
    return cas


def cas_sources():
    return [(nom, {'today': JOUR, 'source': nom}) for nom in (
        'revenus_dojo_annee', 'depenses_dojo_annee', 'cadran_moneta', 'points_solde', 'points_objectif',
        'revenus_dojo_annee_total', 'depenses_dojo_annee_total')]


def cas_paiements():
    return [(f'{fonction} / {annee}', {'today': JOUR, 'fonction': fonction, 'annee': annee})
            for fonction in ('totaux_annee', 'lignes_annee') for annee in (2029, 2030, 2031)]


CAS = {'lectures': cas_lectures, 'ecritures': cas_ecritures, 'sources': cas_sources, 'paiements': cas_paiements,
       'rapports': lambda: [(choix, {'today': JOUR, 'arguments': {'annee': 2030, 'inclure_comptant': choix == 'avec comptant'}})
                            for choix in ('sans comptant', 'avec comptant')],
       'catalogue': lambda: [('tools/list', {'today': JOUR})]}
JOUEURS = {nom: (lambda entree, nom=nom: jouer(nom, entree)) for nom in CAS}

