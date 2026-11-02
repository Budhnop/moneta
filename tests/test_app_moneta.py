"""L'app parle à la vraie logique Moneta par stdio, sur des données fictives."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import select
import stat
import struct
import subprocess
import unittest
from unittest.mock import patch
import zlib

from moneta.agent.droits import REGISTRE, Source
from moneta.outils import factures
from moneta.outils.executeur import execute_safe
from moneta.outils.serveur import MAX_LIGNES, MAX_POINTS, McpServer

ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def serveur_app():
    env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE='1')
    server = subprocess.Popen([str(ROOT / '.venv/bin/python'), 'scripts/moneta_mcp.py', '--app', '--fictif', '--ecriture'],
                              cwd=ROOT / 'moneta', env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True)
    try:
        yield server
    finally:
        server.stdin.close()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait()
        server.stdout.close()
        server.stderr.close()


def demander(server, method, params=None):
    server.stdin.write(json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params or {}}) + '\n')
    server.stdin.flush()
    if not select.select([server.stdout], [], [], 30)[0]:
        raise AssertionError('La logique ne répond pas.')
    line = server.stdout.readline()
    if not line:
        raise AssertionError(server.stderr.read())
    return json.loads(line)


def png():
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 8, 8, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress((b'\0' + b'\xff\0\0' * 8) * 8)) + chunk(b'IEND', b''))


class TestAppMoneta(unittest.TestCase):
    def setUp(self):
        self.context = serveur_app()
        self.server = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)
        self.registry = demander(self.server, 'registre/lire')['result']
        self.folder = Path(self.registry['factures_import'])
        self.home = self.folder.parents[2]

    def appel(self, outil, arguments=None, agent='moneta'):
        return demander(self.server, 'outils/appeler', {'agent': agent, 'outil': outil, 'arguments': arguments or {}})

    def ok(self, outil, arguments=None):
        reply = self.appel(outil, arguments)['result']
        self.assertFalse(reply.get('isError'), reply)
        return json.loads(reply['content'][0]['text'])

    def test_registre(self):
        self.assertEqual(self.registry['version'], 1)
        self.assertNotIn('photos_import', self.registry)
        self.assertEqual([a['id'] for a in self.registry['agents']], ['moneta'])
        agent = self.registry['agents'][0]
        self.assertEqual(set(agent), {'id', 'nom', 'statut', 'plancher', 'outils', 'sources', 'partages_recus',
                                      'partages_donnes', 'acces'})
        self.assertEqual(agent['nom'], 'Moneta')
        self.assertTrue(self.home.name.startswith('moneta-mcp-fictif-'))
        self.assertEqual(self.folder.relative_to(self.home), Path('private/app/factures'))
        self.assertEqual(stat.S_IMODE(self.folder.stat().st_mode), 0o700)
        sources = {s['nom'] for s in agent['sources']}
        self.assertIn('cadran_moneta', sources)
        self.assertNotIn('cadran_finances', sources)
        depot = next(o for o in agent['outils'] if o['nom'] == 'facture_deposer')
        self.assertEqual(depot['type'], 'depot')
        self.assertTrue(depot['disponible'])
        tools = demander(self.server, 'tools/list')['result']['tools']
        self.assertNotIn('facture_deposer', [t['name'] for t in tools])

    def test_sources(self):
        sources = demander(self.server, 'sources/lire', {'agent': 'moneta', 'sources': [
            'cadran_moneta', 'depenses_dojo_annee', 'revenus_dojo_annee', 'points_solde', 'points_objectif']})['result']['sources']
        self.assertEqual(sources['cadran_moneta']['colonnes'], ['nom', 'fait', 'total'])
        self.assertEqual(len(sources['cadran_moneta']['lignes']), 3)
        for nom in ('depenses_dojo_annee', 'revenus_dojo_annee'):
            self.assertEqual(sources[nom], {'type': 'liste', 'colonnes': ['date', 'description', 'montant'],
                                           'lignes': [], 'texte_externe': True})
        self.assertEqual(sources['points_solde'], {'type': 'nombre', 'indisponible': 'aucun relevé Cobalt'})
        self.assertEqual(sources['points_objectif'], {'type': 'nombre', 'indisponible': 'aucun objectif'})
        for noms in ([], ['points_solde'] * 33, 'points_solde', [1]):
            self.assertEqual(demander(self.server, 'sources/lire', {'agent': 'moneta', 'sources': noms})['error']['code'], -32602)
        refus = demander(self.server, 'sources/lire', {'agent': 'moneta', 'sources': ['inconnue']})['error']
        self.assertEqual(refus, {'code': -32602, 'message': 'Source inconnue « inconnue ».'})
        for agent in ('dojo', 'finances', 'inconnu'):
            self.assertEqual(demander(self.server, 'sources/lire', {'agent': agent, 'sources': ['points_solde']})['error']['code'], -32602)

    def test_outils_et_lot(self):
        self.assertEqual(self.ok('finances_depenses')['total'], 0)
        self.ok('depense_preparer', {'date': '2026-10-20', 'montant': 12.5, 'categorie': '8811'})
        self.assertEqual(self.ok('finances_lot_voir')['etapes'], 1)
        for nom in ('dojo_consulter', 'inconnu'):
            self.assertEqual(self.appel(nom)['error']['code'], -32602)
        for agent in ('dojo', 'finances'):
            self.assertEqual(self.appel('finances_depenses', agent=agent)['error']['code'], -32602)
        for method in ('disposition/lire', 'disposition/ecrire', 'tools/call'):
            self.assertEqual(demander(self.server, method, {'name': 'finances_depenses'})['error']['code'], -32601)

    def test_ajouter_une_facture(self):
        inbox = self.home / 'drive' / factures.INBOX
        inbox.mkdir(parents=True)
        ident = 'a' * 32
        path = self.folder / (ident + '.img')
        path.write_bytes(png())
        path.chmod(0o600)
        depot = self.ok('facture_deposer', {'import': ident})
        self.assertTrue(depot['depose'])
        self.assertEqual((inbox / depot['nom']).read_bytes(), png())
        self.assertFalse(path.exists())
        self.assertEqual(stat.S_IMODE((inbox / depot['nom']).stat().st_mode), 0o600)

    def test_imports_refuses_et_effaces(self):
        ident = 'b' * 32
        path = self.folder / (ident + '.img')
        for arguments in ({}, {'import': '../x'}, {'import': 'A' * 32}, {'import': 'a' * 31},
                          {'import': ident, 'extra': 1}, {'import': 123}, {'import': ident + '\u200b'}):
            self.assertEqual(self.appel('facture_deposer', arguments)['error'],
                             {'code': -32602, 'message': 'Arguments invalides.'})
        target = self.folder / 'cible.png'
        target.write_bytes(png())
        for genre in ('absent', 'lien', 'gros', 'fifo', 'invalide'):
            with self.subTest(genre=genre):
                if genre == 'lien':
                    path.symlink_to(target)
                elif genre == 'gros':
                    with path.open('wb') as handle:
                        handle.truncate(factures.MAX_BYTES + 1)
                elif genre == 'fifo':
                    os.mkfifo(path)
                elif genre == 'invalide':
                    path.write_bytes(b'pas une image')
                result = self.appel('facture_deposer', {'import': ident})['result']
                self.assertTrue(result['isError'], result)
                if genre in ('absent', 'lien'):
                    self.assertIn('Import de facture absent ou illisible.', result['content'][0]['text'])
                self.assertFalse(path.exists())
                self.assertFalse(path.is_symlink())
                self.assertEqual(target.read_bytes(), png())

    def test_commande_fermee_de_l_executeur(self):
        self.assertFalse(execute_safe('sources_moneta', ['membres_actifs_total'])['ok'])
        self.assertFalse(execute_safe('sources_moneta', ['inconnue'])['ok'])
        self.assertFalse(execute_safe('sources_moneta', ['points_solde', 'x'])['ok'])
        with patch.dict(os.environ, {'MONETA_FINANCES_DB': str(self.home / 'finances.db')}):
            self.assertTrue(execute_safe('sources_moneta', ['points_solde'])['ok'])


class TestSourcesControlees(unittest.TestCase):
    def setUp(self):
        # Jamais le vrai dossier de Moneta, même lancé hors de bin/moneta-tests.
        import tempfile
        dossier = tempfile.TemporaryDirectory(prefix='moneta-app-test-')
        self.addCleanup(dossier.cleanup)
        variables = patch.dict(os.environ, {'MONETA_HOME': dossier.name})
        variables.start()
        self.addCleanup(variables.stop)
        self.server = McpServer()
        self.server.registre = REGISTRE
        self.source = Source('depenses_dojo_annee', 'liste', ('date', 'description', 'montant'))

    def lire(self, value, source=None):
        with patch('moneta.outils.serveur.execute_safe', return_value={'ok': True, 'result': json.dumps(value)}):
            return self.server.source_value(source or self.source)

    def test_colonnes_et_indisponibilite(self):
        for value in ({'type': 'liste', 'colonnes': ['date', 'description', 'montant', 'intrus'], 'lignes': []},
                      {'type': 'liste', 'colonnes': list(self.source.colonnes), 'lignes': [['a', 'b', 1, 'intrus']]},
                      {'type': 'liste', 'colonnes': list(self.source.colonnes), 'lignes': 'x'},
                      {'type': 'nombre', 'valeur': 1},
                      {'type': 'liste', 'indisponible': 'raison', 'lignes': [['intrus']]},
                      {'type': 'liste', 'indisponible': 42}):
            self.assertEqual(self.lire(value), {'type': 'liste', 'indisponible': 'lecture impossible'})
        bon = {'type': 'liste', 'colonnes': list(self.source.colonnes), 'lignes': [['a', 'b', 1]]}
        self.assertEqual(self.lire(bon)['lignes'], [['a', 'b', 1]])
        for raison, attendu in (('calendrier absent', 'calendrier absent'), ('x' * 300, 'x' * 120), ('a\u200bb', 'ab')):
            self.assertEqual(self.lire({'type': 'liste', 'indisponible': raison}), {'type': 'liste', 'indisponible': attendu})
        self.assertEqual(self.server.source_value(Source('absente', 'nombre', requiert='points_japon')),
                         {'type': 'nombre', 'indisponible': 'base Finances absente'})

    def test_bornes(self):
        for source, maximum in ((self.source, 10000), (Source('cadran_moneta', 'liste', ('nom', 'fait', 'total')), MAX_LIGNES)):
            value = {'type': 'liste', 'colonnes': list(source.colonnes), 'lignes': [['a', 1, 2]] * (maximum + 1)}
            self.assertEqual(len(self.lire(value, source)['lignes']), maximum)
        value = {'type': 'serie', 'valeurs': list(range(60)), 'etiquettes': list(range(60))}
        lu = self.lire(value, Source('serie', 'serie'))
        self.assertEqual(lu['valeurs'], list(range(60))[-MAX_POINTS:])
        self.assertEqual(lu['etiquettes'], list(range(60))[-MAX_POINTS:])

    def test_purge_au_demarrage_app_seulement(self):
        from moneta.chemins import factures_import
        self.assertTrue(str(factures_import()).startswith(os.environ['MONETA_HOME']))
        folder = factures_import()
        path = folder / ('c' * 32 + '.img')
        path.write_bytes(b'ancien')
        conserve = folder / 'garder.txt'
        conserve.write_text('garder')
        with patch('moneta.outils.serveur.factures.purge_deposit_copies') as purge:
            desktop = McpServer()
            self.assertTrue(path.exists())
            purge.assert_not_called()
            self.assertNotIn('facture_deposer', desktop.handlers)
            app = McpServer(registre=REGISTRE)
            purge.assert_called_once_with()
            self.assertFalse(path.exists())
            self.assertTrue(conserve.exists())
            self.assertIn('facture_deposer', app.handlers)
        conserve.unlink()

    def test_arguments_avant_gestionnaire(self):
        """Repris de Smith (test_app_photo, spec 009) : un argument faux est refusé avant le gestionnaire, jamais appelé."""
        with patch('moneta.outils.serveur.factures.purge_deposit_copies'):
            server = McpServer(registre=REGISTRE)
        self.assertIn('facture_deposer', REGISTRE.agent('moneta').sans_autrui)
        for args in ({}, {'import': 'a' * 31}, {'import': 'A' * 32}, {'import': 'a' * 32, 'nom': 'x'}, {'import': []}):
            with self.subTest(args=args), patch.dict(server.handlers, facture_deposer=lambda a: self.fail('appel interdit')):
                rep = server.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'outils/appeler',
                                     'params': {'agent': 'moneta', 'outil': 'facture_deposer', 'arguments': args}})
                self.assertEqual(rep['error']['code'], -32602)



class TestTableauDeBordSansPaiements(unittest.TestCase):
    """Repris de Smith (test_frontiere_paiements, spec 011) : paiements du Dojo illisibles, seules les sources qui en
    dépendent sont indisponibles, sans zéro; les autres restent identiques."""

    def test_seules_les_sources_liees_sont_indisponibles(self):
        import tempfile
        from socle import confirmation
        from moneta.scripts.moneta_mcp import serveur_fictif
        dependantes = ['revenus_dojo_annee_total', 'revenus_dojo_annee']
        autres = ['depenses_dojo_annee_total', 'depenses_dojo_annee', 'cadran_moneta', 'points_solde']
        with tempfile.TemporaryDirectory() as dossier, patch.object(confirmation, 'AUTOMATIC', True), \
                patch.dict(os.environ):
            server = serveur_fictif(dossier, registre=REGISTRE)
            lire = lambda: server.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'sources/lire', 'params': {
                'agent': 'moneta', 'sources': dependantes + autres}})['result']['sources']
            lu = lire()
            for nom in dependantes + autres:
                self.assertNotEqual(lu[nom].get('indisponible'), 'lecture impossible', nom)
            avant = {n: lu[n] for n in autres}
            os.environ['MONETA_LECTURE_PAIEMENTS'] = str(Path(dossier) / 'aucun-programme')
            lu = lire()
        self.assertEqual(lu['revenus_dojo_annee_total'], {'type': 'nombre', 'indisponible': 'lecture impossible'})
        self.assertEqual(lu['revenus_dojo_annee'], {'type': 'liste', 'indisponible': 'lecture impossible'})
        self.assertEqual({n: lu[n] for n in autres}, avant)


class TestFictifJamaisReel(unittest.TestCase):
    """Un dossier temporaire sous ~/_PersonalAgents (TMPDIR illisible, repli de Python sur le dossier courant) : MONETA_HOME
    n'y est pas suivi, le serveur fictif s'arrête avant d'écrire quoi que ce soit, au lieu de prendre le vrai dossier."""

    def test_dossier_sous_personnel_refuse(self):
        from moneta import chemins
        from moneta.scripts.moneta_mcp import serveur_fictif
        dossier = chemins.PERSONNEL / 'moneta-mcp-fictif-jamais-cree'
        with patch.dict(os.environ), patch('moneta.scripts.moneta_mcp.migrate') as migre, \
                patch('moneta.scripts.moneta_mcp.McpServer') as serveur, self.assertRaises(SystemExit) as arret:
            serveur_fictif(str(dossier), registre=REGISTRE)
        self.assertEqual(arret.exception.code, 2)
        migre.assert_not_called()
        serveur.assert_not_called()
        self.assertFalse(dossier.exists())


if __name__ == '__main__':
    unittest.main()
