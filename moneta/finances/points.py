"""Points Membership Rewards de la Cobalt et objectif du voyage au Japon (spec 005, D081) : lecture seule, rien n'est gardé.

Module pur : une connexion en lecture et la date du jour en entrée, un dictionnaire en sortie, pour le chat comme pour le
planificateur plus tard (D077). Seuls des totaux de points et des dates en sortent : aucun montant en dollars, aucun aperçu
de compte, aucun nom de fichier, aucune opération (FR-011). Un mois manquant n'est jamais deviné.

La série suit le programme, pas la carte : tous les comptes Cobalt, triés par période. Seuls les relevés dont les points
concordent portent un chiffre; un relevé mis de côté (concorde = 0) ou au résumé illisible entre dans la chaîne de la 003
sans soldes : sa période est couverte, et aucune rupture n'est calculée à ses bords.
"""
from datetime import date, timedelta
from fractions import Fraction
import math

from finances.releves import chaine

HISTORY = 24
PACE_STATEMENTS = 6
PACE_MINIMUM = 3
NONE_KNOWN = 'Aucun relevé Cobalt importé : aucun solde de points connu.'


class RefusPoints(ValueError):
    pass


def statements(con):
    """[{du, au, statut, precedent?, gagnes?, utilises?, nouveau?}] of every Cobalt statement, sorted by period."""
    out = [{'du': r['du'], 'au': r['au'], 'statut': 'concorde' if r['concorde'] else 'mis_de_cote',
            **({k: r[k] for k in ('precedent', 'gagnes', 'utilises', 'nouveau')} if r['concorde'] else {})}
           for r in con.execute("SELECT p.du, p.au, p.precedent, p.gagnes, p.utilises, p.nouveau, p.concorde FROM points_releves p "
                                "JOIN imports i ON i.id=p.import_id WHERE i.source='cobalt'")]
    # A Cobalt statement whose points summary could not be read: its dates only, never its balances in dollars.
    out += [{'du': r['du'], 'au': r['au'], 'statut': 'illisible'}
            for r in con.execute("SELECT s.du, s.au FROM soldes_releves s JOIN imports i ON i.id=s.import_id WHERE i.source='cobalt' "
                                 "AND NOT EXISTS (SELECT 1 FROM points_releves p WHERE p.import_id=s.import_id)")]
    out.sort(key=lambda s: (s['du'], s['au']))
    for before, after in zip(out, out[1:]):
        if after['du'] <= before['au']:
            # Two cards at the same time would add up to a wrong balance: no figure rather than a false one.
            raise RefusPoints(f"Deux relevés Cobalt se chevauchent ({before['du']} au {before['au']} et {after['du']} au "
                              f"{after['au']}) : aucun calcul de points.")
    return out


def goals(con):
    """(goal in force or None, closed goals newest first); nothing before migration Finances 0006."""
    if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='objectifs_points'").fetchone() is None:
        return None, []
    rows = con.execute('SELECT cible, date_visee, cree_le, fin_le, fin FROM objectifs_points ORDER BY id DESC').fetchall()
    shown = [{'cible': r['cible'], **({'date_visee': r['date_visee']} if r['date_visee'] else {}), 'depuis': r['cree_le'][:10],
              **({'fin_le': r['fin_le'][:10], 'fin': r['fin']} if r['fin'] else {})} for r in rows]
    return next((g for g in shown if 'fin' not in g), None), [g for g in shown if 'fin' in g]


def month(day):
    """A month as one integer: (year × 12 + month) − 1."""
    parsed = date.fromisoformat(day)
    return parsed.year * 12 + parsed.month - 1


def month_text(index):
    return f'{index // 12:04d}-{index % 12 + 1:02d}'


def pace(known):
    """Average points earned (gagnes only) over the last 6 concordant statements, 3 at least: (Fraction or None, shown)."""
    used = known[-PACE_STATEMENTS:]
    if len(used) < PACE_MINIMUM:
        return None, {'estimable': False,
                      'raison': f'{len(used)} relevé(s) concordant(s); il en faut au moins {PACE_MINIMUM} pour estimer un rythme.'}
    rate = Fraction(sum(s['gagnes'] for s in used), len(used))
    shown = {'points_par_mois': math.floor(rate + Fraction(1, 2)), 'periodes': [{'du': s['du'], 'au': s['au']} for s in used]}
    if rate <= 0:
        shown['raison'] = 'Rythme nul ou négatif : aucune date estimée.'
    return rate, shown


def progress(goal, balance, known, rate):
    """Gap, estimated month and target date of the goal; only from the last concordant statement and the pace."""
    gap = goal['cible'] - balance['points']
    out = {'ecart': gap, 'cible_atteinte': gap <= 0}
    last = month(balance['au'])
    estimated = None
    if gap > 0 and rate is not None and rate > 0:
        estimated = last + math.ceil(gap / rate)
        out['date_estimee'] = month_text(estimated)
    if goal.get('date_visee'):
        left = month(goal['date_visee']) - last
        if gap <= 0:
            out['date_visee'] = {'atteignable': True, 'mois_restants': max(left, 0)}
        elif left <= 0:
            out['date_visee'] = {'mois_restants': 0,
                                 'raison': 'Date visée trop proche : aucun mois entier après le dernier relevé.'}
        else:
            target = {'mois_restants': left, 'points_par_mois_necessaires': math.ceil(Fraction(gap, left))}
            if estimated is None:
                target['raison'] = 'Rythme pas estimable, nul ou négatif : on ne peut pas dire si la date est atteignable.'
            else:
                # The estimated month is reached when its statement ends: on the target date only if that is the month's last day.
                last_day = (date.fromisoformat(goal['date_visee']) + timedelta(days=1)).day == 1
                target['atteignable'] = estimated < month(goal['date_visee']) or (estimated == month(goal['date_visee']) and last_day)
            out['date_visee'] = target
    return out


def state(con, today=None, historique=False):
    """Read tool points_japon (and the planner later): balance, goal, progress and what is missing."""
    today = today or date.today()
    goal, past = goals(con)
    out = {**({'objectif': goal} if goal else {})}
    try:
        found = statements(con)
    except RefusPoints as exc:
        # No figure rather than a false one; the goal stays readable (and changeable) all the same.
        return {**out, 'message': str(exc)}
    if not found:
        out['message'] = NONE_KNOWN
        if historique:
            out.update({'historique': [], 'objectifs_passes': past})
        return out
    rows = [(s['du'], s['au'], s.get('precedent'), s.get('nouveau')) for s in found]
    linked = chaine.chain(rows, today, debut=found[0]['du'])
    known = [s for s in found if s['statut'] == 'concorde']
    out['manques'] = {'trous': linked['trous'],
                      'illisibles': [{'du': s['du'], 'au': s['au']} for s in found if s['statut'] == 'illisible'],
                      'mis_de_cote': [{'du': s['du'], 'au': s['au']} for s in found if s['statut'] == 'mis_de_cote'],
                      'ruptures': linked['ruptures']}
    if 'en_retard_depuis' in linked:
        # Nothing covers the last weeks: the balance is old, and the next statement is missing.
        out['manques']['en_retard_depuis'] = linked['en_retard_depuis']
    if found[-1]['statut'] != 'concorde':
        out['dernier_mis_de_cote'] = {'du': found[-1]['du'], 'au': found[-1]['au'], 'statut': found[-1]['statut']}
    if known:
        balance = {'points': known[-1]['nouveau'], 'au': known[-1]['au']}
        out['solde'] = balance
        rate, out['rythme'] = pace(known)
        if goal:
            out.update(progress(goal, balance, known, rate))
    else:
        out['message'] = 'Aucun relevé Cobalt dont les points concordent : aucun solde de points connu.'
    if historique:
        out['historique'] = [({k: s[k] for k in ('du', 'au', 'precedent', 'gagnes', 'utilises', 'nouveau')} if s['statut'] == 'concorde'
                              else {'du': s['du'], 'au': s['au'], 'statut': s['statut']}) for s in reversed(found)][:HISTORY]
        out['objectifs_passes'] = past
    return out
