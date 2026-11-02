"""Relevés bancaires : lecture OFX ou PDF (spec 003), comptes reconnus par empreinte, règles de classement, opérations à classer.

Le fichier n'est lu que dans le dossier imports de Moneta (MONETA_HOME en test), jamais copié dans la base; le numéro de compte n'est gardé
que sous forme d'empreinte et d'aperçu masqué. Aucun analyseur XML : un relevé OFX est lu par balises, sans entités externes.
Un relevé PDF est lu sur le Mac (finances/releves) : soldes contrôlés au cent près, soldes et points gardés par relevé.
"""
from datetime import date
from decimal import Decimal, InvalidOperation
import hashlib
import html
import os
import re
import stat
import unicodedata

from moneta.chemins import home

MAX_BYTES = 2_000_000
MAX_PDF_BYTES = 10_000_000
MAX_LINES = 5000
MAX_ACCOUNTS = 20
MAX_CENTS = 1_000_000_00
FILE_NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9 ._-]{0,99}$')
TAG = re.compile(r'<(/?)([A-Za-z0-9.]+)>([^<]*)')


class RefusBanque(ValueError):
    pass


def imports_dir():
    return home() / 'imports'


def sha(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def normal(value):
    """Rule matching ignores case, accents and repeated spaces."""
    value = unicodedata.normalize('NFKD', value)
    return ' '.join(''.join(c for c in value if not unicodedata.combining(c)).casefold().split())


def clean(value, limit=200):
    value = ''.join(c if ord(c) >= 32 else ' ' for c in html.unescape(value or ''))
    return ' '.join(value.split())[:limit]


def read_file(name):
    """Bytes of imports/<name>: plain name, regular file (no link), OFX or QFX (2 Mo at most) or PDF (10 Mo at most)."""
    if not isinstance(name, str) or not FILE_NAME.match(name) or '..' in name:
        raise RefusBanque('Nom de fichier invalide : seulement un nom de fichier du dossier imports, sans chemin.')
    if name.lower().endswith('.csv'):
        raise RefusBanque("CSV pas pris en charge : dépose le relevé PDF (ou OFX) dans le dossier imports du Mac.")
    if not name.lower().endswith(('.ofx', '.qfx', '.pdf')):
        raise RefusBanque('Format accepté : .pdf, .ofx ou .qfx.')
    limit = MAX_PDF_BYTES if name.lower().endswith('.pdf') else MAX_BYTES
    folder = imports_dir()
    try:
        fd = os.open(folder / name, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        raise RefusBanque(f'Fichier introuvable dans {folder}.') from None
    except OSError:
        raise RefusBanque('Fichier illisible (lien symbolique ou droits).') from None
    with os.fdopen(fd, 'rb') as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise RefusBanque("Ce n'est pas un fichier ordinaire.")
        if info.st_size > limit:
            raise RefusBanque(f'Fichier trop gros ({limit // 1_000_000} Mo au plus).')
        raw = handle.read(limit + 1)
        if len(raw) > limit:
            raise RefusBanque(f'Fichier trop gros ({limit // 1_000_000} Mo au plus).')
        return raw


def decode(raw):
    try:
        return raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        return raw.decode('cp1252', errors='replace')


def masked(acctid):
    """'…4567 EOP' : the last four digits, plus a product suffix such as EOP when the bank adds one."""
    # A short product suffix (EOP, ES1, MC1): one to three letters and a digit at most; a longer one would show the number.
    match = re.match(r'^(.*?\d)([A-Z]{1,3}\d?)?$', acctid)
    if not match:
        return '…' + acctid[-4:]
    digits = re.sub(r'\D', '', match[1])
    return '…' + digits[-4:] + (f' {match[2]}' if match[2] else '')


def cents(value):
    try:
        amount = Decimal(value.strip().replace(',', '.').replace(' ', ''))
    except (InvalidOperation, AttributeError):
        raise RefusBanque('Montant illisible dans le relevé.') from None
    if not amount.is_finite() or amount != amount.quantize(Decimal('0.01')) or abs(amount) * 100 > MAX_CENTS:
        raise RefusBanque('Montant invalide dans le relevé.')
    return int(amount * 100)


def posted(value):
    try:
        return date(int(value[0:4]), int(value[4:6]), int(value[6:8])).isoformat()
    except (TypeError, ValueError):
        raise RefusBanque('Date illisible dans le relevé.') from None


def conflict(first, second):
    # No date or amount in the message: it reaches the model, and the account may be Perso (D057).
    return RefusBanque("Deux opérations portent le même identifiant bancaire (FITID) avec une date ou un montant différents : "
                       "relevé refusé, rien n'est importé. Réexporte-le depuis la banque.")


def parse_ofx(raw):
    """{'comptes': [{empreinte, apercu, type, lignes: [{cle, date, montant_cents, description}]}]} from OFX 1 (SGML) or 2 (XML)."""
    text = decode(raw)
    if '<OFX>' not in text.upper():
        raise RefusBanque("Ce fichier n'est pas un relevé OFX.")
    accounts, account, transaction, kind, inside_from, count = [], None, None, None, False, 0
    for closing, tag, value in TAG.findall(text):
        tag, value = tag.upper(), value.strip()
        if tag in ('STMTRS', 'CCSTMTRS'):
            if closing:
                account = None
            else:
                kind, account = ('banque' if tag == 'STMTRS' else 'carte'), {'acctid': None, 'lignes': []}
                accounts.append(account)
                if len(accounts) > MAX_ACCOUNTS:
                    raise RefusBanque(f'Plus de {MAX_ACCOUNTS} comptes dans un même relevé.')
        elif tag in ('BANKACCTFROM', 'CCACCTFROM'):
            inside_from = not closing
        elif tag == 'STMTTRN':
            if closing or account is None:
                transaction = None
            else:
                transaction = {}
                account['lignes'].append(transaction)
                count += 1
                if count > MAX_LINES:
                    raise RefusBanque(f'Plus de {MAX_LINES} opérations : coupe le relevé en périodes plus courtes.')
        elif closing or not value:
            continue
        elif transaction is not None:
            transaction[tag] = value
        elif tag in ('DTSTART', 'DTEND') and account is not None:
            account[tag] = posted(value)
        elif tag == 'ACCTID' and inside_from and account is not None:
            account['acctid'], account['type'] = re.sub(r'[^A-Za-z0-9]', '', value).upper(), kind
    out = {}
    for account in accounts:
        if not account['acctid']:
            raise RefusBanque('Relevé sans numéro de compte.')
        # The same account twice in one file (two periods) is one account; a repeated line is kept once.
        merged = out.setdefault(account['acctid'], {'empreinte': sha('compte|' + account['acctid']), 'apercu': masked(account['acctid']),
                                                    'type': account['type'], 'lignes': [], 'du': None, 'au': None})
        # The period the bank declares (DTSTART, DTEND): the chain of statements counts it as covered (FR-017).
        if account.get('DTSTART'):
            merged['du'] = min(filter(None, (merged['du'], account['DTSTART'])))
        if account.get('DTEND'):
            merged['au'] = max(filter(None, (merged['au'], account['DTEND'])))
        # A FITID seen twice is the same operation only when its date and amount agree; otherwise the file is refused,
        # never one of the two dropped in silence. Its description may differ (banks enrich it once posted): the first stays.
        keys = {line['cle']: line for line in merged['lignes']}
        seen, lines = {}, merged['lignes']
        for t in account['lignes']:
            if 'DTPOSTED' not in t or 'TRNAMT' not in t:
                raise RefusBanque('Opération incomplète dans le relevé (date ou montant).')
            amount = cents(t['TRNAMT'])
            if amount == 0:
                continue
            name, memo = clean(t.get('NAME')), clean(t.get('MEMO'))
            description = clean(name + (' ' + memo if memo and memo != name else '')) or '(sans description)'
            day = posted(t['DTPOSTED'])
            if t.get('FITID'):
                key = sha('fitid|' + t['FITID'])
            else:
                base = f'{day}|{amount}|{description}'
                seen[base] = seen.get(base, 0) + 1
                key = sha(f'{base}|{seen[base]}')
            line = {'cle': key, 'date': day, 'montant_cents': amount, 'description': description}
            if key in keys:
                if (keys[key]['date'], keys[key]['montant_cents']) != (day, amount):
                    raise conflict(keys[key], line)
                continue
            keys[key] = line
            lines.append(line)
    if not out:
        raise RefusBanque('Aucun compte dans ce relevé.')
    previews = [a['apercu'] for a in out.values()]
    if len(set(previews)) != len(previews):
        raise RefusBanque('Deux comptes du relevé ont le même aperçu; impossible de les distinguer.')
    return {'comptes': list(out.values())}


def load(name):
    """(footprint of the bytes read, statement): the same shape for OFX and PDF, plus format, source, du, au and points.
    A PDF account also has its opening and closing balances, already checked; an OFX one has none."""
    raw = read_file(name)
    footprint = hashlib.sha256(raw).hexdigest()
    if name.lower().endswith('.pdf'):
        from moneta.finances.releves import controle, extraction
        return footprint, {**controle.read(extraction.pages(raw)), 'format': 'pdf'}
    parsed = parse_ofx(raw)
    dates = [line['date'] for account in parsed['comptes'] for line in account['lignes']]
    dates += [d for account in parsed['comptes'] for d in (account['du'], account['au']) if d]
    return footprint, {**parsed, 'format': 'ofx', 'source': None, 'du': min(dates, default=None), 'au': max(dates, default=None),
                       'points': None}


# Classification -------------------------------------------------------------------------------------------------------

def targets(con):
    """code -> ('t2125', row) or ('perso', row), active only."""
    out = {r['code']: ('t2125', r) for r in con.execute('SELECT id, code, nom, pct_deductible FROM categories WHERE actif=1')}
    out.update({r['code']: ('perso', r) for r in con.execute('SELECT id, code, nom FROM categories_perso WHERE actif=1')})
    return out


def check_target(kinds, code, amount, account_name, pct=None):
    """(kind, row, pct) when code may classify this line, else a reason (str)."""
    if code not in kinds:
        return 'Catégorie inconnue; voir finances_reference (codes T2125 et catégories perso).'
    kind, row = kinds[code]
    if kind == 't2125':
        if amount > 0:
            return "Une entrée d'argent ne peut pas être une dépense du dojo."
        pct = row['pct_deductible'] if pct is None else pct
        if pct is None:
            return f"Pourcentage déductible requis pour {row['code']} {row['nom']} (usage d'affaires)."
        return kind, row, pct
    if pct is not None:
        return 'Le pourcentage ne sert qu\'aux dépenses du dojo (codes T2125).'
    if code == 'depot_dojo' and (amount < 0 or account_name != 'Dojo'):
        return 'depot_dojo : seulement une entrée du compte Dojo.'
    return kind, row, None


def active_rules(con):
    rows = con.execute('SELECT id, motif, cible, pct FROM regles WHERE actif=1').fetchall()
    return sorted(rows, key=lambda r: (-len(r['motif']), r['id']))


def rule_for(rules, kinds, description, amount, account_name):
    text = normal(description)
    for rule in rules:
        if rule['motif'] in text and isinstance(check_target(kinds, rule['cible'], amount, account_name, rule['pct']), tuple):
            return rule
    return None


def apply(con, audit, operation, code, pct=None, rule_id=None):
    """Classify one stored operation (sqlite Row with compte, type). A dojo expense becomes a T2125 expense."""
    kinds = targets(con)
    checked = check_target(kinds, code, operation['montant_cents'], operation['compte'], pct)
    if isinstance(checked, str):
        raise RefusBanque(checked)
    kind, row, pct = checked
    if operation['depense_id'] is not None:
        con.execute('UPDATE depenses SET annulee=1 WHERE id=?', (operation['depense_id'],))
        audit(con, 'annuler_depense', operation['depense_id'], f"reclassement de l'opération #{operation['id']}")
    if kind == 't2125':
        # operation_id never changes: a later reclassification keeps the supplier judged as this bank line (F2, D091).
        number = con.execute('''INSERT INTO depenses(compte_id, date, montant_cents, categorie_id, pct_deductible, fournisseur, description, mode,
            operation_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''', (operation['compte_id'], operation['date'], -operation['montant_cents'], row['id'],
                                                 pct, operation['description'][:200], f"Relevé bancaire, opération #{operation['id']}",
                                                 'carte' if operation['type'] == 'carte' else None, operation['id'])).lastrowid
        audit(con, 'ajouter_depense', number, f"{operation['date']} - {-operation['montant_cents'] / 100:.2f}$ - {row['code']} - {pct}%")
        con.execute("UPDATE operations SET classe='depense_dojo', depense_id=?, categorie_perso_id=NULL, regle_id=? WHERE id=?",
                    (number, rule_id, operation['id']))
    else:
        con.execute("UPDATE operations SET classe='perso', categorie_perso_id=?, depense_id=NULL, regle_id=? WHERE id=?",
                    (row['id'], rule_id, operation['id']))
    return row['code']


# Never o.*: the holder of an operation (carte_additionnelle_id, spec 004) is kept for the check of the statement only, and no
# read, summary or filter ever gives it (FR-004).
TABLES_OPERATIONS = ''' FROM operations o JOIN comptes_bancaires b ON b.id=o.compte_bancaire_id JOIN comptes c ON c.id=b.compte_id
    LEFT JOIN categories_perso p ON p.id=o.categorie_perso_id LEFT JOIN depenses d ON d.id=o.depense_id
    LEFT JOIN categories k ON k.id=d.categorie_id'''
OPERATION = '''SELECT o.id, o.compte_bancaire_id, o.import_id, o.cle, o.date, o.montant_cents, o.description, o.classe,
    o.categorie_perso_id, o.depense_id, o.regle_id, o.cree_le,
    b.apercu, b.type, b.compte_id, c.nom AS compte, p.code AS perso, k.code AS t2125, d.annulee''' + TABLES_OPERATIONS


def category_of(row):
    return row['perso'] or row['t2125']


# Import -----------------------------------------------------------------------------------------------------------------

IGNORE = 'ignore'
# Answers for the section of an additional card of the Cobalt, never for an account (spec 004, research R2).
ADDITIONAL = ('additionnel', 'remplace')
# Classified by Python from the genre the statement prints, never by a rule to accept (FR-009, research R11).
OFFICE = {'paiement': 'virement_interne', 'avance': 'virement_interne', 'frais': 'frais_bancaires', 'interets': 'frais_bancaires'}
MATCH_DAYS = 5


def office_code(account, line, name):
    """Transfers always; fees and interest only on a Perso card or margin: on the Dojo, they may be a T2125 expense."""
    code = OFFICE.get(line.get('genre')) if account['type'] in ('carte', 'marge') else None
    return code if code == 'virement_interne' or name == 'Perso' else None


def reconcile(con, accounts):
    """The bank lines that are the other side of a card or margin transfer (payment, advance): opposite amount, five days
    apart at most, each transfer matched once, closest first, and at least one side new in this import (an old payment
    never waits for any outflow to come). Marks the new bank lines (line['office']) and returns (ids of stored bank
    operations to classify, [(date of the bank line, its account, the card or margin)] for the recap)."""
    transfers, sides = [], []
    for row in con.execute("SELECT o.date, o.montant_cents, b.apercu FROM operations o JOIN comptes_bancaires b "
                           "ON b.id=o.compte_bancaire_id JOIN categories_perso p ON p.id=o.categorie_perso_id "
                           "WHERE b.type IN ('carte', 'marge') AND p.code='virement_interne'"):
        transfers.append((row['date'], row['montant_cents'], row['apercu'], False))
    for row in con.execute("SELECT o.id, o.date, o.montant_cents, p.code, b.apercu FROM operations o JOIN comptes_bancaires b "
                           "ON b.id=o.compte_bancaire_id LEFT JOIN categories_perso p ON p.id=o.categorie_perso_id "
                           "WHERE b.type='banque' AND (o.classe IS NULL OR p.code='virement_interne')"):
        sides.append({'date': row['date'], 'montant_cents': row['montant_cents'], 'id': row['id'], 'deja': row['code'] is not None,
                      'apercu': row['apercu'], 'neuf': False})
    for account in accounts:
        if account['compte'] in (None, IGNORE):
            continue
        for line in account['nouvelles']:
            if line.get('office') == 'virement_interne':
                transfers.append((line['date'], line['montant_cents'], account['apercu'], True))
            elif account['type'] == 'banque' and not line.get('office'):
                sides.append({'date': line['date'], 'montant_cents': line['montant_cents'], 'line': line, 'deja': False,
                              'apercu': account['apercu'], 'neuf': True})
    used, stored, shown = set(), [], []
    for when, amount, card, fresh in sorted(transfers):
        day = date.fromisoformat(when)
        candidates = [(not side['deja'], abs((date.fromisoformat(side['date']) - day).days), n) for n, side in enumerate(sides)
                      if n not in used and side['montant_cents'] == -amount and (fresh or side['neuf'] or side['deja'])
                      and abs((date.fromisoformat(side['date']) - day).days) <= MATCH_DAYS]
        if not candidates:
            continue
        number = min(candidates)[2]
        side = sides[number]
        used.add(number)
        if side['deja']:
            continue
        shown.append((side['date'], side['apercu'], card))
        if 'line' in side:
            side['line']['office'] = 'virement_interne'
        else:
            stored.append(side['id'])
    return stored, sorted(shown)


def overlapping(con, account_id, start, end):
    """The statement period of this account already imported that overlaps start..end, other than exactly the same one."""
    return con.execute('SELECT du, au FROM soldes_releves WHERE compte_bancaire_id=? AND du<=? AND au>=? AND NOT (du=? AND au=?)',
                       (account_id, end, start, start, end)).fetchone()


def cards(con, account, known, name, links, missing):
    """The additional cards of the sections of a Cobalt card (spec 004, research R2), or a refusal: [{empreinte, apercu, id,
    reponse, remplace?}]. A card already attached passes, even replaced (a late line); the first one is asked like an unknown
    account (its preview goes to missing); a new one while a card is in force is a third card, unless Ugo says remplace."""
    found = account.get('additionnelles') or []
    if not found:
        return []
    if name in ('Dojo', IGNORE):
        raise RefusBanque(f"Une carte additionnelle va seulement avec une carte du compte Perso : {account['apercu']} ne peut être "
                          f"{'liée à Dojo' if name == 'Dojo' else 'ignorée'} avec sa section additionnelle. Relevé refusé.")
    in_force = con.execute('SELECT id, apercu FROM cartes_additionnelles WHERE compte_bancaire_id=? AND remplacee_le IS NULL',
                           (known['id'],)).fetchone() if known else None
    rows = {card['apercu']: con.execute('SELECT id, compte_bancaire_id FROM cartes_additionnelles WHERE empreinte=?',
                                        (card['empreinte'],)).fetchone() for card in found}
    if sum(1 for row in rows.values() if row is None) > 1:
        raise RefusBanque("Le relevé a plus d'un aperçu nouveau de carte additionnelle : relevé refusé, une carte à la fois.")
    out = []
    for card in found:
        answer, row = links.get(card['apercu']), rows[card['apercu']]
        if answer is not None and answer not in ADDITIONAL:
            raise RefusBanque(f"Section de carte additionnelle {card['apercu']} : seulement additionnel ou remplace, jamais Dojo, "
                              "Perso ni ignore (ses dépenses comptent dans le solde du relevé).")
        if row:
            if not known or row['compte_bancaire_id'] != known['id']:
                raise RefusBanque(f"La carte additionnelle {card['apercu']} est rattachée à une autre carte : relevé refusé.")
            if answer is not None:
                raise RefusBanque(f"La carte additionnelle {card['apercu']} est déjà rattachée : reprends sans réponse pour elle.")
            out.append({**card, 'id': row['id'], 'reponse': None})
            continue
        if con.execute('SELECT 1 FROM cartes_additionnelles WHERE apercu=?', (card['apercu'],)).fetchone() or \
                con.execute('SELECT 1 FROM comptes_bancaires WHERE apercu=? OR empreinte=?', (card['apercu'], card['empreinte'])).fetchone():
            raise RefusBanque(f"Un compte bancaire ou une autre carte porte déjà l'aperçu {card['apercu']} : relevé refusé.")
        if in_force and answer is None:
            raise RefusBanque(f"Section d'une troisième carte ({card['apercu']}) alors que la carte additionnelle {in_force['apercu']} "
                              "est rattachée : relevé refusé, rien n'est gardé. Seulement si l'utilisateur dit que c'est la nouvelle "
                              f"carte de sa conjointe (elle remplace {in_force['apercu']}) : reprends avec comptes = "
                              f'{{"{card["apercu"]}": "remplace"}}.')
        if in_force and answer == 'additionnel':
            raise RefusBanque(f"Une carte additionnelle est déjà rattachée ({in_force['apercu']}) : répondre remplace si "
                              f"{card['apercu']} est sa nouvelle carte.")
        if not in_force and answer == 'remplace':
            raise RefusBanque(f"Aucune carte additionnelle à remplacer pour {account['apercu']} : répondre additionnel pour "
                              f"{card['apercu']}.")
        if answer is None:
            missing.append(card['apercu'])
        out.append({**card, 'id': None, 'reponse': answer,
                    **({'remplace': {'id': in_force['id'], 'apercu': in_force['apercu']}} if answer == 'remplace' else {})})
    return out


def plan(con, parsed, links):
    """What an import would do, without writing: accounts (known, linked now, ignored or missing), the new lines with their
    rule, and for a PDF the balances and points that are new. The previews still to link come accounts first, then the
    additional cards (in each account's 'cartes')."""
    kinds, rules = targets(con), active_rules(con)
    accounts, missing, missing_cards = [], [], []
    sections = {c['apercu'] for a in parsed['comptes'] for c in a.get('additionnelles') or []}
    extra = set(links) - {a['apercu'] for a in parsed['comptes']} - sections
    if extra:
        raise RefusBanque('Aperçu absent du relevé : ' + ', '.join(sorted(extra)) + '.')
    for account in parsed['comptes']:
        known = con.execute('SELECT b.id, b.type, c.nom FROM comptes_bancaires b JOIN comptes c ON c.id=b.compte_id WHERE b.empreinte=?',
                            (account['empreinte'],)).fetchone()
        ignored = con.execute('SELECT 1 FROM comptes_ignores WHERE empreinte=?', (account['empreinte'],)).fetchone() is not None
        link = links.get(account['apercu'])
        if link in ADDITIONAL:
            raise RefusBanque(f"{link} : seulement pour une carte additionnelle, pas pour le compte {account['apercu']} "
                              "(Dojo, Perso ou ignore).")
        if known and link not in (None, known['nom']):
            raise RefusBanque(f"Le compte {account['apercu']} est déjà lié à {known['nom']}.")
        name = known['nom'] if known else link if link else IGNORE if ignored else None
        pdf = parsed['format'] == 'pdf'
        state = {'compte': name, 'connu': bool(known), 'id': known['id'] if known else None, 'ignore_avant': ignored,
                 'nouvelles': [], 'doublons': 0, 'solde': None, 'du': parsed['du'] if pdf else account.get('du'),
                 'au': parsed['au'] if pdf else account.get('au')}
        if known and known['type'] != account['type']:
            raise RefusBanque(f"Le compte {account['apercu']} est connu comme {known['type']}, le relevé le dit {account['type']}.")
        state['cartes'] = cards(con, account, known, name, links, missing_cards)
        if name is None:
            missing.append(account['apercu'])
        elif name == IGNORE:
            # Its balances were checked with the others; nothing of it is kept, and it is never asked again (FR-005).
            accounts.append({**account, **state})
            continue
        elif not known and con.execute('SELECT 1 FROM comptes WHERE nom=? AND actif=1', (name,)).fetchone() is None:
            raise RefusBanque(f'Compte inconnu : {name}.')
        elif not known and con.execute('SELECT 1 FROM comptes_bancaires WHERE apercu=?', (account['apercu'],)).fetchone():
            raise RefusBanque(f"Un autre compte bancaire porte déjà l'aperçu {account['apercu']}.")
        elif not known and con.execute('SELECT 1 FROM cartes_additionnelles WHERE apercu=? OR empreinte=?',
                                       (account['apercu'], account['empreinte'])).fetchone():
            raise RefusBanque(f"L'aperçu {account['apercu']} est déjà celui d'une carte additionnelle : relevé refusé.")
        existing = {r['cle']: r for r in con.execute('SELECT cle, date, montant_cents FROM operations WHERE compte_bancaire_id=?',
                                                     (known['id'],))} if known else {}
        if parsed['format'] == 'pdf':
            # No bank identifier in a PDF: the n-th operation of a day and amount is new only if the account holds fewer
            # than n of them already, whether they came from a PDF or an OFX (research R5).
            held = {}
            for row in existing.values():
                held[(row['date'], row['montant_cents'])] = held.get((row['date'], row['montant_cents']), 0) + 1
            new = [dict(line) for line in account['lignes'] if line['rang'] > held.get((line['date'], line['montant_cents']), 0)]
            if known:
                clash = overlapping(con, known['id'], parsed['du'], parsed['au'])
                if clash:
                    raise RefusBanque(f"La période de ce relevé chevauche un relevé déjà importé pour le compte {account['apercu']} "
                                      f"(du {clash['du']} au {clash['au']}) : refusé.")
            same = known and con.execute('SELECT solde_debut_cents, solde_fin_cents FROM soldes_releves WHERE compte_bancaire_id=? '
                                         'AND du=? AND au=?', (known['id'], parsed['du'], parsed['au'])).fetchone()
            if same and (same['solde_debut_cents'], same['solde_fin_cents']) != (account['solde_debut_cents'], account['solde_fin_cents']):
                raise RefusBanque(f"Ce relevé est déjà importé pour le compte {account['apercu']} avec d'autres soldes : refusé, "
                                  "rien n'est changé.")
            state['solde'] = 'deja' if same else 'nouveau'
        else:
            for line in account['lignes']:
                if line['cle'] in existing and (existing[line['cle']]['date'], existing[line['cle']]['montant_cents']) != \
                        (line['date'], line['montant_cents']):
                    raise conflict(existing[line['cle']], line)
            new = [dict(line) for line in account['lignes'] if line['cle'] not in existing]
            if known:
                # Operations a PDF statement already brought have no bank identifier: count them by day and amount, and
                # only the lines beyond them are new (FR-007, both ways).
                from_pdf = {}
                for row in con.execute("SELECT o.date, o.montant_cents FROM operations o JOIN imports i ON i.id=o.import_id "
                                       "WHERE o.compte_bancaire_id=? AND i.format='pdf'", (known['id'],)):
                    from_pdf[(row['date'], row['montant_cents'])] = from_pdf.get((row['date'], row['montant_cents']), 0) + 1
                kept = []
                for line in new:
                    key = (line['date'], line['montant_cents'])
                    if from_pdf.get(key):
                        from_pdf[key] -= 1
                    else:
                        kept.append(line)
                new = kept
        for line in new:
            line['office'] = office_code(account, line, name)
            line['regle'] = rule_for(rules, kinds, line['description'], line['montant_cents'], name) if name and not line['office'] else None
        dates = [line['date'] for line in account['lignes']]
        state.update(nouvelles=new, doublons=len(account['lignes']) - len(new),
                     du=state['du'] or min(dates, default=None), au=state['au'] or max(dates, default=None))
        accounts.append({**account, **state})
    stored, matched = reconcile(con, accounts)
    for account in accounts:
        for line in account['nouvelles']:
            if line.get('office'):
                line['regle'] = None
    return accounts, missing + missing_cards, (stored, matched)


def news(accounts, points):
    """Whether the import adds anything: an operation, a statement balance, or the points of a new statement."""
    return any(a['nouvelles'] or a['solde'] == 'nouveau' for a in accounts if a['compte'] != IGNORE) or \
        any(a['compte'] == IGNORE and not a['ignore_avant'] for a in accounts)


def attachment(account):
    """The recap's word on an additional card attached by this import, with its preview only (spec 004)."""
    for card in account.get('cartes', []):
        if card['reponse'] == 'additionnel':
            return {'rattachement': f"carte additionnelle {card['apercu']} : titulaire additionnel"}
        if card['reponse'] == 'remplace':
            return {'rattachement': f"carte additionnelle {card['apercu']} : remplace {card['remplace']['apercu']}"}
    return {}


def summary(accounts, missing, footprint, already, parsed, reconciled):
    stored, matched = reconciled
    by_rule, by_office, to_sort = {}, {}, 0
    entries = exits = 0
    for account in accounts:
        for line in account['nouvelles']:
            if line.get('office'):
                by_office[line['office']] = by_office.get(line['office'], 0) + 1
            elif line['regle']:
                by_rule[line['regle']['cible']] = by_rule.get(line['regle']['cible'], 0) + 1
            else:
                to_sort += 1
            if line['montant_cents'] > 0:
                entries += line['montant_cents']
            else:
                exits -= line['montant_cents']
    out = {'empreinte': footprint, 'deja_importe': already, 'rien_de_nouveau': already or not news(accounts, parsed['points']),
           'comptes': [{'apercu': a['apercu'], 'type': a['type'], 'compte': a['compte'],
                        'lien': 'ignoré' if a['compte'] == IGNORE else 'existant' if a['connu'] else 'nouveau',
                        **({} if a['compte'] == IGNORE else {'lignes': len(a['lignes']), 'nouvelles': len(a['nouvelles']),
                                                             'doublons': a['doublons'], 'du': a['du'], 'au': a['au']}),
                        **({'solde_controle': 'ok'} if 'solde_debut_cents' in a else {}),
                        # A section was read; the totals stay those of the card, both holders together (FR-004).
                        **({'section_additionnelle': 'lue'} if a.get('additionnelles') else {}),
                        **attachment(a)} for a in accounts],
           'a_lier': missing, 'nouvelles': sum(len(a['nouvelles']) for a in accounts),
           'classees_par_regle': dict(sorted(by_rule.items())), 'a_classer': to_sort,
           'entrees': round(entries / 100, 2), 'sorties': round(exits / 100, 2)}
    waiting = [c['apercu'] for a in accounts for c in a.get('cartes', []) if c['id'] is None and c['reponse'] is None]
    if waiting:
        out['cartes_a_lier'] = waiting
    if by_office or stored:
        out['classees_d_office'] = {**dict(sorted(by_office.items())), **({'rapprochees_deja_importees': len(stored)} if stored else {})}
    if matched:
        # Shown in the Mac window before the click: which bank line becomes a transfer, by date and masked accounts only.
        out['rapprochements'] = [f'{day} {bank} → {card}' for day, bank, card in matched[:8]] + \
            ([f'et {len(matched) - 8} autres'] if len(matched) > 8 else [])
    if parsed['format'] == 'pdf':
        out = {'source': parsed['source'], 'du': parsed['du'], 'au': parsed['au'], **out}
    points = parsed['points']
    if points:
        out['points'] = {'illisible': True, 'avertissement': 'résumé des points illisible : mis de côté'} if points.get('illisible') \
            else {**{k: points[k] for k in ('precedent', 'gagnes', 'utilises', 'nouveau', 'concorde')},
                  **({} if points['concorde'] else {'avertissement': 'les points ne concordent pas : gardés de côté'})}
    return out


def links_of(data):
    links = data.get('comptes') or {}
    if not isinstance(links, dict) or not all(isinstance(k, str) and v in ('Dojo', 'Perso', IGNORE, *ADDITIONAL) for k, v in links.items()):
        raise RefusBanque('comptes : aperçu du relevé -> Dojo, Perso ou ignore (additionnel ou remplace pour une carte additionnelle).')
    return links


def analyse(con, data):
    footprint, parsed = load(data['fichier'])
    accounts, missing, reconciled = plan(con, parsed, links_of(data))
    already = con.execute('SELECT 1 FROM imports WHERE empreinte=?', (footprint,)).fetchone() is not None
    out = summary(accounts, missing, footprint, already, parsed, reconciled)
    # What would still be missing for these accounts once this statement is in (FR-017): dates only.
    from moneta.finances.releves import chaine
    gaps = {}
    for account in accounts:
        if account['compte'] in (None, IGNORE) or not account['du']:
            continue
        mine = (account['du'], account['au'], account.get('solde_debut_cents'), account.get('solde_fin_cents'))
        rows = chaine.periods(con, account['id'], () if account.get('solde') == 'deja' else (mine,))
        found = chaine.short(chaine.chain(rows, date.today()))
        if found:
            gaps[account['apercu']] = found
    if gaps:
        out['manquants'] = gaps
    return out


def import_file(con, data, audit):
    footprint, parsed = load(data['fichier'])
    if footprint != data['empreinte']:
        raise RefusBanque('Le fichier a changé depuis le récapitulatif; prépare l\'import de nouveau.')
    accounts, missing, (stored, _) = plan(con, parsed, links_of(data))
    if missing:
        raise RefusBanque('Comptes à lier : ' + ', '.join(missing) + '.')
    if con.execute('SELECT 1 FROM imports WHERE empreinte=?', (footprint,)).fetchone() or not news(accounts, parsed['points']):
        raise RefusBanque('Rien de nouveau dans ce relevé.')
    kept = [a for a in accounts if a['compte'] != IGNORE]
    new = sum(len(a['nouvelles']) for a in kept)
    batch = con.execute('INSERT INTO imports(fichier, empreinte, format, source, du, au, lignes, nouvelles) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                        (data['fichier'], footprint, parsed['format'], parsed['source'], parsed['du'], parsed['au'],
                         sum(len(a['lignes']) for a in kept), new)).lastrowid
    classified = office = 0
    for account in accounts:
        if account['compte'] == IGNORE:
            if not account['ignore_avant']:
                con.execute('INSERT INTO comptes_ignores(empreinte, apercu, type) VALUES (?, ?, ?)',
                            (account['empreinte'], account['apercu'], account['type']))
                audit(con, 'ignorer_compte_bancaire', None, account['apercu'], 'compte_bancaire')
            continue
        if account['id'] is None:
            if account['ignore_avant']:
                con.execute('DELETE FROM comptes_ignores WHERE empreinte=?', (account['empreinte'],))
            account['id'] = con.execute('INSERT INTO comptes_bancaires(compte_id, empreinte, apercu, type) '
                                        'SELECT id, ?, ?, ? FROM comptes WHERE nom=?',
                                        (account['empreinte'], account['apercu'], account['type'], account['compte'])).lastrowid
            audit(con, 'lier_compte_bancaire', account['id'], f"{account['apercu']} -> {account['compte']}", 'compte_bancaire')
        holders = {}
        for card in account['cartes']:
            if card['id'] is None:
                # In the same transaction as the import: the card, its replacement and the lines all go in, or nothing does.
                if card.get('remplace'):
                    con.execute("UPDATE cartes_additionnelles SET remplacee_le=datetime('now', 'localtime') WHERE id=? "
                                'AND remplacee_le IS NULL', (card['remplace']['id'],))
                card['id'] = con.execute('INSERT INTO cartes_additionnelles(compte_bancaire_id, empreinte, apercu) VALUES (?, ?, ?)',
                                         (account['id'], card['empreinte'], card['apercu'])).lastrowid
                if card.get('remplace'):
                    audit(con, 'remplacer_carte_additionnelle', card['id'], f"{card['remplace']['apercu']} -> {card['apercu']}",
                          'carte_additionnelle')
                else:
                    audit(con, 'rattacher_carte_additionnelle', card['id'], f"{card['apercu']} -> {account['apercu']}",
                          'carte_additionnelle')
            holders[card['apercu']] = card['id']
        if account['solde'] == 'nouveau':
            con.execute('INSERT INTO soldes_releves(import_id, compte_bancaire_id, du, au, solde_debut_cents, solde_fin_cents) '
                        'VALUES (?, ?, ?, ?, ?, ?)', (batch, account['id'], parsed['du'], parsed['au'],
                                                      account['solde_debut_cents'], account['solde_fin_cents']))
            points = parsed['points']
            if points and not points.get('illisible'):
                con.execute('INSERT INTO points_releves(import_id, compte_bancaire_id, du, au, precedent, gagnes, utilises, nouveau, '
                            'concorde) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                            (batch, account['id'], parsed['du'], parsed['au'], points['precedent'], points['gagnes'],
                             points['utilises'], points['nouveau'], int(points['concorde'])))
        for line in account['nouvelles']:
            number = con.execute('INSERT INTO operations(compte_bancaire_id, import_id, cle, date, montant_cents, description, '
                                 'carte_additionnelle_id) VALUES (?, ?, ?, ?, ?, ?, ?)',
                                 (account['id'], batch, line['cle'], line['date'], line['montant_cents'], line['description'],
                                  holders.get(line.get('carte')))).lastrowid
            if line.get('office'):
                apply(con, audit, con.execute(OPERATION + ' WHERE o.id=?', (number,)).fetchone(), line['office'])
                office += 1
            elif line['regle']:
                apply(con, audit, con.execute(OPERATION + ' WHERE o.id=?', (number,)).fetchone(), line['regle']['cible'],
                      line['regle']['pct'], line['regle']['id'])
                classified += 1
    for number in stored:
        apply(con, audit, con.execute(OPERATION + ' WHERE o.id=?', (number,)).fetchone(), 'virement_interne')
    audit(con, 'importer_releve', batch, f"{new} nouvelles, {classified} classées par règle, {office} d'office, "
          f"{len(stored)} rapprochées", 'import')
    return (f"OK : relevé importé ({new} opérations nouvelles, {classified} classées par règle, {office} classées d'office, "
            f"{new - classified - office} à classer" + (f"; {len(stored)} opérations déjà importées rapprochées" if stored else '') + ").")


# Classify and rules -----------------------------------------------------------------------------------------------------

def operation(con, number):
    if type(number) is not int or number < 1:
        raise RefusBanque("Numéro d'opération invalide.")
    row = con.execute(OPERATION + ' WHERE o.id=?', (number,)).fetchone()
    if row is None:
        raise RefusBanque(f'Opération #{number} introuvable.')
    return row


def classify(con, data, audit):
    row = operation(con, data['id'])
    pct = data.get('pct')
    if pct is not None and (type(pct) is not int or not 0 <= pct <= 100):
        raise RefusBanque('Pourcentage déductible invalide (entier de 0 à 100).')
    if category_of(row) == data['categorie'] and pct is None:
        raise RefusBanque(f"Opération #{row['id']} déjà classée {data['categorie']}.")
    code = apply(con, audit, row, data['categorie'], pct)
    audit(con, 'classer_operation', row['id'], f"{category_of(row) or 'à classer'} -> {code}", 'operation')
    return f"OK : opération #{row['id']} classée {code}."


def motif(value):
    if not isinstance(value, str):
        raise RefusBanque('Motif invalide.')
    value = normal(value)
    if not 3 <= len(value) <= 60:
        raise RefusBanque('Motif : de 3 à 60 caractères.')
    return value


def matching(con, kinds, pattern, code, pct):
    """Operations still to classify that this rule would classify."""
    rows = con.execute(OPERATION + ' WHERE o.classe IS NULL ORDER BY o.date, o.id').fetchall()
    return [r for r in rows if pattern in normal(r['description'])
            and isinstance(check_target(kinds, code, r['montant_cents'], r['compte'], pct), tuple)]


def check_rule(con, data):
    pattern, code, pct = motif(data['motif']), data['categorie'], data.get('pct')
    kinds = targets(con)
    if code not in kinds:
        raise RefusBanque('Catégorie inconnue; voir finances_reference (codes T2125 et catégories perso).')
    kind, row = kinds[code]
    if kind == 'perso' and pct is not None:
        raise RefusBanque("Le pourcentage ne sert qu'aux dépenses du dojo (codes T2125).")
    if kind == 't2125' and pct is None and row['pct_deductible'] is None:
        raise RefusBanque(f"Pourcentage déductible requis pour {row['code']} {row['nom']} (usage d'affaires).")
    if pct is not None and (type(pct) is not int or not 0 <= pct <= 100):
        raise RefusBanque('Pourcentage déductible invalide (entier de 0 à 100).')
    existing = con.execute('SELECT id, cible FROM regles WHERE motif=? AND actif=1', (pattern,)).fetchone()
    if existing:
        raise RefusBanque(f"Une règle « {pattern} » existe déjà (#{existing['id']}, {existing['cible']}).")
    return pattern, code, pct, matching(con, kinds, pattern, code, pct)


def add_rule(con, data, audit):
    pattern, code, pct, rows = check_rule(con, data)
    old = con.execute('SELECT id FROM regles WHERE motif=?', (pattern,)).fetchone()
    if old:
        con.execute('UPDATE regles SET cible=?, pct=?, actif=1 WHERE id=?', (code, pct, old['id']))
        number = old['id']
    else:
        number = con.execute('INSERT INTO regles(motif, cible, pct) VALUES (?, ?, ?)', (pattern, code, pct)).lastrowid
    for row in rows:
        apply(con, audit, row, code, pct, number)
    audit(con, 'ajouter_regle', number, f"« {pattern} » -> {code}; {len(rows)} opération(s) classée(s)", 'regle')
    return f"OK : règle #{number} « {pattern} » -> {code}; {len(rows)} opération(s) à classer classée(s)."


def remove_rule(con, data, audit):
    number = data['id']
    row = con.execute('SELECT * FROM regles WHERE id=? AND actif=1', (number,)).fetchone() if type(number) is int else None
    if row is None:
        raise RefusBanque('Règle introuvable ou déjà retirée.')
    con.execute('UPDATE regles SET actif=0 WHERE id=?', (number,))
    audit(con, 'retirer_regle', number, f"« {row['motif']} » -> {row['cible']}", 'regle')
    return f"OK : règle #{number} retirée (les opérations déjà classées le restent)."


# Reads ------------------------------------------------------------------------------------------------------------------

def describe(row):
    return {'id': row['id'], 'date': row['date'], 'montant': round(row['montant_cents'] / 100, 2), 'compte': row['compte'],
            'compte_bancaire': row['apercu'], 'categorie': category_of(row), 'par_regle': row['regle_id'] is not None,
            'description': row['description'], 'type_compte': row['type']}


def filtres_operations(data):
    where, params = [], []
    if data.get('statut') not in (None, 'a_classer', 'classee'):
        raise RefusBanque('statut : a_classer ou classee.')
    if data.get('id') is not None:
        where.append('o.id=?'); params.append(data['id'])
    if data.get('annee') is not None:
        where.append('substr(o.date, 1, 4)=?'); params.append(str(data['annee']))
    if data.get('mois') is not None:
        where.append('substr(o.date, 6, 2)=?'); params.append(f"{data['mois']:02d}")
    if data.get('compte') is not None:
        where.append('c.nom=?'); params.append(data['compte'])
    if data.get('statut') == 'a_classer':
        where.append('o.classe IS NULL')
    elif data.get('statut') == 'classee':
        where.append('o.classe IS NOT NULL')
    if data.get('categorie') is not None:
        where.append('(p.code=? OR k.code=?)'); params += [data['categorie']] * 2
    return (' WHERE ' + ' AND '.join(where) if where else ''), params


def operations(con, data):
    where, params = filtres_operations(data)
    rows = con.execute(OPERATION + where + ' ORDER BY o.date, o.id', params).fetchall()
    return {'operations': [describe(r) for r in rows[:200]], 'nombre': len(rows), 'tronque': len(rows) > 200}


OUTSIDE_BUDGET = ('virement_interne', 'depot_dojo')


def overview(con, data):
    where, params = ['substr(o.date, 1, 4)=?'], [str(data['annee'])]
    if data.get('mois') is not None:
        where.append('substr(o.date, 6, 2)=?'); params.append(f"{data['mois']:02d}")
    if data.get('compte') is not None:
        where.append('c.nom=?'); params.append(data['compte'])
    rows = con.execute(OPERATION + ' WHERE ' + ' AND '.join(where), params).fetchall()
    lines, outside, pending = {}, {}, {'nombre': 0, 'entrees': 0, 'sorties': 0}
    for r in rows:
        code = category_of(r)
        if code is None:
            pending['nombre'] += 1
            pending['entrees' if r['montant_cents'] > 0 else 'sorties'] += abs(r['montant_cents'])
            continue
        bucket = outside if code in OUTSIDE_BUDGET else lines
        line = bucket.setdefault(code, {'categorie': code, 'dojo': r['classe'] == 'depense_dojo', 'entrees': 0, 'sorties': 0, 'nombre': 0})
        line['entrees' if r['montant_cents'] > 0 else 'sorties'] += abs(r['montant_cents'])
        line['nombre'] += 1

    def money(items):
        return [{**v, 'entrees': round(v['entrees'] / 100, 2), 'sorties': round(v['sorties'] / 100, 2)}
                for v in sorted(items.values(), key=lambda v: -v['sorties'])]
    return {'annee': data['annee'], **({'mois': data['mois']} if data.get('mois') else {}),
            'par_categorie': money(lines), 'hors_budget': money(outside),
            'total_entrees': round(sum(v['entrees'] for v in lines.values()) / 100, 2),
            'total_sorties': round(sum(v['sorties'] for v in lines.values()) / 100, 2),
            'a_classer': {**pending, 'entrees': round(pending['entrees'] / 100, 2), 'sorties': round(pending['sorties'] / 100, 2)},
            'notes': ['hors_budget : virements entre vos comptes et dépôts du dojo déjà comptés; exclus des totaux.']}


def reference(con):
    return {'categories_perso': [dict(r) for r in con.execute('SELECT code, nom, note FROM categories_perso WHERE actif=1 ORDER BY id')],
            'comptes_bancaires': [dict(r) for r in con.execute('SELECT b.apercu, c.nom AS compte, b.type FROM comptes_bancaires b '
                                                                'JOIN comptes c ON c.id=b.compte_id ORDER BY b.id')],
            'regles': [{'id': r['id'], 'motif': r['motif'], 'categorie': r['cible'], **({'pct': r['pct']} if r['pct'] is not None else {})}
                       for r in con.execute('SELECT * FROM regles WHERE actif=1 ORDER BY motif')]}
