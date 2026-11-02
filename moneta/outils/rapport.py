"""Printable annual report: one self-contained HTML page in the private exports folder."""
from datetime import datetime
from html import escape

from core.paths import exports, private_folder, write_private

MODES = {'comptant': 'Comptant', 'virement': 'Virement', 'non_precise': 'Mode non précisé'}
MISSING = {'piece': 'reçu', 'fournisseur': 'fournisseur', 'mode': 'mode de paiement'}


def money(value):
    whole, cents = f"{abs(value):,.2f}".split('.')
    return ('-' if value < 0 else '') + whole.replace(',', ' ') + ',' + cents + ' $'


def rows(cells_list):
    return ''.join('<tr>' + ''.join(f'<td class="{cls}">{escape(str(text))}</td>' for cls, text in cells) + '</tr>'
                   for cells in cells_list)


def render(report):
    income, spent, todo = report['revenus_dojo'], report['depenses'], report['a_completer']
    others = report['autres_revenus']
    income_rows = rows([[('', f'Inscriptions — {MODES[k].lower()}'), ('n', money(v))] for k, v in income['par_mode'].items() if v]
                       + [[('', f'Autres revenus — {kind}'), ('n', money(v))] for kind, v in others['par_type'].items()]
                       + [[('total', 'Total des revenus'), ('n total', money(report['revenus_total']))]])
    spent_rows = rows([[('', line['code']), ('', line['nom']), ('n', line['nombre']), ('n', money(line['brut'])), ('n', money(line['deductible']))]
                       for line in spent['par_ligne']]
                      + [[('total', ''), ('total', 'Total'), ('n total', spent['nombre']), ('n total', money(spent['total_brut'])),
                          ('n total', money(spent['total_deductible']))]]) if spent['par_ligne'] else \
        '<tr><td colspan="5">Aucune dépense enregistrée pour cette année.</td></tr>'
    incomplete = todo['depenses_incompletes']
    todo_items = []
    if todo['paiements_sans_mode']['nombre']:
        todo_items.append(f"{todo['paiements_sans_mode']['nombre']} paiement(s) du dojo sans mode (comptant ou virement), "
                          f"{money(todo['paiements_sans_mode']['total'])}.")
    if todo['autres_revenus_sans_mode']['nombre']:
        todo_items.append(f"{todo['autres_revenus_sans_mode']['nombre']} autre(s) revenu(s) sans mode, "
                          f"{money(todo['autres_revenus_sans_mode']['total'])}.")
    unsorted = todo.get('sorties_bancaires_dojo_a_classer', {'nombre': 0})
    if unsorted['nombre']:
        todo_items.append(f"{unsorted['nombre']} sortie(s) du compte bancaire Dojo encore à classer, {money(unsorted['total'])} "
                          "(peut-être des dépenses à déclarer).")
    todo_items += [f"Dépense #{d['id']} du {d['date']}, {money(d['montant'])}, {d['categorie']} : manque {', '.join(MISSING[m] for m in d['manque'])}."
                   for d in incomplete]
    todo_html = ''.join(f'<li>{escape(item)}</li>' for item in todo_items) or '<li>Rien à compléter.</li>'
    notes = ''.join(f'<li>{escape(n)}</li>' for n in report['notes'] if not n.startswith('Revenu comptant exclu'))
    # Cash left out is said at the top of the page, never only in the notes (D058).
    left_out = report['comptant']['exclu']
    cash_html = '' if left_out is None else (
        f'<p class="cash">Revenu comptant exclu de ce rapport : {money(left_out["total"])} '
        f'({left_out["paiements_dojo"]["nombre"]} paiement(s) du dojo, {left_out["autres_revenus"]["nombre"]} autre(s) revenu(s)).'
        f'{" Le revenu comptant reste un revenu d’entreprise : à voir avec le comptable." if left_out["total"] else ""}</p>')
    scope = 'revenu comptant inclus' if left_out is None else 'sans le revenu comptant'
    return f"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Rapport financier {report['annee']}</title>
<style>
:root {{ color-scheme: light; --ink:#1d1d1f; --muted:#6e6e73; --line:#d2d2d7; --band:#f5f5f7; }}
body {{ font: 14px/1.45 -apple-system, "Helvetica Neue", Arial, sans-serif; color: var(--ink); background: #fff; max-width: 820px; margin: 32px auto; padding: 0 16px; }}
h1 {{ font-size: 22px; margin: 0 0 4px; }} h2 {{ font-size: 16px; margin: 28px 0 8px; }}
.sub {{ color: var(--muted); margin: 0 0 20px; }}
table {{ width: 100%; border-collapse: collapse; }}
td {{ padding: 6px 8px; border-bottom: 1px solid var(--line); vertical-align: top; }}
td.n {{ text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }}
td.total {{ font-weight: 600; background: var(--band); }}
.net {{ margin-top: 24px; padding: 12px 14px; background: var(--band); border-radius: 8px; display: flex; justify-content: space-between; font-weight: 600; }}
.cash {{ margin: 0 0 20px; padding: 10px 14px; border: 2px solid #b25000; border-radius: 8px; font-weight: 600; }}
ul {{ padding-left: 20px; }} li {{ margin: 3px 0; }} .notes {{ color: var(--muted); font-size: 12px; }}
@media print {{ body {{ margin: 0; max-width: none; }} .net, td.total {{ -webkit-print-color-adjust: exact; print-color-adjust: exact; }} }}
</style></head><body>
<h1>Rapport financier {report['annee']} — Dojo</h1>
<p class="sub">Entreprise individuelle · généré le {datetime.now():%Y-%m-%d à %H:%M} · lignes du formulaire T2125 · {scope}</p>
{cash_html}
<h2>Revenus ({income['paiements']} paiement(s) d'inscription, {others['nombre']} autre(s) revenu(s))</h2>
<table>{income_rows}</table>
<h2>Dépenses par ligne</h2>
<table><tr><td class="total">Ligne</td><td class="total">Catégorie</td><td class="n total">Nombre</td><td class="n total">Montant</td><td class="n total">Déductible</td></tr>{spent_rows}</table>
<div class="net"><span>Revenu net estimé (revenus − dépenses déductibles)</span><span>{money(report['revenu_net_estime'])}</span></div>
<h2>À compléter avant l'envoi au comptable</h2>
<ul>{todo_html}</ul>
<ul class="notes">{notes}</ul>
</body></html>
"""


def write_report(report, folder=None):
    folder = private_folder(exports()) if folder is None else folder
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    return write_private(folder, f"rapport-finances-{report['annee']}.html", render(report).encode('utf-8'))
