"""Finances writer and reader, whitelisted CLI. Money is integer cents; each command takes one JSON argument.

Writes print "OK : ..." on success (the MCP lot checks it); reads print JSON. Dojo income comes from the reading Dojo exposes (agents.dojo.paiements).
"""
from contextlib import closing
from datetime import date, datetime
import json
import re
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.paths import finances_db  # noqa: E402
from agents.dojo.paiements import totaux_annee  # noqa: E402
from core.factures import valid_stored  # noqa: E402

# Always the module finances.banque, never a second copy named banque: the refusals raised by finances/releves must be
# the same class as the ones caught here.
if __package__:
    from . import banque
else:
    from finances import banque  # noqa: E402
from finances.releves import chaine  # noqa: E402
from finances import points  # noqa: E402
from finances import budget  # noqa: E402

MODES = ('comptant', 'carte', 'virement', 'cheque')
INCOME_TYPES = ('abonnement', 'equipement', 'kimono', 'guide', 'passage', 'autre')
TEXT_FIELDS = ('fournisseur', 'description', 'piece')


class Refus(ValueError):
    pass


def connect(db, readonly=False):
    if readonly:
        con = sqlite3.connect(f"file:{Path(db).resolve()}?mode=ro", uri=True)
        con.execute('PRAGMA query_only=ON')
    else:
        con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    return con


def dollars(cents):
    return round(cents / 100, 2)


def text(value, label):
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 200 or any(ord(c) < 32 for c in value):
        raise Refus(f'{label} invalide.')
    value = ' '.join(value.split())
    return value or None


def day(value, label='date'):
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError):
        raise Refus(f'{label} invalide; format AAAA-MM-JJ.') from None
    if parsed > date.today():
        raise Refus(f'{label} dans le futur.')
    return parsed.isoformat()


def fields(data, required, optional=()):
    if not isinstance(data, dict) or not set(required) <= set(data) or set(data) - set(required) - set(optional):
        raise Refus('Champs attendus : ' + ', '.join(required) + (' (optionnels : ' + ', '.join(optional) + ')' if optional else '') + '.')
    return data


def audit(con, action, target, details, kind='depense'):
    con.execute("INSERT INTO audit_log(action, cible_type, cible_id, details, source, resultat) VALUES (?, ?, ?, ?, 'assistant', 'succes')",
                (action, kind, target, details))


def categories(con):
    return [dict(r) for r in con.execute('SELECT code, nom, pct_deductible, note FROM categories WHERE actif=1 ORDER BY code')]


def reference(db):
    with closing(connect(db, readonly=True)) as con:
        return {'comptes': [r[0] for r in con.execute('SELECT nom FROM comptes WHERE actif=1 ORDER BY id')],
                'categories': categories(con), 'modes': list(MODES), 'types_de_revenu': list(INCOME_TYPES), **banque.reference(con)}


def describe(row):
    return {'id': row['id'], 'date': row['date'], 'montant': dollars(row['montant_cents']), 'categorie': row['code'],
            'categorie_nom': row['nom'], 'pct_deductible': row['pct_deductible'],
            'deductible': dollars(deductible(row['montant_cents'], row['pct_deductible'])),
            'fournisseur': row['fournisseur'], 'description': row['description'], 'mode': row['mode'], 'piece': row['piece'],
            'facture': row['facture'], 'annulee': bool(row['annulee']), 'compte': row['compte'] or 'Dojo'}


def deductible(cents, pct):
    # Half-up rounding to the cent.
    return (cents * pct + 50) // 100


# The bank operation an expense was classed from, if any: its free text follows that line's rule (spec 002, R9). By
# operation_id, set when the expense is made and kept after a reclassification undoes the link (F2, D091).
EXPENSE = '''SELECT d.*, c.code, c.nom, k.nom AS compte,
    (SELECT b.type FROM operations o JOIN comptes_bancaires b ON b.id=o.compte_bancaire_id WHERE o.id=d.operation_id) AS operation_type,
    (SELECT o.description FROM operations o WHERE o.id=d.operation_id) AS operation_description
    FROM depenses d JOIN categories c ON c.id=d.categorie_id LEFT JOIN comptes k ON k.id=d.compte_id'''


def expense(con, number):
    if type(number) is not int or number < 1:
        raise Refus('Numéro de dépense invalide.')
    row = con.execute(EXPENSE + ' WHERE d.id=?', (number,)).fetchone()
    if row is None:
        raise Refus(f'Dépense #{number} introuvable.')
    return row


def receipt(value):
    # The path inside « Agent Smith/factures », as made by core.factures; the file itself is moved by the MCP commit.
    if value is not None and not valid_stored(value):
        raise Refus('Chemin de facture invalide.')
    return value


def add_expense(db, data):
    data = fields(data, ('date', 'montant_cents', 'categorie'), ('pct', 'fournisseur', 'description', 'mode', 'piece', 'compte', 'facture'))
    facture = receipt(data.get('facture'))
    when = day(data['date'])
    cents = data['montant_cents']
    if type(cents) is not int or not 0 < cents <= 100_000_00:
        raise Refus('Montant invalide (entre 0,01 $ et 100 000 $).')
    mode = data.get('mode')
    if mode is not None and mode not in MODES:
        raise Refus('Mode invalide : ' + ', '.join(MODES) + '.')
    values = {k: text(data.get(k), k) for k in TEXT_FIELDS}
    with closing(connect(db)) as con, con:
        category = con.execute('SELECT id, code, nom, pct_deductible FROM categories WHERE code=? AND actif=1', (data['categorie'],)).fetchone()
        if category is None:
            raise Refus('Catégorie inconnue; voir finances_reference.')
        pct = data.get('pct', category['pct_deductible'])
        if pct is None:
            raise Refus(f"Pourcentage déductible requis pour {category['code']} {category['nom']}.")
        if type(pct) is not int or not 0 <= pct <= 100:
            raise Refus('Pourcentage déductible invalide (entier de 0 à 100).')
        account = con.execute('SELECT id FROM comptes WHERE nom=? AND actif=1', (data.get('compte', 'Dojo'),)).fetchone()
        if account is None:
            raise Refus('Compte inconnu.')
        try:
            number = con.execute('''INSERT INTO depenses(compte_id, date, montant_cents, categorie_id, pct_deductible, fournisseur, description,
                mode, piece, facture) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''', (account['id'], when, cents, category['id'], pct,
                values['fournisseur'], values['description'], mode, values['piece'], facture)).lastrowid
        except sqlite3.IntegrityError:
            raise Refus('Cette facture est déjà liée à une autre dépense.') from None
        audit(con, 'ajouter_depense', number, f"{when} - {cents / 100:.2f}$ - {category['code']} - {pct}%")
    return f"OK : dépense #{number} du {when}, {cents / 100:.2f} $, {category['code']} {category['nom']} ({pct} % déductible)."


def complete_expense(db, data):
    data = fields(data, ('id',), ('fournisseur', 'description', 'mode', 'piece', 'facture'))
    changes = {k: text(v, k) for k, v in data.items() if k in TEXT_FIELDS}
    if 'facture' in data:
        if data['facture'] is None:
            raise Refus('Une facture liée ne se retire pas.')
        changes['facture'] = receipt(data['facture'])
    if 'mode' in data:
        if data['mode'] is not None and data['mode'] not in MODES:
            raise Refus('Mode invalide : ' + ', '.join(MODES) + '.')
        changes['mode'] = data['mode']
    if not changes:
        raise Refus('Aucun champ à compléter.')
    with closing(connect(db)) as con, con:
        row = expense(con, data['id'])
        if row['annulee']:
            raise Refus(f"Dépense #{row['id']} annulée.")
        if 'facture' in changes and row['facture']:
            raise Refus(f"Dépense #{row['id']} : une facture est déjà liée.")
        try:
            con.execute('UPDATE depenses SET ' + ', '.join(f'{k}=?' for k in changes) + ' WHERE id=?', (*changes.values(), row['id']))
        except sqlite3.IntegrityError:
            raise Refus('Cette facture est déjà liée à une autre dépense.') from None
        audit(con, 'completer_depense', row['id'], ', '.join(f'{k}: {row[k] or "—"} -> {v or "—"}' for k, v in changes.items()))
    return f"OK : dépense #{row['id']} complétée ({', '.join(changes)})."


def cancel_expense(db, data):
    data = fields(data, ('id',))
    with closing(connect(db)) as con, con:
        row = expense(con, data['id'])
        if row['annulee']:
            raise Refus(f"Dépense #{row['id']} déjà annulée.")
        con.execute('UPDATE depenses SET annulee=1 WHERE id=?', (row['id'],))
        audit(con, 'annuler_depense', row['id'], f"{row['date']} - {row['montant_cents'] / 100:.2f}$ - {row['code']}")
    return f"OK : dépense #{row['id']} annulée (conservée dans l'historique)."


def missing(row):
    return [label for label, absent in (('piece', not row['piece'] and not row['facture']), ('fournisseur', not row['fournisseur']),
                                        ('mode', not row['mode'])) if absent]


def list_expenses(db, data):
    data = fields(data, (), ('annee', 'mois', 'categorie', 'incompletes', 'annulees', 'id'))
    where, params = [], []
    if data.get('id') is not None:
        where.append('d.id=?'); params.append(data['id'])
    if data.get('annee') is not None:
        if type(data['annee']) is not int or not 2000 <= data['annee'] <= 2100:
            raise Refus('Année invalide.')
        where.append("substr(d.date, 1, 4)=?"); params.append(str(data['annee']))
    if data.get('mois') is not None:
        if type(data['mois']) is not int or not 1 <= data['mois'] <= 12:
            raise Refus('Mois invalide.')
        where.append("substr(d.date, 6, 2)=?"); params.append(f"{data['mois']:02d}")
    if data.get('categorie') is not None:
        where.append('c.code=?'); params.append(str(data['categorie']))
    if not data.get('annulees'):
        where.append('d.annulee=0')
    with closing(connect(db, readonly=True)) as con:
        rows = con.execute(EXPENSE + (' WHERE ' + ' AND '.join(where) if where else '') + ' ORDER BY d.date, d.id', params).fetchall()
    rows = [r for r in rows if not data.get('incompletes') or missing(r)]
    out = [{**describe(r), 'manque': missing(r),
            'operation': {'type': r['operation_type'], 'description': r['operation_description']} if r['operation_type'] else None}
           for r in rows[:200]]
    return {'depenses': out, 'nombre': len(rows), 'total': dollars(sum(r['montant_cents'] for r in rows)),
            'tronque': len(rows) > 200}


def add_income(db, data):
    data = fields(data, ('date', 'montant_cents', 'type'), ('mode', 'description', 'compte'))
    when = day(data['date'])
    cents = data['montant_cents']
    if type(cents) is not int or not 0 < cents <= 100_000_00:
        raise Refus('Montant invalide (entre 0,01 $ et 100 000 $).')
    if data['type'] not in INCOME_TYPES:
        raise Refus('Type de revenu : ' + ', '.join(INCOME_TYPES) + '.')
    if data.get('mode') is not None and data['mode'] not in MODES:
        raise Refus('Mode invalide : ' + ', '.join(MODES) + '.')
    with closing(connect(db)) as con, con:
        account = con.execute('SELECT id FROM comptes WHERE nom=? AND actif=1', (data.get('compte', 'Dojo'),)).fetchone()
        if account is None:
            raise Refus('Compte inconnu.')
        number = con.execute('INSERT INTO revenus(compte_id, date, montant_cents, type, mode, description) VALUES (?, ?, ?, ?, ?, ?)',
                             (account['id'], when, cents, data['type'], data.get('mode'), text(data.get('description'), 'description'))).lastrowid
        audit(con, 'ajouter_revenu', number, f"{when} - {cents / 100:.2f}$ - {data['type']}", 'revenu')
    return f"OK : revenu #{number} du {when}, {cents / 100:.2f} $, {data['type']}."


def cancel_income(db, data):
    data = fields(data, ('id',))
    with closing(connect(db)) as con, con:
        row = con.execute('SELECT * FROM revenus WHERE id=?', (data['id'],)).fetchone() if type(data['id']) is int else None
        if row is None:
            raise Refus('Revenu introuvable.')
        if row['annule']:
            raise Refus(f"Revenu #{row['id']} déjà annulé.")
        con.execute('UPDATE revenus SET annule=1 WHERE id=?', (row['id'],))
        audit(con, 'annuler_revenu', row['id'], f"{row['date']} - {row['montant_cents'] / 100:.2f}$ - {row['type']}", 'revenu')
    return f"OK : revenu #{row['id']} annulé (conservé dans l'historique)."


def describe_income(row):
    return {'id': row['id'], 'date': row['date'], 'montant': dollars(row['montant_cents']), 'type': row['type'], 'mode': row['mode'],
            'description': row['description'], 'annule': bool(row['annule'])}


def list_income(db, data):
    data = fields(data, (), ('annee', 'mois', 'type', 'annules', 'id', 'comptant'))
    where, params = [], []
    # Cash stays out unless Ugo asks for it (D058); a lookup by number always finds the income.
    cash_out = not data.get('comptant') and data.get('id') is None
    if data.get('id') is not None:
        where.append('id=?'); params.append(data['id'])
    if data.get('annee') is not None:
        if type(data['annee']) is not int or not 2000 <= data['annee'] <= 2100:
            raise Refus('Année invalide.')
        where.append('substr(date, 1, 4)=?'); params.append(str(data['annee']))
    if data.get('mois') is not None:
        if type(data['mois']) is not int or not 1 <= data['mois'] <= 12:
            raise Refus('Mois invalide.')
        where.append('substr(date, 6, 2)=?'); params.append(f"{data['mois']:02d}")
    if data.get('type') is not None:
        where.append('type=?'); params.append(str(data['type']))
    if not data.get('annules'):
        where.append('annule=0')
    with closing(connect(db, readonly=True)) as con:
        rows = con.execute('SELECT * FROM revenus' + (' WHERE ' + ' AND '.join(where) if where else '') + ' ORDER BY date, id', params).fetchall()
    cash = [r for r in rows if cash_out and r['mode'] == 'comptant']
    rows = [r for r in rows if not (cash_out and r['mode'] == 'comptant')]
    return {'revenus': [describe_income(r) for r in rows[:200]], 'nombre': len(rows),
            'total': dollars(sum(r['montant_cents'] for r in rows)), 'tronque': len(rows) > 200,
            **({'comptant_exclu': {'nombre': len(cash), 'total': dollars(sum(r['montant_cents'] for r in cash))}} if cash_out else {})}


def other_income(con, year):
    rows = con.execute('SELECT * FROM revenus WHERE annule=0 AND substr(date, 1, 4)=? ORDER BY date, id', (str(year),)).fetchall()
    by_mode, by_type = {'comptant': 0, 'carte': 0, 'virement': 0, 'cheque': 0, 'non_precise': 0}, {}
    for r in rows:
        by_mode[r['mode'] or 'non_precise'] += r['montant_cents']
        by_type[r['type']] = by_type.get(r['type'], 0) + r['montant_cents']
    return rows, by_mode, by_type


def report(db, year, cash=False):
    if not 2000 <= year <= 2100:
        raise Refus('Année invalide.')
    income, counts = totaux_annee(year)
    with closing(connect(db, readonly=True)) as con:
        rows = con.execute(EXPENSE + " WHERE d.annulee=0 AND substr(d.date, 1, 4)=? ORDER BY c.code, d.date, d.id", (str(year),)).fetchall()
        others, others_by_mode, others_by_type = other_income(con, year)
        # A Dojo debit still to classify may be a missing T2125 expense.
        unsorted = con.execute("SELECT count(*), coalesce(-sum(o.montant_cents), 0) FROM operations o "
                               "JOIN comptes_bancaires b ON b.id=o.compte_bancaire_id JOIN comptes c ON c.id=b.compte_id "
                               "WHERE o.classe IS NULL AND o.montant_cents < 0 AND c.nom='Dojo' AND substr(o.date, 1, 4)=?",
                               (str(year),)).fetchone()
    # Cash stays out of the report unless Ugo asks for it (D058); what is left out is always shown.
    excluded = {'paiements_dojo': {'nombre': counts['comptant'], 'total': dollars(income['comptant'])},
                'autres_revenus': {'nombre': sum(1 for r in others if r['mode'] == 'comptant'), 'total': dollars(others_by_mode['comptant'])},
                'total': dollars(income['comptant'] + others_by_mode['comptant'])}
    if not cash:
        income, counts = {k: v for k, v in income.items() if k != 'comptant'}, {k: v for k, v in counts.items() if k != 'comptant'}
        others = [r for r in others if r['mode'] != 'comptant']
        others_by_mode = {k: v for k, v in others_by_mode.items() if k != 'comptant'}
        others_by_type = {}
        for r in others:
            others_by_type[r['type']] = others_by_type.get(r['type'], 0) + r['montant_cents']
    lines = {}
    for r in rows:
        line = lines.setdefault(r['code'], {'code': r['code'], 'nom': r['nom'], 'brut': 0, 'deductible': 0, 'nombre': 0})
        line['brut'] += r['montant_cents']
        line['deductible'] += deductible(r['montant_cents'], r['pct_deductible'])
        line['nombre'] += 1
    total_others = sum(r['montant_cents'] for r in others)
    total_income = sum(income.values()) + total_others
    total_gross = sum(l['brut'] for l in lines.values())
    total_deductible = sum(l['deductible'] for l in lines.values())
    incomplete = [{**describe(r), 'manque': missing(r)} for r in rows if missing(r)]
    notes = ["Estimation arithmétique, pas un conseil fiscal : à valider avec le comptable.",
             "Non inclus : amortissement (9936), bureau à domicile (9945).",
             "Revenus = paiements de dojo.db + autres revenus de finances.db (chacun une seule fois)."]
    if not cash and excluded['total']:
        amount = f"{excluded['total']:.2f}".replace('.', ',')
        notes.insert(0, f"Revenu comptant exclu de ce rapport : {amount} $ "
                        f"({excluded['paiements_dojo']['nombre']} paiement(s) du dojo, {excluded['autres_revenus']['nombre']} autre(s) revenu(s)). "
                        "Le revenu comptant reste un revenu d'entreprise : à voir avec le comptable.")
    return {
        'annee': year,
        'comptant': {'inclus': cash, 'exclu': None if cash else excluded},
        'revenus_total': dollars(total_income),
        'autres_revenus': {'total': dollars(total_others), 'nombre': len(others),
                           'par_type': {k: dollars(v) for k, v in sorted(others_by_type.items())},
                           'par_mode': {k: dollars(v) for k, v in others_by_mode.items() if v},
                           'source': 'finances.db, autres revenus'},
        'revenus_dojo': {'total': dollars(sum(income.values())), 'paiements': sum(counts.values()),
                         'par_mode': {k: dollars(v) for k, v in income.items()},
                         'source': 'dojo.db, paiements reçus, par date de paiement'},
        'depenses': {'par_ligne': [{**l, 'brut': dollars(l['brut']), 'deductible': dollars(l['deductible'])} for l in lines.values()],
                     'total_brut': dollars(total_gross), 'total_deductible': dollars(total_deductible), 'nombre': len(rows)},
        'revenu_net_estime': dollars(total_income - total_deductible),
        'a_completer': {'paiements_sans_mode': {'nombre': counts['non_precise'], 'total': dollars(income['non_precise'])},
                        'autres_revenus_sans_mode': {'nombre': sum(1 for r in others if not r['mode']),
                                                     'total': dollars(others_by_mode['non_precise'])},
                        'depenses_incompletes': incomplete,
                        'sorties_bancaires_dojo_a_classer': {'nombre': unsorted[0], 'total': dollars(unsorted[1])}},
        'notes': notes,
    }


def history(db, count):
    with closing(connect(db, readonly=True)) as con:
        rows = con.execute('SELECT date_action AS date, action, cible_id AS depense, details FROM audit_log ORDER BY id DESC LIMIT ?', (count,))
        return {'entries': [dict(r) for r in rows]}


def bank_write(function, required, optional=()):
    def write(db, data):
        data = fields(data, required, optional)
        with closing(connect(db)) as con, con:
            try:
                return function(con, data, audit)
            except banque.RefusBanque as exc:
                raise Refus(str(exc)) from None
    return write


def bank_read(function, required=(), optional=()):
    def read(db, data):
        data = fields(data, required, optional)
        for key, low, high in (('annee', 2000, 2100), ('mois', 1, 12), ('id', 1, 10**9)):
            if data.get(key) is not None and (type(data[key]) is not int or not low <= data[key] <= high):
                raise Refus(f'{key} invalide.')
        with closing(connect(db, readonly=True)) as con:
            try:
                return function(con, data)
            except banque.RefusBanque as exc:
                raise Refus(str(exc)) from None
    return read


def points_read(db, data, today=None):
    """Points and the Japan goal (spec 005): totals of points and dates only, read without Autoriser (D081)."""
    data = fields(data, (), ('historique',))
    if 'historique' in data and type(data['historique']) is not bool:
        raise Refus('historique : vrai ou faux.')
    with closing(connect(db, readonly=True)) as con:
        try:
            return points.state(con, today or date.today(), historique=data.get('historique', False))
        except points.RefusPoints as exc:
            raise Refus(str(exc)) from None


def goal_text(row):
    return f"{row['cible']:,} points".replace(',', ' ') + (f", visé le {row['date_visee']}" if row['date_visee'] else ', sans date visée')


def goal_in_force(con):
    return con.execute('SELECT id, cible, date_visee FROM objectifs_points WHERE fin_le IS NULL').fetchone()


def now():
    # Mac's local time: « depuis » is the day Ugo set the goal, not the UTC day.
    return datetime.now().isoformat(sep=' ', timespec='seconds')


def set_goal(db, data, today=None):
    """Sets or changes the Japan points goal; a changed goal stays in the history (fin = 'change'), nothing is erased."""
    data = fields(data, ('cible',), ('date_visee',))
    target, wanted = data['cible'], data.get('date_visee')
    if type(target) is not int or not 1 <= target <= 10_000_000:
        raise Refus('cible : un nombre entier de points, de 1 à 10 000 000.')
    if wanted is not None:
        if not isinstance(wanted, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', wanted):
            raise Refus('date_visee invalide; format AAAA-MM-JJ.')
        try:
            parsed = date.fromisoformat(wanted)
        except ValueError:
            raise Refus('date_visee invalide; format AAAA-MM-JJ.') from None
        if parsed <= (today or date.today()):
            # Checked again when the lot is saved: a date prepared for tomorrow before midnight can be today by then.
            raise Refus("date_visee : aujourd'hui ou passée (date du Mac à l'enregistrement); donne une date future.")
    with closing(connect(db)) as con, con:
        current = goal_in_force(con)
        if current and (current['cible'], current['date_visee']) == (target, wanted):
            raise Refus("rien ne change : c'est déjà l'objectif en vigueur.")
        when = now()
        if current:
            con.execute("UPDATE objectifs_points SET fin_le=?, fin='change' WHERE id=? AND fin_le IS NULL", (when, current['id']))
        number = con.execute('INSERT INTO objectifs_points(cible, date_visee, cree_le) VALUES (?, ?, ?)', (target, wanted, when)).lastrowid
        new = {'cible': target, 'date_visee': wanted}
        if current:
            audit(con, 'changer_objectif_points', number, f'{goal_text(current)} -> {goal_text(new)}', 'objectif_points')
        else:
            audit(con, 'fixer_objectif_points', number, goal_text(new), 'objectif_points')
    return f"OK : objectif Japon {'changé' if current else 'fixé'} : {goal_text(new)}."


def remove_goal(db, data):
    fields(data, ())
    with closing(connect(db)) as con, con:
        current = goal_in_force(con)
        if current is None:
            raise Refus('aucun objectif en vigueur.')
        con.execute("UPDATE objectifs_points SET fin_le=?, fin='retire' WHERE id=? AND fin_le IS NULL", (now(), current['id']))
        audit(con, 'retirer_objectif_points', current['id'], goal_text(current), 'objectif_points')
    return f"OK : objectif Japon retiré ({goal_text(current)}); il reste dans l'historique."


# Budget familial (spec 004) ---------------------------------------------------------------------------------------------

def month_of(value, today, label='debut'):
    """AAAA-MM from 2026-01 (start of the chain of statements) to 12 months after the Mac's current month."""
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{4}-(0[1-9]|1[0-2])', value):
        raise Refus(f'{label} invalide; format AAAA-MM.')
    if not budget.FIRST <= value <= budget.shift(today, 12):
        raise Refus(f'{label} : de {budget.FIRST} à {budget.shift(today, 12)} (12 mois après le mois courant du Mac).')
    return value


def spending_category(con, code):
    if not isinstance(code, str):
        raise Refus('categorie invalide.')
    if code in budget.OUTSIDE:
        raise Refus('categorie : seulement une catégorie de dépense perso (pas les revenus, les virements internes ni le dépôt '
                    'du dojo).')
    row = con.execute('SELECT id, code, nom FROM categories_perso WHERE code=? AND actif=1', (code,)).fetchone()
    if row is None:
        raise Refus('categorie inconnue ou inactive; voir finances_reference (catégories perso).')
    return row


def set_budget(db, data, today=None):
    """Sets or changes the monthly budget of a category from a month on; nothing is erased (FR-009): a line at the same
    month of start is marked replaced, in the same transaction, and the months before keep their budget."""
    data = fields(data, ('categorie', 'montant_cents', 'debut'))
    cents = data['montant_cents']
    if type(cents) is not int or not 1 <= cents <= budget.MAX_CENTS:
        raise Refus('montant_cents : un nombre entier de cents, de 1 à 10 000 000 (0,01 $ à 100 000 $).')
    start = month_of(data['debut'], today or date.today())
    with closing(connect(db)) as con, con:
        category = spending_category(con, data['categorie'])
        current = budget.in_force(con, category['id'], start)
        if current and current['montant_cents'] == cents:
            raise Refus(f"rien ne change : {category['nom']} est déjà à {budget.money(cents)} en {start}.")
        same = current if current and current['debut'] == start else None
        when = now()
        if same:
            con.execute('UPDATE budgets SET remplace_le=? WHERE id=? AND remplace_le IS NULL', (when, same['id']))
        number = con.execute('INSERT INTO budgets(categorie_perso_id, debut, montant_cents, cree_le) VALUES (?, ?, ?, ?)',
                             (category['id'], start, cents, when)).lastrowid
        if same:
            before = budget.money(same['montant_cents']) if same['montant_cents'] is not None else 'retiré'
            audit(con, 'changer_budget', number, f"{category['nom']} dès {start} : {before} -> {budget.money(cents)}", 'budget')
        else:
            audit(con, 'fixer_budget', number, f"{category['nom']} : {budget.money(cents)} par mois dès {start}", 'budget')
    return f"OK : budget {'changé' if same else 'fixé'} : {category['nom']}, {budget.money(cents)} par mois dès {start}."


def remove_budget(db, data, today=None):
    """Removes the budget of a category from a month on: a line without amount; the months before keep theirs."""
    data = fields(data, ('categorie', 'debut'))
    start = month_of(data['debut'], today or date.today())
    with closing(connect(db)) as con, con:
        category = spending_category(con, data['categorie'])
        current = budget.in_force(con, category['id'], start)
        if current is None or current['montant_cents'] is None:
            raise Refus(f"aucun budget en vigueur pour {category['code']} en {start}.")
        when = now()
        if current['debut'] == start:
            con.execute('UPDATE budgets SET remplace_le=? WHERE id=? AND remplace_le IS NULL', (when, current['id']))
        number = con.execute('INSERT INTO budgets(categorie_perso_id, debut, montant_cents, cree_le) VALUES (?, ?, NULL, ?)',
                             (category['id'], start, when)).lastrowid
        audit(con, 'retirer_budget', number, f"{category['nom']} : retiré dès {start} (était {budget.money(current['montant_cents'])})",
              'budget')
    return f"OK : budget retiré : {category['nom']} à partir de {start}; les mois d'avant le gardent."


def budget_read(db, data, today=None):
    """The state of the budget (spec 004): {annee, mois?, historique?}; or the versions of one category for the preparers,
    {categorie}. Totals by category and month only, opened read-only; the MCP server asks Autoriser before it shows them."""
    if isinstance(data, dict) and 'categorie' in data:
        data = fields(data, ('categorie',))
        with closing(connect(db, readonly=True)) as con:
            return {'lignes': budget.versions(con, spending_category(con, data['categorie'])['id'])}
    data = fields(data, ('annee',), ('mois', 'historique'))
    today = today or date.today()
    year, wanted = data['annee'], data.get('mois')
    if type(year) is not int or not 2026 <= year <= 2100:
        raise Refus('annee invalide (2026 au plus tôt).')
    if year > today.year:
        raise Refus('annee dans le futur.')
    if wanted is not None and (type(wanted) is not int or not 1 <= wanted <= 12):
        raise Refus('mois invalide (1 à 12).')
    if wanted is not None and year == today.year and wanted > today.month:
        raise Refus('mois dans le futur.')
    if 'historique' in data and type(data['historique']) is not bool:
        raise Refus('historique : vrai ou faux.')
    with closing(connect(db, readonly=True)) as con:
        return budget.state(con, today, year, wanted, historique=data.get('historique', False))


WRITES = {'depense': add_expense, 'completer': complete_expense, 'annuler': cancel_expense,
          'revenu': add_income, 'annuler_revenu': cancel_income,
          'importer': bank_write(banque.import_file, ('fichier', 'empreinte'), ('comptes',)),
          'classer': bank_write(banque.classify, ('id', 'categorie'), ('pct',)),
          'regle': bank_write(banque.add_rule, ('motif', 'categorie'), ('pct',)),
          'retirer_regle': bank_write(banque.remove_rule, ('id',)),
          'objectif': set_goal, 'retirer_objectif': remove_goal,
          'budget_fixer': set_budget, 'budget_retirer': remove_budget}
READS = {'depenses': list_expenses, 'revenus': list_income,
         'import_analyser': bank_read(banque.analyse, ('fichier',), ('comptes',)),
         'regle_analyser': bank_read(lambda con, data: {'motif': (r := banque.check_rule(con, data))[0], 'categorie': r[1], 'pct': r[2],
                                                         'operations': [banque.describe(o) for o in r[3]]},
                                     ('motif', 'categorie'), ('pct',)),
         'operations': bank_read(banque.operations, (), ('id', 'annee', 'mois', 'compte', 'statut', 'categorie')),
         'resume': bank_read(banque.overview, ('annee',), ('mois', 'compte')),
         'manquants': bank_read(chaine.missing, (), ('compte',)),
         'points': points_read, 'budget': budget_read}


def main(argv):
    db = finances_db()
    try:
        if len(argv) == 1 and argv[0] == 'reference':
            print(json.dumps(reference(db), ensure_ascii=False))
        elif len(argv) == 2 and argv[0] in WRITES:
            print(WRITES[argv[0]](db, json.loads(argv[1])))
        elif len(argv) == 2 and argv[0] in READS:
            print(json.dumps(READS[argv[0]](db, json.loads(argv[1])), ensure_ascii=False))
        elif len(argv) in (2, 3) and argv[0] == 'rapport' and argv[1].isdigit() and argv[2:] in ([], ['comptant']):
            print(json.dumps(report(db, int(argv[1]), cash=argv[2:] == ['comptant']), ensure_ascii=False))
        elif len(argv) == 2 and argv[0] == 'historique' and argv[1].isdigit() and 1 <= int(argv[1]) <= 100:
            print(json.dumps(history(db, int(argv[1])), ensure_ascii=False))
        else:
            print('ERREUR : commande invalide.')
            return 1
    except Refus as exc:
        print(f'ERREUR : {exc}')
        return 1
    except (ValueError, sqlite3.Error):
        print('ERREUR : finances indisponibles ou données invalides.')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
