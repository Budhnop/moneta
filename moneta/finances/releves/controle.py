"""Contrôle commun des relevés PDF (spec 003) : un seul profil reconnaît le fichier, ce qu'il lit est vérifié, et les soldes
de chaque compte doivent concorder au cent près; sinon tout le relevé est refusé, sans rien deviner. La section de la carte
additionnelle d'une Cobalt (spec 004) est lue avec le compte de la carte : un seul solde, celui du relevé entier.

Les montants sont du point de vue d'Ugo : une sortie est négative, une dette (carte, marge) aussi. Les messages de refus
atteignent le modèle : ils nomment la vérification et le compte masqué, jamais un montant ni une description (D057).
"""
from datetime import date
import re

from moneta.finances.banque import MAX_ACCOUNTS, MAX_CENTS, MAX_LINES, RefusBanque, clean, masked, sha
from moneta.finances.releves.profils import NOMS, SOURCES, registre

TYPES = ('banque', 'carte', 'marge')
# Genre of a line, with the sign it must have: + entrée, - sortie (research R11).
GENRES = {
    'banque': {'operation': None},
    'carte': {'achat': -1, 'frais': -1, 'interets': -1, 'credit': 1, 'paiement': 1},
    'marge': {'avance': -1, 'frais': -1, 'interets': -1, 'paiement': 1},
}
MAX_BALANCE = 100 * MAX_CENTS
MAX_POINTS = 100_000_000
MAX_ADDITIONAL = 2  # the additional card, and its replacement the month it changes (spec 004)


def day(value):
    try:
        if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            raise ValueError
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise RefusBanque('Date illisible dans le relevé.') from None


def whole(value, limit, what):
    if type(value) is not int or abs(value) > limit:
        raise RefusBanque(f'{what} invalide dans le relevé.')
    return value


def profile_of(pages, registry=None):
    registry = registre() if registry is None else registry
    found = [p for p in registry if p.reconnait(pages)]
    if len(found) > 1:
        raise RefusBanque("Relevé reconnu par plus d'un profil : refusé, rien n'est deviné.")
    if not found:
        known = ', '.join(NOMS[p.SOURCE] for p in registry)
        raise RefusBanque('Relevé non reconnu : seuls les relevés dont la mise en page est définie sont lus (' + known + ').'
                          if known else "Relevé non reconnu : aucune mise en page de relevé n'est encore définie; elle le "
                          "sera au premier vrai relevé de chaque source.")
    return found[0]


def card_number(value):
    return re.sub(r'[^A-Za-z0-9]', '', str(value or '')).upper()


def sections(raw, source, kind, preview):
    """[(number, preview, lines)] of the additional cards printed in this account (spec 004): a Cobalt card only, two at
    most, each with its own preview. The name printed beside the number is never read (FR-005)."""
    found = raw.get('additionnelles') or []
    if not isinstance(found, list):
        raise RefusBanque('Section additionnelle invalide dans le relevé.')
    if not found:
        return []
    if source != 'cobalt' or kind != 'carte':
        raise RefusBanque(f'Section additionnelle inattendue (compte {preview}) : seule la carte Cobalt en a une. Relevé refusé.')
    if len(found) > MAX_ADDITIONAL:
        raise RefusBanque(f'Plus de deux sections additionnelles (compte {preview}) : relevé refusé.')
    out = []
    for item in found:
        if not isinstance(item, dict):
            raise RefusBanque('Section additionnelle invalide dans le relevé.')
        number = card_number(item.get('numero'))
        if not 4 <= len(number) <= 34:
            raise RefusBanque(f'Section additionnelle sans numéro de carte lisible (compte {preview}) : relevé refusé.')
        out.append((number, masked(number), item.get('lignes') or []))
    previews = [preview] + [p for _, p, _ in out]
    if len(set(previews)) != len(previews):
        raise RefusBanque(f'Deux cartes du relevé ont le même aperçu (compte {preview}); impossible de les distinguer.')
    return out


def account(raw, start, end, source):
    if not isinstance(raw, dict):
        raise RefusBanque('Compte invalide dans le relevé.')
    number = card_number(raw.get('numero'))
    if not 4 <= len(number) <= 34:
        raise RefusBanque('Relevé sans numéro de compte lisible.')
    kind = raw.get('type')
    if kind not in TYPES:
        raise RefusBanque('Type de compte invalide dans le relevé.')
    preview = masked(number)
    opening = whole(raw.get('solde_debut_cents'), MAX_BALANCE, 'Solde')
    closing = whole(raw.get('solde_fin_cents'), MAX_BALANCE, 'Solde')
    additional = sections(raw, source, kind, preview)
    # One list for every holder of the card, in the order of the statement: the same checks, the same ranks, and one
    # balance check over the whole statement (FR-002). A line of a section keeps the preview of its card ('carte').
    everything = [(line, None) for line in raw.get('lignes') or []] + \
        [(line, card) for _, card, found in additional for line in found]
    lines, ranks = [], {}
    for line, card in everything:
        if not isinstance(line, dict):
            raise RefusBanque('Opération invalide dans le relevé.')
        when = day(line.get('date'))
        if not start <= when <= end:
            raise RefusBanque(f'Opération hors de la période du relevé (compte {preview}).')
        amount = whole(line.get('montant_cents'), MAX_CENTS, 'Montant')
        if amount == 0:
            raise RefusBanque(f'Opération sans montant (compte {preview}).')
        genre = line.get('genre')
        if genre not in GENRES[kind]:
            raise RefusBanque(f'Genre d\'opération invalide pour ce compte ({preview}).')
        sign = GENRES[kind][genre]
        if sign is not None and (amount > 0) != (sign > 0):
            raise RefusBanque(f'Sens d\'une opération contraire à son genre (compte {preview}).')
        # Key without the description: the same operation reads differently in a PDF and in an OFX (research R5).
        base = f'{when}|{amount}'
        ranks[base] = ranks.get(base, 0) + 1
        lines.append({'cle': sha(f'pdf|{number}|{base}|{ranks[base]}'), 'date': when, 'montant_cents': amount,
                      'description': clean(str(line.get('description') or '')) or '(sans description)', 'genre': genre,
                      'rang': ranks[base], **({'carte': card} if card else {})})
    # The one check that proves every line was read, page breaks included (FR-003).
    if opening + sum(line['montant_cents'] for line in lines) != closing:
        raise RefusBanque(f'Soldes qui ne concordent pas pour le compte {preview} : solde de départ et opérations ne '
                          'donnent pas le solde de fin. Relevé entier refusé, rien n\'est importé.')
    return {'empreinte': sha('compte|' + number), 'apercu': preview, 'type': kind, 'solde_debut_cents': opening,
            'solde_fin_cents': closing, 'lignes': lines,
            'additionnelles': [{'empreinte': sha('compte|' + n), 'apercu': card} for n, card, _ in additional]}


def points(raw, accounts):
    if raw is None:
        return None
    if not isinstance(raw, dict) or len(accounts) != 1 or accounts[0]['type'] != 'carte':
        raise RefusBanque('Résumé des points invalide dans le relevé.')
    values = {k: raw.get(k) for k in ('precedent', 'gagnes', 'utilises', 'nouveau')}
    if not all(type(v) is int and 0 <= v <= MAX_POINTS for v in values.values()):
        # A points summary that cannot be read is set aside: the operations do not depend on it (FR-016).
        return {'concorde': False, 'illisible': True}
    values['concorde'] = values['precedent'] + values['gagnes'] - values['utilises'] == values['nouveau']
    return values


def verify(data, source):
    """The profile's reading, checked and normalised: {source, du, au, comptes: [...], points}."""
    if not isinstance(data, dict) or data.get('source') != source or source not in SOURCES:
        raise RefusBanque('Lecture du relevé invalide.')
    if 'titulaire_additionnel' in data:
        # The old contract of 003: a section must come as additionnelles, never be dropped in silence (spec 004).
        raise RefusBanque('Lecture du relevé invalide (ancien contrat de profil) : relevé refusé.')
    start, end = day(data.get('du')), day(data.get('au'))
    if start > end:
        raise RefusBanque('Période du relevé invalide.')
    raw_accounts = data.get('comptes')
    if not isinstance(raw_accounts, list) or not 1 <= len(raw_accounts) <= MAX_ACCOUNTS:
        raise RefusBanque('Nombre de comptes invalide dans le relevé.')
    accounts = [account(raw, start, end, source) for raw in raw_accounts]
    if sum(len(a['lignes']) for a in accounts) > MAX_LINES:
        raise RefusBanque(f'Plus de {MAX_LINES} opérations dans le relevé.')
    previews = [a['apercu'] for a in accounts]
    if len(set(previews)) != len(previews) or len({a['empreinte'] for a in accounts}) != len(accounts):
        raise RefusBanque('Deux comptes du relevé ont le même aperçu; impossible de les distinguer.')
    return {'source': source, 'du': start, 'au': end, 'comptes': accounts, 'points': points(data.get('points'), accounts)}


def read(pages, registry=None):
    profile = profile_of(pages, registry)
    try:
        data = profile.lire(pages)
    except RefusBanque:
        raise
    except Exception:
        # A profile that trips on an unexpected layout refuses; it never guesses.
        raise RefusBanque(f'Mise en page inattendue pour un relevé {NOMS[profile.SOURCE]} : relevé refusé.') from None
    return verify(data, profile.SOURCE)
