"""Budget familial du compte Perso (spec 004) : budget en vigueur par catégorie et par mois, dépensé, cumul de l'année, et ce
qui rend la comparaison incomplète. Lecture seule, rien n'est gardé : tout est recalculé à chaque demande (FR-013).

Module pur : une connexion en lecture et la date du jour en entrée, un dictionnaire en sortie, pour le chat comme pour le
planificateur plus tard (D077). Seuls des totaux par catégorie et par mois, des dates et des aperçus masqués en sortent :
aucune opération, aucune description, aucun numéro de carte, jamais le titulaire d'une opération (FR-004, FR-015).

Une version de budget vaut à partir de son mois de début; la ligne non remplacée de plus grand début <= M est celle du mois
M (montant NULL : retiré). Le dépensé d'un mois est la somme des opérations classées perso des comptes liés à Perso dont la
date tombe dans le mois civil, cartes et titulaires confondus; un crédit le diminue. Les montants restent en cents entiers
et les pourcentages en fractions jusqu'à l'affichage.
"""
from calendar import monthrange
from datetime import date
from fractions import Fraction

from finances.releves import chaine

FIRST = '2026-01'  # the chain of statements starts on 2026-01-01
MAX_CENTS = 10_000_000  # 100 000 $
OUTSIDE = ('revenus', 'virement_interne', 'depot_dojo')  # never a spending category (FR-008)
HISTORY = 100
NO_STATEMENT = "Aucun relevé du compte Perso importé : le dépensé est vide, la comparaison ne dit rien encore."
# Why complet is false, in the order Ugo reads them (essai dans Claude Desktop, 2026-10-24).
REASONS = (('aucun_releve', 'aucun relevé du compte Perso'), ('a_classer', 'opérations à classer'),
           ('incomplet', 'relevés manquants, en retard ou soldes qui ne se suivent pas'), ('mois_en_cours', 'mois en cours'))
NOTES = ["Chiffres seulement : aucun conseil, aucun budget proposé; les budgets viennent d'Ugo.",
         'Dépensé = sorties moins crédits des opérations classées du compte Perso, par mois civil de leur date, toutes cartes '
         'et tous titulaires confondus; dépenses du dojo, revenus, virements internes et dépôts du dojo exclus.',
         'a_classer : opérations du compte Perso pas encore classées, jamais réparties dans une catégorie.',
         'mois_en_cours : le prorata est un repère, pas un dépassement; depassees se juge contre le budget entier.']


def shift(today, months):
    """AAAA-MM, months after the month of today."""
    n = today.year * 12 + today.month - 1 + months
    return f'{n // 12:04d}-{n % 12 + 1:02d}'


def money(cents):
    """« 1 000,00 $ » : dollars as Ugo reads them."""
    whole, part = divmod(abs(cents), 100)
    return ('-' if cents < 0 else '') + f'{whole:,}'.replace(',', ' ') + f',{part:02d} $'


def dollars(cents):
    return round(cents / 100, 2)


def round_half_up(value):
    """A Fraction to the nearest integer, halves up."""
    return (value.numerator * 2 + value.denominator) // (value.denominator * 2)


def percent(part, whole):
    """Whole percent, half up, from exact fractions."""
    return round_half_up(Fraction(part * 100, whole))


def in_force(con, category_id, month):
    """The line in force for a category in a month (AAAA-MM): {id, debut, montant_cents}, montant NULL when removed; or None."""
    row = con.execute('SELECT id, debut, montant_cents FROM budgets WHERE categorie_perso_id=? AND remplace_le IS NULL AND debut<=? '
                      'ORDER BY debut DESC LIMIT 1', (category_id, month)).fetchone()
    return dict(row) if row else None


def versions(con, category_id):
    """The lines not replaced of a category, oldest start first: [{debut, montant_cents | None}]."""
    return [{'debut': r['debut'], 'montant_cents': r['montant_cents']}
            for r in con.execute('SELECT debut, montant_cents FROM budgets WHERE categorie_perso_id=? AND remplace_le IS NULL '
                                 'ORDER BY debut', (category_id,))]


def months_of(year, wanted, today):
    """The months of the period: one month, or January to the current month (this year) or to December (a past year)."""
    if wanted is not None:
        return [f'{year:04d}-{wanted:02d}']
    last = today.month if year == today.year else 12
    return [f'{year:04d}-{m:02d}' for m in range(1, last + 1)]


def bounds(month):
    year, number = map(int, month.split('-'))
    return date(year, number, 1), date(year, number, monthrange(year, number)[1])


def incomplete(con, months, today):
    """[{apercu, mois, raison}] for every account linked to Perso and every month of the period that the chain of statements
    (spec 003) shows incomplete: a hole (days not covered, the last day of the month included), a break, or lateness.
    Dates and masked previews only, like releves_manquants."""
    current = shift(today, 0)
    found = []
    accounts = con.execute("SELECT b.id, b.apercu FROM comptes_bancaires b JOIN comptes c ON c.id=b.compte_id WHERE c.nom='Perso' "
                           'ORDER BY b.apercu').fetchall()
    for account in accounts:
        rows = chaine.periods(con, account['id'])
        found_chain = chaine.chain(rows, today)
        covered_to = max([date.fromisoformat(r[1]) for r in rows], default=None)
        late = found_chain.get('en_retard_depuis')
        for month in months:
            first, last = bounds(month)
            reasons = []
            if any(date.fromisoformat(h['du']) <= last and date.fromisoformat(h['au']) >= first for h in found_chain['trous']):
                reasons.append('relevé manquant')
            if any(b['fin'][:7] == month or b['debut'][:7] == month for b in found_chain['ruptures']):
                reasons.append('soldes qui ne se suivent pas')
            if late and month >= late[:7]:
                reasons.append('en retard')
            elif month < current and (covered_to is None or covered_to < last) and 'relevé manquant' not in reasons:
                # Days after the last statement: not a hole of the chain yet, but the month is not all there.
                reasons.append('relevé manquant')
            found += [{'apercu': account['apercu'], 'mois': month, 'raison': reason} for reason in reasons]
    return found


def state(con, today, year, wanted=None, historique=False):
    """The budget of a month (wanted) or of the year to date, against the spending of the Perso account."""
    months = months_of(year, wanted, today)
    period = {'annee': year, 'mois': wanted} if wanted is not None else {'annee': year, 'du': months[0], 'au': months[-1]}
    # Inactive categories too: one that has spending or a budget in the period is shown, the others leave no line.
    categories = con.execute('SELECT id, code, nom FROM categories_perso WHERE code NOT IN (?, ?, ?) ORDER BY id', OUTSIDE).fetchall()
    # Sums by category and calendar month of the date only: the query never reads a description, a card nor its holder.
    spent = {(r['code'], r['mois']): -r['total'] for r in con.execute(
        "SELECT p.code, substr(o.date, 1, 7) AS mois, sum(o.montant_cents) AS total FROM operations o "
        "JOIN comptes_bancaires b ON b.id=o.compte_bancaire_id JOIN comptes c ON c.id=b.compte_id "
        "JOIN categories_perso p ON p.id=o.categorie_perso_id WHERE c.nom='Perso' AND o.classe='perso' "
        "AND substr(o.date, 1, 7) BETWEEN ? AND ? GROUP BY p.code, mois", (months[0], months[-1]))}
    lines, without, total_spent, total_budget = [], [], 0, 0
    current = shift(today, 0)
    in_progress = current in months
    prorata = []
    for category in categories:
        budgets = {m: (in_force(con, category['id'], m) or {}).get('montant_cents') for m in months}
        counted = [m for m in months if budgets[m] is not None]
        outside = sum(spent.get((category['code'], m), 0) for m in months if m not in counted)
        if outside:
            without.append({'categorie': category['code'], 'nom': category['nom'], 'depense': dollars(outside)})
        if not counted:
            continue
        cents = sum(spent.get((category['code'], m), 0) for m in counted)
        planned = sum(budgets[m] for m in counted)
        line = {'categorie': category['code'], 'nom': category['nom'], 'depense': dollars(cents), 'budget': dollars(planned),
                'ecart': dollars(planned - cents), 'pourcentage': percent(cents, planned), 'depassee': cents > planned}
        if wanted is None and counted[0] != months[0]:
            line['depuis'] = counted[0]
        if wanted is None and counted != months[months.index(counted[0]):months.index(counted[-1]) + 1]:
            line['mois_comptes'] = counted
        lines.append(line)
        total_spent, total_budget = total_spent + cents, total_budget + planned
        if in_progress and budgets[current] is not None:
            prorata.append((category['code'], budgets[current]))
    pending = con.execute("SELECT count(*), coalesce(-sum(o.montant_cents), 0) FROM operations o "
                          "JOIN comptes_bancaires b ON b.id=o.compte_bancaire_id JOIN comptes c ON c.id=b.compte_id "
                          "WHERE c.nom='Perso' AND o.classe IS NULL AND substr(o.date, 1, 7) BETWEEN ? AND ?",
                          (months[0], months[-1])).fetchone()
    warnings = {}
    if pending[0]:
        warnings['a_classer'] = {'nombre': pending[0], 'montant': dollars(pending[1])}
    if not con.execute("SELECT 1 FROM comptes_bancaires b JOIN comptes c ON c.id=b.compte_id WHERE c.nom='Perso'").fetchone():
        # No account linked to Perso: no chain to follow, nothing would say the months are empty.
        warnings['aucun_releve'] = NO_STATEMENT
    missing = incomplete(con, months, today)
    if missing:
        warnings['incomplet'] = missing
    if in_progress and prorata:
        days = monthrange(today.year, today.month)[1]

        def share(cents):
            return dollars(round_half_up(Fraction(cents * today.day, days)))
        warnings['mois_en_cours'] = {'jours_ecoules': today.day, 'jours': days, 'budget_au_prorata': share(sum(c for _, c in prorata)),
                                     'par_categorie': [{'categorie': code, 'budget_au_prorata': share(c)} for code, c in prorata]}
    elif in_progress:
        days = monthrange(today.year, today.month)[1]
        warnings['mois_en_cours'] = {'jours_ecoules': today.day, 'jours': days}
    out = {'periode': period}
    if lines:
        out.update(categories=lines, total={'depense': dollars(total_spent), 'budget': dollars(total_budget),
                                            'ecart': dollars(total_budget - total_spent),
                                            'pourcentage': percent(total_spent, total_budget)},
                   depassees=[line['categorie'] for line in lines if line['depassee']])
    else:
        out['message'] = 'Aucun budget fixé.'
    out.update(sans_budget=without, avertissements=warnings, complet=not warnings)
    if warnings:
        out['raisons_incomplet'] = [text for key, text in REASONS if key in warnings]
    if historique:
        out['historique'] = history(con, year)
    out['notes'] = NOTES
    return out


def history(con, year):
    """The versions in force during the year: those starting in it (replaced ones too) and, per category, the last one that
    started before January. Never a line of a later year. 100 at most."""
    rows = con.execute(
        "SELECT p.code, b.debut, b.montant_cents, b.cree_le, b.remplace_le FROM budgets b JOIN categories_perso p "
        "ON p.id=b.categorie_perso_id WHERE substr(b.debut, 1, 4)=? OR b.id IN (SELECT (SELECT id FROM budgets x "
        "WHERE x.categorie_perso_id=b2.categorie_perso_id AND x.remplace_le IS NULL AND x.debut<? ORDER BY x.debut DESC LIMIT 1) "
        "FROM budgets b2 WHERE NOT EXISTS (SELECT 1 FROM budgets y WHERE y.categorie_perso_id=b2.categorie_perso_id "
        "AND y.remplace_le IS NULL AND y.debut=?)) ORDER BY p.id, b.debut, b.id LIMIT ?", (str(year), f'{year:04d}-01', f'{year:04d}-01', HISTORY)).fetchall()
    return [{'categorie': r['code'], 'debut': r['debut'],
             # One type per field: a removal has no amount, its action says so.
             'montant': dollars(r['montant_cents']) if r['montant_cents'] is not None else None,
             'action': 'fixe' if r['montant_cents'] is not None else 'retrait', 'cree_le': r['cree_le'][:10],
             **({'remplace_le': r['remplace_le'][:10]} if r['remplace_le'] else {})} for r in rows]
