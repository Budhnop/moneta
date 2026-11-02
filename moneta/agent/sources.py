"""Sources de lecture de Moneta : totaux et détails annuels du compte Dojo.
Les descriptions des détails sont marquées texte externe par l'adaptateur."""
from contextlib import closing
from datetime import date
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from moneta.chemins import finances_db  # noqa: E402
from moneta.lecture_dojo import lignes_annee, totaux_annee  # noqa: E402
from moneta.finances.finances import points_read, connect
from moneta.finances.banque import filtres_operations, TABLES_OPERATIONS
from moneta.finances.releves.chaine import PERIODES_PDF, PERIODES_OFX
from moneta.outils.factures import inbox
from moneta.agent.cadrans import lire_anneaux  # noqa: E402


def points(cle, sinon):
    state = points_read(finances_db(), {})
    valeur = (state.get('solde') if cle == 'solde' else state.get('objectif')) or {}
    valeur = valeur.get('points' if cle == 'solde' else 'cible')
    return {'type': 'nombre', 'valeur': valeur} if valeur is not None else {'type': 'nombre', 'indisponible': sinon}


def total(sql, annee):
    con = sqlite3.connect(finances_db().resolve().as_uri() + '?mode=ro', uri=True)
    with closing(con):
        con.execute('PRAGMA query_only=ON')
        return con.execute(sql, (str(annee),)).fetchone()[0]


def revenus(today):
    """Paiements du dojo (lecture de Dojo) et autres revenus du compte Dojo, année civile, sans le comptant (D058)."""
    paiements, _ = totaux_annee(today.year)
    autres = total("SELECT coalesce(sum(r.montant_cents), 0) FROM revenus r JOIN comptes c ON c.id = r.compte_id "
                   "WHERE r.annule = 0 AND c.nom = 'Dojo' AND coalesce(r.mode, '') <> 'comptant' AND substr(r.date, 1, 4) = ?",
                   today.year)
    return {'type': 'nombre', 'valeur': (paiements['virement'] + paiements['non_precise'] + autres) / 100}


def depenses(today):
    cents = total("SELECT coalesce(sum(d.montant_cents), 0) FROM depenses d JOIN comptes c ON c.id = d.compte_id "
                  "WHERE d.annulee = 0 AND c.nom = 'Dojo' AND substr(d.date, 1, 4) = ?", today.year)
    return {'type': 'nombre', 'valeur': cents / 100}


def detail_annee(today, genre):
    """Même périmètre que les totaux : compte Dojo, année civile, revenus sans comptant."""
    with closing(connect(finances_db(), readonly=True)) as con:
        if genre == 'revenus':
            rows = con.execute("SELECT r.date, r.description, r.montant_cents FROM revenus r JOIN comptes c ON c.id=r.compte_id "
                               "WHERE r.annule=0 AND c.nom='Dojo' AND coalesce(r.mode, '') <> 'comptant' "
                               "AND substr(r.date, 1, 4)=? ORDER BY r.date, r.id", (str(today.year),)).fetchall()
        else:
            rows = con.execute("SELECT d.date, d.description, d.montant_cents FROM depenses d JOIN comptes c ON c.id=d.compte_id "
                               "WHERE d.annulee=0 AND c.nom='Dojo' AND substr(d.date, 1, 4)=? ORDER BY d.date, d.id",
                               (str(today.year),)).fetchall()
    lignes = [[r[0], r[1] or '', r[2] / 100] for r in rows]
    if genre == 'revenus':
        lignes += [[jour, 'Paiement' + (' · ' + famille if famille else ''), cents / 100] for jour, famille, cents in lignes_annee(today.year)]
    # Ne jamais présenter une année partielle comme la liste entière.
    if len(lignes) > 10000:
        return {'type': 'liste', 'indisponible': 'trop de lignes pour le détail annuel'}
    return {'type': 'liste', 'colonnes': ['date', 'description', 'montant'], 'lignes': sorted(lignes, key=lambda r: r[0])}


def operations_classees(today):
    with closing(connect(finances_db(), readonly=True)) as con:
        def compte(statut=None):
            where, params = filtres_operations({'annee': today.year, 'statut': statut})
            return con.execute('SELECT count(*)' + TABLES_OPERATIONS + where, params).fetchone()[0]
        total = compte()
        return total - compte('a_classer'), total


def releves_recus():
    with closing(connect(finances_db(), readonly=True)) as con:
        comptes = con.execute('SELECT b.id FROM comptes_bancaires b JOIN comptes c ON c.id=b.compte_id').fetchall()
        return sum(con.execute('SELECT count(*) FROM (' + sql + ')', (c[0],)).fetchone()[0]
                   for c in comptes for sql in (PERIODES_PDF, PERIODES_OFX)), None


def cadran_moneta(today):
    return lire_anneaux((('Opérations classées', lambda: operations_classees(today)),
                        ('Factures à classer', lambda: (len(inbox()), None)), ('Relevés reçus', releves_recus)))


LECTURES = {
    'revenus_dojo_annee': lambda today: detail_annee(today, 'revenus'),
    'depenses_dojo_annee': lambda today: detail_annee(today, 'depenses'),
    'cadran_moneta': cadran_moneta,
    'points_solde': lambda today: points('solde', 'aucun relevé Cobalt'),
    'points_objectif': lambda today: points('objectif', 'aucun objectif'),
    'revenus_dojo_annee_total': revenus,
    'depenses_dojo_annee_total': depenses,
}


def lire(nom, today=None):
    return LECTURES[nom](today or date.today())


if __name__ == '__main__':
    if len(sys.argv) != 2 or sys.argv[1] not in LECTURES:
        sys.exit('Source inconnue.')
    print(json.dumps(lire(sys.argv[1]), ensure_ascii=False))
