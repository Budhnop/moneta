"""Chaîne des relevés (spec 003, FR-017) : pour chaque compte, les périodes importées doivent se suivre depuis le 1er janvier
2026 (fin + 1 jour = début du relevé suivant), et le solde de fin d'un relevé PDF doit être le solde de départ du suivant.

Rien que des dates et des aperçus masqués : aucun montant ne sort d'ici, donc pas de fenêtre « Autoriser » (D050). Une
période importée en OFX (sans soldes) compte comme couverte; aucune rupture n'est calculée à sa frontière.
"""
from datetime import date, timedelta

START = '2026-01-01'
# Past this many days after the last period covered, the next statement is late (cards have cycles of about a month).
LATE_DAYS = 35
SHOWN = 6


PERIODES_PDF = 'SELECT du, au, solde_debut_cents, solde_fin_cents FROM soldes_releves WHERE compte_bancaire_id=?'
PERIODES_OFX = ("SELECT DISTINCT i.du, i.au FROM imports i JOIN operations o ON o.import_id=i.id "
                "WHERE o.compte_bancaire_id=? AND i.format='ofx' AND i.du IS NOT NULL")


def periods(con, account_id, extra=()):
    """[(du, au, solde_debut, solde_fin)] of this account: PDF statements with balances, OFX imports without; plus extra."""
    out = [(r['du'], r['au'], r['solde_debut_cents'], r['solde_fin_cents'])
           for r in con.execute(PERIODES_PDF, (account_id,))] if account_id else []
    if account_id:
        # An OFX file covers the period it declares (DTSTART..DTEND; its first and last operations for older imports).
        out += [(r['du'], r['au'], None, None) for r in con.execute(PERIODES_OFX, (account_id,))]
    return sorted(out + list(extra))


def chain(rows, today, debut=START):
    """{premier, dernier, trous: [{du, au}], ruptures: [{fin, debut}], en_retard_depuis?} for sorted periods, from debut."""
    cursor, previous, holes, breaks = date.fromisoformat(debut), None, [], []
    for start, end, opening, closing in rows:
        first, last = date.fromisoformat(start), date.fromisoformat(end)
        if first > cursor:
            holes.append({'du': cursor.isoformat(), 'au': (first - timedelta(days=1)).isoformat()})
        elif previous and previous[3] is not None and opening is not None and first == date.fromisoformat(previous[1]) + timedelta(days=1) \
                and previous[3] != opening:
            breaks.append({'fin': previous[1], 'debut': start})
        cursor = max(cursor, last + timedelta(days=1))
        previous = (start, end, opening, closing)
    out = {'premier': rows[0][0] if rows else None, 'dernier': rows[-1][1] if rows else None, 'trous': holes, 'ruptures': breaks}
    if (today - cursor).days > LATE_DAYS:
        out['en_retard_depuis'] = cursor.isoformat()
    return out


def missing(con, data, today=None):
    """Read tool releves_manquants: every linked account (or those of one compte), with its holes, breaks and lateness."""
    today = today or date.today()
    where, params = '', ()
    if data.get('compte') is not None:
        where, params = ' WHERE c.nom=?', (data['compte'],)
    accounts = con.execute('SELECT b.id, b.apercu, b.type, c.nom FROM comptes_bancaires b JOIN comptes c ON c.id=b.compte_id'
                           + where + ' ORDER BY c.nom, b.apercu', params).fetchall()
    out = [{'apercu': a['apercu'], 'type': a['type'], 'compte': a['nom'], **chain(periods(con, a['id']), today)} for a in accounts]
    complete = all(not a['trous'] and not a['ruptures'] and 'en_retard_depuis' not in a for a in out)
    return {'depuis': START, 'comptes': out, 'complet': complete,
            'notes': ['Un trou est une période sans relevé; une rupture, un solde de départ différent du solde de fin précédent.']}


def short(found):
    """A few words for the import recap (the Mac window is 38 lines of 55 characters)."""
    items = [f"{h['du']} au {h['au']}" for h in found['trous']] + [f"rupture {b['fin']} / {b['debut']}" for b in found['ruptures']]
    return items[:SHOWN] + ([f'et {len(items) - SHOWN} autres'] if len(items) > SHOWN else [])
