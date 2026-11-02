"""Spec 010 de Smith (EF-010, EF-011) : les cas Finances du jeu figé du socle, rejoués sur Moneta (spec 013). Repris de
capturer_cas_socle.py et test_non_regression_socle.py de Smith, sans la capture.

tests/references/cas_socle contient trois fichiers de Smith (8033ee8), copiés tels quels; seuls les cas Finances y sont rejoués
ici (CAS), avec la déclaration de Moneta et son lot finances_lot. Les autres restent chez Smith, qui saute ceux-ci par leur nom.
La seule différence attendue est le champ agent du journal commun (D110), retiré par son nom comme chez Smith. Tout est fictif.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import moneta  # noqa: F401  déclare Moneta au socle : sans déclaration, le socle viserait le vrai signal
from outils_tests import SourceStore, seed_fictive_finances
from moneta.outils.mcp_finances import VARIABLE, finances_lot
from socle import confirmation, journal_ecritures, politique, signal_texte
from socle.lots import ToolError

SOURCE = '8033ee8'
REFERENCES = Path(__file__).resolve().parent / 'references' / 'cas_socle'
CAS = {'politique': ('classer sans t2125', 'classer avec t2125', 'depense', 'regle fenetre', 'annulation fenetre'),
       'journal': ('hors wr permis',), 'lot_finances': None}  # None : tous les cas de la famille
MAINTENANT = datetime(2030, 1, 15, 12, 0, tzinfo=timezone.utc)
MOMENT = datetime(2030, 1, 15, 8, 0)
T0 = 1_800_000_000.0
AGENT = re.compile(r'"agent": "moneta", ')


def sans_agent(valeur):
    if isinstance(valeur, dict):
        return {k: sans_agent(v) for k, v in valeur.items() if not (k == 'agent' and v == 'moneta' and 'id' in valeur)}
    if isinstance(valeur, list):
        return [sans_agent(v) for v in valeur]
    if isinstance(valeur, str):
        return AGENT.sub('', valeur)
    return valeur


def masquer(valeur, dossier=None):
    texte = json.dumps(valeur, ensure_ascii=False)
    if dossier:
        texte = texte.replace(str(Path(dossier).resolve()), '<dossier>').replace(str(dossier), '<dossier>')
    texte = texte.replace(str(Path.home()), '<home>')
    texte = re.sub(r'\\"code\\": \\"[0-9a-f]{6}\\"|"code": "[0-9a-f]{6}"', lambda m: re.sub(r'[0-9a-f]{6}(?=\\?"$)', '<code>', m.group(0)), texte)
    texte = re.sub(r'(\\?"id\\?": \\?")[0-9a-f]{8}', r'\1<id>', texte)
    texte = re.sub(r'\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}', lambda m: m.group(0) if m.group(0).startswith('2030-01-15T08:00') else '<heure>', texte)
    texte = texte.replace(datetime.now().date().isoformat(), '<jour>').replace(f'{datetime.now():%Y-%m}.jsonl', '<mois>.jsonl')
    return json.loads(texte)


@contextmanager
def monde():
    """Un monde fictif et vide : rien ne vise le vrai dossier, ni le vrai signal."""
    with tempfile.TemporaryDirectory(prefix='cas-socle-') as nom:
        dossier = Path(nom)
        (dossier / 'drive').mkdir()
        env = {'MONETA_HOME': str(dossier / 'home'), 'MONETA_DRIVE_DOSSIER': str(dossier / 'drive'),
               'MONETA_FINANCES_DB': str(dossier / 'finances.db')}
        with patch.dict(os.environ, env):
            os.environ.pop('UGOD_SIGNAL_TEXTE', None)
            (dossier / 'home').mkdir()
            avant = confirmation.AUTOMATIC
            confirmation.AUTOMATIC = True
            try:
                yield dossier
            finally:
                confirmation.AUTOMATIC = avant


def erreur(exc):
    return {'exception': type(exc).__name__, 'message': str(exc)}


def poser_signal(etat):
    if etat == 'absent':
        return
    if etat == 'douteux':
        fichier = signal_texte.chemin()
        fichier.parent.mkdir(parents=True, exist_ok=True)
        fichier.write_text('pas du json', encoding='utf-8')
    elif etat == 'aucun':
        signal_texte.creer_initial()
    else:
        ecoule = {'recent': 60, 'ancien': 3600}[etat]
        signal_texte.inscrire('thot', datetime.fromtimestamp(MAINTENANT.timestamp() - ecoule, timezone.utc))


def fichiers(racine):
    racine = Path(racine)
    return {p.relative_to(racine).as_posix(): p.read_text(encoding='utf-8') for p in sorted(racine.rglob('*')) if p.is_file()} if racine.is_dir() else {}


def jouer_politique(entree):
    with monde():
        poser_signal(entree.get('signal', 'aucun'))
        d = politique.decider(entree['lot'], entree['etapes'], maintenant=MAINTENANT, version_app=entree.get('version_app'))
        return {'mode': d.mode, 'raisons': list(d.raisons), 'montant': str(d.montant), 'etapes': d.etapes,
                'libelles': politique.libelles(d)}


class Dec:
    def __init__(self, mode='go', raisons=(), montant='10.00'):
        self.mode, self.raisons = mode, raisons
        self.montant = politique.Decimal(montant) if montant is not None else None


def jouer_journal(entree):
    with monde() as dossier:
        sortie = []
        for action in entree['actions']:
            assert action['quoi'] == 'intention', action  # seul cas Finances du journal : une intention
            try:
                d = Dec(action.get('mode', 'go'), tuple(action.get('raisons', ())), action.get('montant', '10.00'))
                journal_ecritures.intention(action['outil'], action.get('lot', 'lot'), d, action.get('etapes', []), MOMENT,
                                            origine=action.get('origine', 'desktop'))
                sortie.append('ok')
            except Exception as exc:
                sortie.append(erreur(exc))
        return masquer({'sortie': sortie, 'fichiers': fichiers(journal_ecritures.dossier())}, dossier)


class Horloge:
    def __init__(self):
        self.t = T0

    def __call__(self):
        return self.t


def jouer_lot(entree):
    with monde() as dossier:
        horloge = Horloge()
        db = seed_fictive_finances(dossier)
        lot = finances_lot(SourceStore(db, dossier / 'backups', variable=VARIABLE))
        lot.clock = horloge
        # Les lots lisent le signal à l'heure réelle : un signal récent s'écrit donc à l'heure réelle.
        signal_texte.inscrire('thot') if entree.get('signal') == 'recent' else poser_signal(entree.get('signal', 'aucun'))
        fenetres, sortie, code = [], [], None
        reponse = {'valeur': entree.get('clic', True)}

        def fenetre(titre, montre, script, bouton):
            fenetres.append({'titre': titre, 'texte': montre})
            return reponse['valeur']
        confirmation.AUTOMATIC = False
        with patch.object(confirmation, 'dialog', fenetre):
            for action in entree['actions']:
                nom = action['outil']
                horloge.t += action.get('avance', 0)
                arguments = action.get('args', {})
                if nom == lot.confirm:
                    arguments = {'code': action.get('code') or code}
                try:
                    if action.get('panne'):
                        panne = {'Exception': RuntimeError, 'BaseException': KeyboardInterrupt}[action['panne']]
                        with patch.object(lot.store, 'commit', side_effect=panne('panne')):
                            resultat = lot.handlers[nom](arguments)
                    else:
                        resultat = lot.handlers[nom](arguments)
                except ToolError as exc:
                    resultat = {'refus': str(exc)}
                except BaseException as exc:
                    resultat = {'levee': type(exc).__name__}
                if isinstance(resultat, dict) and 'code' in resultat:
                    code = resultat['code']
                sortie.append(resultat)
        with sqlite3.connect(db) as con:
            base = {q: con.execute(q).fetchone()[0] for q in entree.get('requetes', [])}
        os.environ.pop(VARIABLE, None)
        return masquer({'sortie': sortie, 'fenetres': fenetres, 'base': base, 'sauvegardes': len(list((dossier / 'backups').glob('*.db'))) if not entree.get('sans_sauvegardes') else None,
                        'journal': fichiers(journal_ecritures.dossier())}, dossier)


JOUEURS = {'politique': jouer_politique, 'journal': jouer_journal, 'lot_finances': jouer_lot}


def cas(famille):
    contenu = json.loads((REFERENCES / f'{famille}.json').read_text(encoding='utf-8'))
    assert (contenu['source'], contenu['famille']) == (SOURCE, famille)
    return [c for c in contenu['cas'] if CAS[famille] is None or c['nom'] in CAS[famille]]


class TestSocleFinances(unittest.TestCase):
    def test_le_jeu_est_complet(self):
        self.assertEqual({p.stem for p in REFERENCES.glob('*.json')}, set(CAS))
        for famille, noms in CAS.items():
            with self.subTest(famille=famille):
                trouves = [c['nom'] for c in cas(famille)]
                self.assertEqual(len(trouves), len(noms) if noms else 9)
                self.assertEqual(len(set(trouves)), len(trouves))

    def rejouer(self, famille):
        for c in cas(famille):
            with self.subTest(cas=c['nom']):
                # Passe par JSON comme l'attendu : un tuple redevient une liste, des deux côtés.
                self.assertEqual(sans_agent(json.loads(json.dumps(JOUEURS[famille](c['entree']), ensure_ascii=False))), c['attendu'])


for _famille in CAS:
    setattr(TestSocleFinances, f'test_{_famille}', lambda self, famille=_famille: self.rejouer(famille))


if __name__ == '__main__':
    unittest.main()
