"""Finances over MCP: closed reads and a lot of its own (WR_SAVE_FINANCES_BD), separate from the Dojo lot.

Same rules as the Dojo: no free SQL, each step rehearsed on a private copy, one backup, all or nothing.
"""
from datetime import date
from decimal import Decimal, InvalidOperation
import json

import base64

from core import regles
from core import factures
from socle import externe
from core.executor import execute_safe
from core.paths import home
from core.services.mcp_adapter import READ_ONLY, ToolError, WithImages, confirmed_on_mac
from core.services.mcp_writes import NOTHING, Lot, Operation, Proposal, args
from finances import natures

VARIABLE = 'UGOD_FINANCES_DB'
MODES = ('comptant', 'carte', 'virement', 'cheque')

READ_INSTRUCTIONS = (
    " Finances (entreprise individuelle du dojo) : montants en dollars, lignes du formulaire T2125; codes dans finances_reference. "
    "Les revenus du dojo viennent des paiements de la base Dojo. Pas de conseil fiscal : présente les chiffres comme une estimation à valider. Le comptant (paiements et autres revenus) est exclu des statistiques et du rapport annuel : inclure_comptant=true seulement quand l'utilisateur le demande explicitement, et dis toujours le montant comptant exclu quand il n'est pas 0. "
    "Montants, totaux, catégories et budget, Dojo comme Perso, se lisent sans fenêtre. Une description bancaire du compte "
    "Desjardins de type inconnu, le message d'un virement Interac et le texte d'une dépense qui en vient sont retenus "
    "(retenu, message_retenu) : inclure_texte_autrui=true seulement quand Ugo demande lui-même ce texte. "
    "Points pour le Japon : points_japon, sans fenêtre (totaux de points seulement). Dis les mois manquants, mis de côté ou "
    "illisibles et les périodes du rythme; jamais de chiffre deviné, jamais de coût de vol ou d'hôtel cherché. Pas de conseil "
    "sur les cartes ni sur l'usage des points. "
    "Budget familial : budget_etat, sur demande seulement. Dis toujours les "
    "avertissements (à classer, relevés manquants, mois en cours); le prorata est un repère, pas un dépassement. Chiffres "
    "seulement : jamais de budget proposé ni de conseil. Les dépenses de la famille ne se séparent jamais par titulaire."
)
WRITE_INSTRUCTIONS = (
    " Les dépenses s'ajoutent à un lot distinct de celui du Dojo : depense_preparer (ou compléter/annuler), puis "
    "finances_lot_preparer, récapitulatif complet, oui explicite de l'utilisateur dans un nouveau message, puis WR_SAVE_FINANCES_BD. "
    "Seuls date, montant et catégorie sont obligatoires; les champs manquants sont signalés dans les rapports, sans bloquer. "
    "Un paiement d'inscription de la session active passe par paiement_preparer (Dojo); revenu_preparer sert aux autres revenus "
    "(équipement, kimonos, guides, passages, abonnements saisis hors Agent Smith), jamais deux fois pour le même argent. "
    "Relevés bancaires : Ugo dépose le relevé PDF (ou OFX) dans le dossier imports d'Agent Smith sur le Mac, jamais dans Google "
    "Drive; releve_import_preparer le lit sur le Mac et contrôle ses soldes (au premier import, demande à l'utilisateur quel "
    "aperçu de compte est Dojo, lequel est Perso et lequel ignorer). Un refus se lit tel quel à Ugo, sans rien deviner. "
    "Si le résumé d'import a « manquants », dis à Ugo quels relevés déposer; releves_manquants les liste en tout temps. Ensuite banque_a_classer, puis operation_classer_preparer ou "
    "regle_preparer (motif -> catégorie, appliquée aussi aux opérations déjà à classer). Une dépense du dojo payée d'un compte "
    "perso se classe avec son code T2125; un dépôt du compte Dojo déjà compté dans les paiements se classe depot_dojo; "
    "un virement entre ses comptes, virement_interne. "
    f"Factures : Ugo dépose photo ou PDF dans « {factures.INBOX} » (Google Drive); factures_a_classer les liste, facture_voir "
    "en montre l'image. depense_preparer (ou depense_completer_preparer) avec facture = nom du fichier : à l'enregistrement, "
    f"le fichier est déplacé dans « {factures.FOLDER}/AAAA/ » et lié à la dépense. Vérifie toujours avec Ugo ce que tu as lu. "
    "Objectif de points du Japon : objectif_points_preparer (ou objectif_points_retirer_preparer), puis le lot Finances, comme "
    "une dépense. "
    "Budget : budget_preparer (une catégorie par étape) ou budget_retirer_preparer, puis le lot Finances; montants d'Ugo "
    "seulement. "
    "Carte additionnelle de la conjointe sur la Cobalt : au premier relevé, demande à Ugo si l'aperçu est sa carte (comptes = "
    "additionnel, ou remplace pour une nouvelle carte); jamais de lecture par titulaire. Le résumé d'import ne parle pas du "
    "budget."
)
INCOME_TYPES = ('abonnement', 'equipement', 'kimono', 'guide', 'passage', 'autre')


def run(action, argument=None):
    reply = execute_safe(action, [] if argument is None else argument if isinstance(argument, list) else [argument], timeout=15)
    try:
        if not reply.get('ok'):
            raise ValueError
        return json.loads(reply['result'])
    except (TypeError, ValueError):
        detail = (reply.get('error') or '').split('ERREUR : ')[-1]
        raise ToolError('Finances : ' + (detail or 'lecture indisponible.')) from None


# Reads of the Perso account need no « Autoriser » (spec 002, R11, E12): amounts, totals, categories and the budget are read
# like the Dojo's. Free text follows its source (R9): a bank description by its nature, an expense classed from a bank line
# by that line's rule; what is retained is shown only when Ugo asks (inclure_texte_autrui), marked as someone else's text.
RETAINED_NATURES = ('inconnue', 'virement_interac')


def judge_operation(o, asked, alertes):
    """o (a bank operation read) with its nature and its description as the rule says, in place."""
    nature, shown, message = natures.classer(o.pop('type_compte', None), o.get('description'))
    o['nature'] = nature
    if message:
        o['message_retenu'] = True
    if asked and (shown is None or message):
        o['description'] = externe.surveiller(externe.Autrui(o['description']), 'description', alertes)
        o.pop('message_retenu', None)
        return o
    o['description'] = shown if shown is None else externe.surveiller(shown, 'description', alertes)
    if shown is None:
        o['retenu'] = True
    return o


def judge_expense(d, asked, alertes):
    """d (an expense read): supplier and description as the bank line it was classed from, Ugo's own text otherwise."""
    source = d.pop('operation', None)
    retained = bool(source) and natures.classer(source['type'], source['description'])[0] in RETAINED_NATURES
    if retained and not asked:
        for key in FREE_TEXT:
            d.pop(key, None)
        d['retenu'] = True
    for key in ('fournisseur', 'description', 'piece'):
        if d.get(key):
            d[key] = externe.surveiller(externe.Autrui(d[key]) if retained else d[key], key if key != 'fournisseur' else 'titre', alertes)
    return d


def plain(value):
    """A value judged for a summary (the Mac's window, a preparation): what is set aside or retained is None."""
    return None if isinstance(value, dict) else value


def receipts_waiting(arguments=None):
    args(arguments or {}, ())
    rows = factures.inbox()
    return {'dossier': factures.INBOX, 'nombre': len(rows), 'factures': rows[:factures.MAX_LISTED],
            'message': "Pour lire une facture : facture_voir (nom). Pour l'entrer : depense_preparer avec facture = nom."}


def receipt_view(arguments):
    a = args(arguments, (), ('nom', 'depense'))
    if ('nom' in a) == ('depense' in a):
        raise ToolError('Donne nom (facture à classer) ou depense (numéro d’une dépense qui a sa facture), un seul.')
    if 'nom' in a:
        image, about = factures.preview(name=a['nom']), {'facture': a['nom'], 'dossier': factures.INBOX}
    else:
        row = current(a['depense'])
        if not row['facture']:
            raise ToolError(f"Dépense #{row['id']} : aucune facture liée.")
        image, about = factures.preview(stored=row['facture']), {'depense': row['id'], 'facture': f"{factures.FOLDER}/{row['facture']}"}
    # A receipt is written by its supplier (E10, R11): shown without a window, marked, so the signal is written.
    about['facture'] = externe.Autrui(about['facture'])
    out = WithImages({**about, 'message': "Image de la facture jointe : lis date, montant, fournisseur et taxes; "
                                          "montre-les à l'utilisateur avant de préparer quoi que ce soit."})
    out.images = [base64.b64encode(image).decode()]
    return out


def reference(arguments=None):
    args(arguments or {}, ())
    return run('finances_reference')


def year(value):
    if type(value) is not int or not 2000 <= value <= 2100:
        raise ToolError('annee invalide (ex. 2026).')
    return value


FREE_TEXT = ('fournisseur', 'description', 'piece', 'facture')


def flag(a, key):
    if key in a and type(a[key]) is not bool:
        raise ToolError(f'{key} doit être vrai ou faux.')
    return a.pop(key, False)


def expenses(arguments):
    a = args(arguments, (), ('annee', 'mois', 'categorie', 'incompletes', 'annulees', 'details', 'inclure_texte_autrui'))
    for key in ('incompletes', 'annulees'):
        flag(dict(a), key)
    details, asked = flag(a, 'details'), flag(a, 'inclure_texte_autrui')
    data = run('finances_depenses', json.dumps(a, ensure_ascii=False))
    alertes = []
    # The receipt's path stays on the Mac: the model learns whether one is linked.
    rows = [{**{k: v for k, v in d.items() if k != 'facture'}, 'facture_liee': bool(d.get('facture'))} for d in data['depenses']]
    if details or asked:
        data['depenses'] = [judge_expense(d, asked, alertes) for d in rows]
    else:
        # Supplier, description and receipt are free text: the model learns what is missing, not what is written.
        data['depenses'] = [{k: v for k, v in d.items() if k not in (*FREE_TEXT, 'operation')} for d in rows]
    return externe.avertir(data, alertes)


def full_report(arguments):
    a = args(arguments, ('annee',), ('inclure_comptant',))
    # Cash only when Ugo asks for it (D058).
    cash = flag(a, 'inclure_comptant')
    return run('finances_rapport', [str(year(a['annee']))] + (['comptant'] if cash else []))


def report(arguments):
    data = full_report(arguments)
    incomplete = data['a_completer']['depenses_incompletes']
    data['a_completer']['depenses_incompletes'] = {'nombre': len(incomplete), 'total': round(sum(d['montant'] for d in incomplete), 2),
                                                   'detail': 'finances_depenses avec incompletes=true'}
    return data


def printable_report(arguments):
    from core.finances_report import write_report
    data = full_report(arguments)
    confirmed_on_mac('WR_SAVE_RAPPORT_FICHIER — fichier HTML',
                     [f"Rapport financier {data['annee']} (page HTML), dans {home() / 'exports'}",
                      'Revenu comptant inclus' if data['comptant']['inclus'] else 'Revenu comptant exclu'])
    path = write_report(data)
    return {'fichier': str(path), 'annee': data['annee'],
            'message': "Rapport imprimable enregistré sur le Mac (ouvrir dans un navigateur, puis Imprimer)."}


def history(arguments):
    count = args(arguments or {}, (), ('nombre',)).get('nombre', 20)
    if type(count) is not int or not 1 <= count <= 100:
        raise ToolError('nombre doit être entre 1 et 100.')
    entries = run('finances_historique', str(count))['entries']
    return {'entries': [{k: e[k] for k in ('date', 'action', 'depense')} for e in entries]}


def cents(value):
    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        raise ToolError(f'Montant invalide; {NOTHING}.') from None
    if type(value) not in (int, float) or not amount.is_finite() or not 0 < amount <= 100000 or amount != amount.quantize(Decimal('0.01')):
        raise ToolError(f'Montant invalide (entre 0,01 et 100 000 $, au cent près); {NOTHING}.')
    return int(amount * 100)


def day(value):
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ToolError(f'Date invalide; format AAAA-MM-JJ; {NOTHING}.') from None
    if parsed > date.today():
        raise ToolError(f'Date dans le futur; {NOTHING}.')
    return parsed.isoformat()


def optional_text(a, key):
    value = a.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 200 or any(ord(c) < 32 for c in value):
        raise ToolError(f'{key} invalide; {NOTHING}.')
    return ' '.join(value.split()) or None


def mode(a):
    if a.get('mode') is not None and a['mode'] not in MODES:
        raise ToolError('mode : ' + ', '.join(MODES) + f'; {NOTHING}.')
    return a.get('mode')


def missing(values):
    # A linked receipt counts as the receipt.
    return [k for k in ('piece', 'fournisseur', 'mode') if not values.get(k) and not (k == 'piece' and values.get('facture'))]


def receipt(name, day, cents):
    """(stored path, summary line, move) for a receipt of the inbox about to be linked."""
    name, digest = factures.waiting(name)
    path = factures.destination(day, cents, name)
    return path, f'{name}, déplacée dans « {factures.FOLDER}/{path} »', {'source': name, 'destination': path, 'sha256': digest}


def prepare_expense(arguments):
    a = args(arguments, ('date', 'montant', 'categorie'), ('pourcentage', 'fournisseur', 'description', 'mode', 'piece', 'facture'))
    amount = cents(a['montant'])
    category = next((c for c in reference()['categories'] if c['code'] == a['categorie']), None)
    if category is None:
        raise ToolError(f'Catégorie inconnue; utilise un code de finances_reference; {NOTHING}.')
    pct = a.get('pourcentage', category['pct_deductible'])
    if pct is None:
        raise ToolError(f"Pourcentage déductible requis pour {category['code']} {category['nom']} (usage d'affaires); demande-le; {NOTHING}.")
    if type(pct) is not int or not 0 <= pct <= 100:
        raise ToolError(f'pourcentage : entier de 0 à 100; {NOTHING}.')
    values = {'date': day(a['date']), 'montant_cents': amount, 'categorie': category['code'], 'pct': pct, 'mode': mode(a),
              **{k: optional_text(a, k) for k in ('fournisseur', 'description', 'piece')}}
    values = {k: v for k, v in values.items() if v is not None}
    files, shown = (), None
    if a.get('facture') is not None:
        values['facture'], shown, move = receipt(a['facture'], values['date'], amount)
        files = (move,)
    summary = {'date': values['date'], 'montant': amount / 100, 'categorie': f"{category['code']} {category['nom']}",
               'pourcentage_deductible': pct, 'deductible': (amount * pct + 50) // 100 / 100,
               **{k: values.get(k) for k in ('fournisseur', 'description', 'mode', 'piece')}, **({'facture': shown} if shown else {}),
               'manque': missing(values)}
    return Proposal(summary, 'finances_depense', [json.dumps(values, ensure_ascii=False)], files)


def current(number):
    if type(number) is not int or number < 1:
        raise ToolError(f'id de dépense invalide; {NOTHING}.')
    rows = run('finances_depenses', json.dumps({'id': number, 'annulees': True}))['depenses']
    if not rows:
        raise ToolError(f'Dépense #{number} introuvable; {NOTHING}.')
    return rows[0]


def prepare_completion(arguments):
    a = args(arguments, ('id',), ('fournisseur', 'description', 'mode', 'piece', 'facture'))
    row = current(a['id'])
    if row['annulee']:
        raise ToolError(f"Dépense #{row['id']} annulée; {NOTHING}.")
    before = judge_expense(dict(row), False, [])
    changes = {k: optional_text(a, k) for k in ('fournisseur', 'description', 'piece') if k in a}
    if 'mode' in a:
        changes['mode'] = mode(a)
    files, shown = (), {}
    if a.get('facture') is not None:
        if row['facture']:
            raise ToolError(f"Dépense #{row['id']} : une facture est déjà liée; {NOTHING}.")
        changes['facture'], line, move = receipt(a['facture'], row['date'], int(round(row['montant'] * 100)))
        files, shown = (move,), {'facture': {'avant': None, 'apres': line}}
    if not changes:
        raise ToolError(f'Aucun champ à compléter; {NOTHING}.')
    summary = {'depense': row['id'], 'date': row['date'], 'montant': row['montant'], 'categorie': row['categorie'],
               'changements': {**{k: {'avant': plain(before.get(k)), 'apres': v} for k, v in changes.items() if k != 'facture'}, **shown}}
    return Proposal(summary, 'finances_completer', [json.dumps({'id': row['id'], **changes}, ensure_ascii=False)], files)


def prepare_cancellation(arguments):
    row = current(args(arguments, ('id',))['id'])
    if row['annulee']:
        raise ToolError(f"Dépense #{row['id']} déjà annulée; {NOTHING}.")
    supplier = plain(judge_expense(dict(row), False, []).get('fournisseur'))
    summary = {'depense': row['id'], 'date': row['date'], 'montant': row['montant'],
               'categorie': f"{row['categorie']} {row['categorie_nom']}", 'fournisseur': supplier,
               'effet': "Annulée : exclue des rapports, conservée dans l'historique."}
    return Proposal(summary, 'finances_annuler', [json.dumps({'id': row['id']})])


def prepare_income(arguments):
    a = args(arguments, ('date', 'montant', 'type'), ('mode', 'description'))
    if a['type'] not in INCOME_TYPES:
        raise ToolError('type : ' + ', '.join(INCOME_TYPES) + f'; {NOTHING}.')
    values = {'date': day(a['date']), 'montant_cents': cents(a['montant']), 'type': a['type'], 'mode': mode(a),
              'description': optional_text(a, 'description')}
    values = {k: v for k, v in values.items() if v is not None}
    summary = {'date': values['date'], 'montant': values['montant_cents'] / 100, 'type': a['type'],
               'mode': values.get('mode'), 'description': values.get('description'),
               **({'manque': ['mode']} if 'mode' not in values else {})}
    return Proposal(summary, 'finances_revenu', [json.dumps(values, ensure_ascii=False)])


def prepare_income_cancellation(arguments):
    number = args(arguments, ('id',))['id']
    if type(number) is not int or number < 1:
        raise ToolError(f'id de revenu invalide; {NOTHING}.')
    rows = run('finances_revenus', json.dumps({'id': number, 'annules': True}))['revenus']
    if not rows:
        raise ToolError(f'Revenu #{number} introuvable; {NOTHING}.')
    row = rows[0]
    if row['annule']:
        raise ToolError(f"Revenu #{row['id']} déjà annulé; {NOTHING}.")
    summary = {'revenu': row['id'], 'date': row['date'], 'montant': row['montant'], 'type': row['type'],
               'effet': "Annulé : exclu des rapports, conservé dans l'historique."}
    return Proposal(summary, 'finances_annuler_revenu', [json.dumps({'id': row['id']})])


def incomes(arguments):
    a = args(arguments, (), ('annee', 'mois', 'type', 'annules', 'details', 'inclure_comptant'))
    flag(dict(a), 'annules')
    details = flag(a, 'details')
    # Cash only when Ugo asks for it (D058).
    if flag(a, 'inclure_comptant'):
        a['comptant'] = True
    data = run('finances_revenus', json.dumps(a, ensure_ascii=False))
    if not details:
        data['revenus'] = [{k: v for k, v in r.items() if k != 'description'} for r in data['revenus']]
    return data


ACCOUNTS = ('Dojo', 'Perso')
LINKS = ACCOUNTS + ('ignore',)  # a statement's account: Dojo, Perso, or ignored (never asked again)
# The section of the additional card of the Cobalt (spec 004): the first one, or a new card that replaces it.
ADDITIONAL = ('additionnel', 'remplace')


def bank_category(code):
    ref = reference()
    t2125 = {c['code']: c for c in ref['categories']}
    perso = {c['code']: c for c in ref['categories_perso']}
    if code not in t2125 and code not in perso:
        raise ToolError(f'Catégorie inconnue; code T2125 ou catégorie perso de finances_reference; {NOTHING}.')
    return (f"{code} {t2125[code]['nom']} (dépense du dojo)" if code in t2125 else perso[code]['nom']), code in t2125


def percentage(a):
    pct = a.get('pourcentage')
    if pct is not None and (type(pct) is not int or not 0 <= pct <= 100):
        raise ToolError(f'pourcentage : entier de 0 à 100; {NOTHING}.')
    return pct


def prepare_import(arguments):
    a = args(arguments, ('fichier',), ('comptes',))
    links = a.get('comptes') or {}
    if not isinstance(links, dict) or not all(isinstance(k, str) and v in LINKS + ADDITIONAL for k, v in links.items()):
        raise ToolError(f'comptes : {{aperçu du relevé : "Dojo", "Perso" ou "ignore"; "additionnel" ou "remplace" pour une '
                        f'carte additionnelle}}; {NOTHING}.')
    request = {'fichier': a['fichier'], **({'comptes': links} if links else {})}
    data = run('finances_import_analyser', json.dumps(request, ensure_ascii=False))
    if data['a_lier']:
        cards = data.get('cartes_a_lier', [])
        accounts = [preview for preview in data['a_lier'] if preview not in cards]
        # One question for the unknown accounts and the section of an additional card (spec 004, research R2).
        asked = (['Comptes du relevé pas encore liés : ' + ', '.join(accounts) + ". Demande à l'utilisateur lequel est Dojo, "
                  'lequel est Perso et lequel ignorer, puis reprends avec comptes = {aperçu: "Dojo", "Perso" ou "ignore"}']
                 if accounts else []) + \
                [f"Section de carte additionnelle ({card}) : demande à l'utilisateur si c'est la carte additionnelle de sa "
                 f'conjointe, puis reprends avec comptes = {{"{card}": "additionnel"}}' for card in cards]
        raise ToolError('. '.join(asked) + '; ' + NOTHING + '.')
    footprint = data.pop('empreinte')
    summary = {'fichier': a['fichier'], **{k: v for k, v in data.items()
                                           if k not in ('a_lier', 'cartes_a_lier', 'deja_importe', 'rien_de_nouveau')}}
    if data['rien_de_nouveau']:
        return {**summary, 'message': 'Rien de nouveau : ce relevé est déjà importé.'}
    return Proposal(summary, 'finances_importer', [json.dumps({**request, 'empreinte': footprint}, ensure_ascii=False)])


def bank_operation(number):
    if type(number) is not int or number < 1:
        raise ToolError(f"id d'opération invalide; {NOTHING}.")
    rows = run('finances_operations', json.dumps({'id': number}))['operations']
    if not rows:
        raise ToolError(f'Opération #{number} introuvable; {NOTHING}.')
    return rows[0]


def prepare_classify(arguments):
    a = args(arguments, ('id', 'categorie'), ('pourcentage',))
    row = bank_operation(a['id'])
    shown = plain(judge_operation(dict(row), False, [])['description'])
    name, dojo = bank_category(a['categorie'])
    pct = percentage(a)
    values = {'id': row['id'], 'categorie': a['categorie'], **({'pct': pct} if pct is not None else {})}
    summary = {'operation': row['id'], 'date': row['date'], 'montant': row['montant'], 'compte': row['compte'],
               'description': shown, 'avant': row['categorie'] or 'à classer', 'apres': name,
               **({'pourcentage_deductible': pct} if pct is not None else {}),
               **({'effet': 'Crée la dépense T2125 correspondante.', 'depense_t2125': abs(row['montant'])} if dojo else {}),
               **({'avertissement': 'La dépense du dojo déjà créée pour cette opération sera annulée.'}
                  if (row['categorie'] or '')[:1].isdigit() else {})}
    return Proposal(summary, 'finances_classer', [json.dumps(values, ensure_ascii=False)])


def prepare_rule(arguments):
    a = args(arguments, ('motif', 'categorie'), ('pourcentage',))
    name, _ = bank_category(a['categorie'])
    pct = percentage(a)
    if not isinstance(a['motif'], str) or len(a['motif']) > 60:
        raise ToolError(f'motif : de 3 à 60 caractères; {NOTHING}.')
    values = {'motif': a['motif'], 'categorie': a['categorie'], **({'pct': pct} if pct is not None else {})}
    data = run('finances_regle_analyser', json.dumps(values, ensure_ascii=False))
    touched = [judge_operation(o, False, []) for o in data['operations']]
    summary = {'motif': data['motif'], 'categorie': name, **({'pourcentage_deductible': pct} if pct is not None else {}),
               'operations_classees_maintenant': len(touched),
               'exemples': [{**{k: o[k] for k in ('id', 'date', 'montant', 'compte')}, 'description': plain(o['description'])}
                            for o in touched[:20]],
               'effet': 'Classera aussi les prochains relevés dont la description contient ce motif.'}
    return Proposal(summary, 'finances_regle', [json.dumps({**values, 'motif': data['motif']}, ensure_ascii=False)])


def prepare_rule_removal(arguments):
    number = args(arguments, ('id',))['id']
    rule = next((r for r in reference()['regles'] if r['id'] == number), None) if type(number) is int else None
    if rule is None:
        raise ToolError(f'Règle introuvable (voir finances_reference); {NOTHING}.')
    summary = {'regle': number, 'motif': rule['motif'], 'categorie': rule['categorie'],
               'effet': "Ne s'applique plus aux prochains relevés; les opérations déjà classées le restent."}
    return Proposal(summary, 'finances_retirer_regle', [json.dumps({'id': number})])


def bank_filters(a):
    if a.get('compte') is not None and a['compte'] not in ACCOUNTS:
        raise ToolError('compte : Dojo ou Perso.')
    return a


def judged_operations(request, asked):
    data = run('finances_operations', json.dumps(request, ensure_ascii=False))
    alertes = []
    data['operations'] = [judge_operation(o, asked, alertes) for o in data['operations']]
    return externe.avertir(data, alertes)


def bank_operations(arguments):
    a = bank_filters(args(arguments or {}, (), ('annee', 'mois', 'compte', 'statut', 'categorie', 'inclure_texte_autrui')))
    asked = flag(a, 'inclure_texte_autrui')
    return judged_operations(a, asked)


def to_sort(arguments):
    a = bank_filters(args(arguments or {}, (), ('annee', 'mois', 'compte', 'inclure_texte_autrui')))
    asked = flag(a, 'inclure_texte_autrui')
    return judged_operations({**a, 'statut': 'a_classer'}, asked)


def statements_missing(arguments):
    a = args(arguments, (), ('compte',))
    if a.get('compte') is not None and a['compte'] not in ACCOUNTS:
        raise ToolError(f'compte : Dojo ou Perso; {NOTHING}.')
    return run('finances_manquants', json.dumps(a, ensure_ascii=False))


# Points of the Cobalt and the Japan goal (spec 005): totals of points and dates only, read without Autoriser (D081). The
# script selects only those columns; what it returns is filtered again here, key by key, at every level.
POINTS_KEYS = {'solde', 'points', 'au', 'du', 'statut', 'dernier_mis_de_cote', 'objectif', 'cible', 'date_visee', 'depuis',
               'ecart', 'cible_atteinte', 'rythme', 'points_par_mois', 'periodes', 'estimable', 'raison', 'date_estimee',
               'manques', 'trous', 'illisibles', 'mis_de_cote', 'ruptures', 'fin', 'debut', 'historique', 'precedent', 'gagnes',
               'utilises', 'nouveau', 'objectifs_passes', 'fin_le', 'message', 'atteignable', 'mois_restants',
               'points_par_mois_necessaires', 'en_retard_depuis'}
POINTS_NOTES = ["Totaux de points seulement : aucun montant en dollars, aucune opération. Aucun chiffre deviné : un mois "
                "manquant, mis de côté ou illisible est dit tel quel.",
                "Rythme = moyenne des points gagnés (pas des points utilisés) sur les relevés nommés dans periodes.",
                "La cible vient d'Ugo seulement : ne cherche ni n'invente le coût en points d'un vol ou d'un hôtel.",
                "date_estimee : le mois où se termine le relevé qui atteindrait la cible, à ce rythme; en_retard_depuis : "
                "aucun relevé depuis cette date, le solde est ancien."]


def kept(value, keys=POINTS_KEYS):
    if isinstance(value, dict):
        return {k: kept(v, keys) for k, v in value.items() if k in keys}
    if isinstance(value, list):
        return [kept(v, keys) for v in value]
    return value


def japan_points(arguments):
    a = args(arguments or {}, (), ('historique',))
    data = run('finances_points', json.dumps({'historique': True} if flag(a, 'historique') else {}))
    return {**kept(data), 'notes': POINTS_NOTES}


def points_text(goal, since=False):
    out = f"{goal['cible']:,} points".replace(',', ' ') + (f", visé le {goal['date_visee']}" if goal.get('date_visee') else '')
    if since:
        return out + f" (depuis {goal['depuis']})"
    return out if goal.get('date_visee') else out + ', sans date visée'


def goal_in_force():
    return run('finances_points', '{}').get('objectif')


def prepare_goal(arguments):
    a = args(arguments, ('cible',), ('date_visee',))
    target, wanted = a['cible'], a.get('date_visee')
    if type(target) is not int or not 1 <= target <= 10_000_000:
        raise ToolError(f'cible : un nombre entier de points, de 1 à 10 000 000; {NOTHING}.')
    if wanted is not None:
        try:
            if not isinstance(wanted, str) or len(wanted) != 10 or date.fromisoformat(wanted).isoformat() != wanted:
                raise ValueError
        except ValueError:
            raise ToolError(f'date_visee invalide; format AAAA-MM-JJ (un mois seul : le premier jour du mois); {NOTHING}.') from None
        if date.fromisoformat(wanted) <= date.today():
            raise ToolError(f"date_visee : aujourd'hui ou passée; donne une date future; {NOTHING}.")
    goal = {'cible': target, **({'date_visee': wanted} if wanted else {})}
    old = goal_in_force()
    if old and (old['cible'], old.get('date_visee')) == (target, wanted):
        # The lot answers « already in place » and adds nothing (the script would refuse it too).
        return {'objectif': points_text(goal)}
    summary = {'objectif': points_text(goal), **({'remplace': points_text(old, since=True)} if old else {}),
               'effet': "Remplace l'objectif en vigueur; l'ancien reste dans l'historique" if old else "Fixe l'objectif"}
    return Proposal(summary, 'finances_objectif', [json.dumps({'cible': target, 'date_visee': wanted})])


def prepare_goal_removal(arguments):
    args(arguments or {}, ())
    old = goal_in_force()
    if old is None:
        raise ToolError(f'Aucun objectif en vigueur; {NOTHING}.')
    summary = {'retire': points_text(old, since=True), 'effet': "Plus d'objectif en vigueur; il reste dans l'historique."}
    return Proposal(summary, 'finances_retirer_objectif', [json.dumps({})])


# Budget familial (spec 004) : a budget kept is an amount of the Perso account, so reading one asks Autoriser for every year
# it touches, in one window (research R9); a first budget shows only what Ugo just gave, without a window.
SPENDING = ('logement', 'epicerie', 'restaurants', 'transport', 'sante', 'assurances', 'telecom', 'loisirs', 'vetements',
            'cadeaux', 'frais_bancaires')
FIRST_MONTH = '2026-01'


def shifted(months):
    today = date.today()
    n = today.year * 12 + today.month - 1 + months
    return f'{n // 12:04d}-{n % 12 + 1:02d}'


def budget_month(value):
    """debut: AAAA-MM from 2026-01 to 12 months after the current month; the current month when Ugo gives none."""
    if value is None:
        return shifted(0)
    import re
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{4}-(0[1-9]|1[0-2])', value):
        raise ToolError(f'debut invalide; format AAAA-MM; {NOTHING}.')
    if not FIRST_MONTH <= value <= shifted(12):
        raise ToolError(f'debut : de {FIRST_MONTH} à {shifted(12)} (12 mois après le mois courant); {NOTHING}.')
    return value


def spending(code):
    """(code, name) of a spending category of the Perso account, never revenus, virement_interne nor depot_dojo (FR-008)."""
    if code not in SPENDING:
        raise ToolError(f'categorie : une catégorie de dépense perso ({", ".join(SPENDING)}); {NOTHING}.')
    name = next((c['nom'] for c in reference()['categories_perso'] if c['code'] == code), None)
    if name is None:
        raise ToolError(f'categorie inactive; {NOTHING}.')
    return code, name


def dollars_text(cents):
    whole, part = divmod(cents, 100)
    return f'{whole:,}'.replace(',', ' ') + f',{part:02d} $'


def budget_lines(code, start):
    """(line in force at start or None, next line after start or None, whether the category has any budget kept)."""
    lines = run('finances_budget', json.dumps({'categorie': code}))['lignes']
    before = [line for line in lines if line['debut'] <= start]
    after = [line for line in lines if line['debut'] > start]
    return (before[-1] if before else None), (after[0] if after else None), bool(lines)


def prepare_budget(arguments):
    a = args(arguments, ('categorie', 'montant'), ('debut',))
    code, name = spending(a['categorie'])
    amount = cents(a['montant'])
    start = budget_month(a.get('debut'))
    current, later, _ = budget_lines(code, start)
    shown = f'{name} : {dollars_text(amount)} par mois dès {start}'
    if current and current['montant_cents'] == amount:
        # The lot answers « already in place » and adds nothing (the script would refuse it too).
        return {'budget': shown}
    replaced = current if current and current['montant_cents'] is not None else None
    summary = {'budget': shown, **({'remplace': f"{dollars_text(replaced['montant_cents'])} (dès {replaced['debut']})"} if replaced else {}),
               **({'ensuite': (f"{dollars_text(later['montant_cents'])} dès {later['debut']}" if later['montant_cents'] is not None
                               else f"retiré dès {later['debut']}")} if later else {}),
               'effet': f"Change le budget à partir de {start}; les mois d'avant gardent l'ancien" if replaced else 'Fixe le budget'}
    return Proposal(summary, 'finances_budget_fixer', [json.dumps({'categorie': code, 'montant_cents': amount, 'debut': start})])


def prepare_budget_removal(arguments):
    a = args(arguments, ('categorie',), ('debut',))
    code, name = spending(a['categorie'])
    start = budget_month(a.get('debut'))
    current, later, _ = budget_lines(code, start)
    if current is None or current['montant_cents'] is None:
        raise ToolError(f'Aucun budget en vigueur pour {code} en {start}; {NOTHING}.')
    summary = {'retire': f"{name} : {dollars_text(current['montant_cents'])} (dès {current['debut']})", 'a_partir_de': start,
               **({'ensuite': (f"{dollars_text(later['montant_cents'])} dès {later['debut']}" if later['montant_cents'] is not None
                               else f"retiré dès {later['debut']}")} if later else {}),
               'effet': f"Plus de budget pour cette catégorie à partir de {start}; les mois d'avant le gardent."}
    return Proposal(summary, 'finances_budget_retirer', [json.dumps({'categorie': code, 'debut': start})])


# The state of the budget (spec 004): totals by category and month, dates and masked previews only (FR-015), after
# Autoriser like any amount of the Perso account (FR-014). The script selects nothing else; this list filters it again.
BUDGET_KEYS = {'periode', 'annee', 'mois', 'du', 'au', 'categories', 'categorie', 'nom', 'depense', 'budget', 'ecart',
               'pourcentage', 'depassee', 'depuis', 'mois_comptes', 'total', 'sans_budget', 'depassees', 'avertissements',
               'a_classer', 'nombre', 'montant', 'incomplet', 'apercu', 'raison', 'mois_en_cours', 'jours_ecoules', 'jours',
               'budget_au_prorata', 'par_categorie', 'complet', 'historique', 'debut', 'cree_le', 'remplace_le', 'message', 'notes',
               'aucun_releve', 'raisons_incomplet', 'action'}


def budget_state(arguments):
    a = args(arguments, ('annee',), ('mois', 'historique'))
    wanted, today = a.get('mois'), date.today()
    if type(a['annee']) is not int or not 2026 <= a['annee'] <= 2100:
        raise ToolError('annee invalide (2026 au plus tôt).')
    if a['annee'] > today.year:
        raise ToolError('annee dans le futur : rien à comparer.')
    if wanted is not None and (type(wanted) is not int or not 1 <= wanted <= 12):
        raise ToolError('mois invalide (1 à 12).')
    if wanted is not None and a['annee'] == today.year and wanted > today.month:
        raise ToolError('mois dans le futur : rien à comparer.')
    request = {'annee': a['annee'], **({'mois': wanted} if wanted is not None else {}), **({'historique': True} if flag(a, 'historique') else {})}
    return kept(run('finances_budget', json.dumps(request)), BUDGET_KEYS)


def bank_overview(arguments):
    a = bank_filters(args(arguments, ('annee',), ('mois', 'compte')))
    year(a['annee'])
    return run('finances_resume', json.dumps(a, ensure_ascii=False))


TEXT = {'type': 'string', 'maxLength': 200}
MONTH = {'type': 'integer', 'minimum': 1, 'maximum': 12}
ACCOUNT = {'type': 'string', 'enum': list(ACCOUNTS)}
PCT = {'type': 'integer', 'minimum': 0, 'maximum': 100,
       'description': 'Pourcentage déductible, seulement pour une dépense du dojo (requis pour 9281).'}
EXPENSE_ID = {'type': 'integer', 'minimum': 1, 'description': 'Numéro de la dépense (voir finances_depenses).'}

OPERATIONS = {op.key: op for op in [
    Operation('depense', 'depense_preparer', 'Dépense',
              "Ajoute une dépense du dojo. Obligatoires : date (AAAA-MM-JJ), montant en dollars, categorie (code T2125 de "
              "finances_reference). pourcentage déductible : celui de la catégorie si omis (requis pour 9281 véhicule). "
              "Optionnels : fournisseur, description, mode (comptant, carte, virement, cheque), piece (référence du reçu), "
              f"facture (nom d'un fichier de « {factures.INBOX} » : déplacé à l'enregistrement et lié à la dépense).",
              {'date': {'type': 'string'}, 'montant': {'type': 'number', 'exclusiveMinimum': 0, 'maximum': 100000},
               'categorie': {'type': 'string'}, 'pourcentage': {'type': 'integer', 'minimum': 0, 'maximum': 100},
               'fournisseur': TEXT, 'description': TEXT, 'mode': {'type': 'string', 'enum': list(MODES)}, 'piece': TEXT,
               'facture': {'type': 'string', 'maxLength': 120}},
              ('date', 'montant', 'categorie'), prepare_expense),
    Operation('completer', 'depense_completer_preparer', 'Compléter une dépense',
              "Complète ou corrige les champs facultatifs d'une dépense : fournisseur, description, mode, piece, et facture "
              f"(nom d'un fichier de « {factures.INBOX} », seulement si la dépense n'en a pas). "
              "Pour changer date, montant ou catégorie : annuler la dépense et en ajouter une nouvelle.",
              {'id': EXPENSE_ID, 'fournisseur': TEXT, 'description': TEXT, 'mode': {'type': 'string', 'enum': list(MODES)}, 'piece': TEXT,
               'facture': {'type': 'string', 'maxLength': 120}},
              ('id',), prepare_completion),
    Operation('annulation', 'depense_annulation_preparer', 'Annuler une dépense',
              "Annule une dépense (exclue des rapports, conservée dans l'historique).", {'id': EXPENSE_ID}, ('id',), prepare_cancellation),
    Operation('revenu', 'revenu_preparer', 'Autre revenu',
              "Ajoute un revenu qui ne passe pas par les inscriptions du Dojo. Obligatoires : date (AAAA-MM-JJ), montant en dollars, "
              "type (abonnement, equipement, kimono, guide, passage, autre). Optionnels : mode (comptant, carte, virement, cheque), description.",
              {'date': {'type': 'string'}, 'montant': {'type': 'number', 'exclusiveMinimum': 0, 'maximum': 100000},
               'type': {'type': 'string', 'enum': list(INCOME_TYPES)}, 'mode': {'type': 'string', 'enum': list(MODES)}, 'description': TEXT},
              ('date', 'montant', 'type'), prepare_income),
    Operation('revenu_annulation', 'revenu_annulation_preparer', 'Annuler un autre revenu',
              "Annule un autre revenu (exclu des rapports, conservé dans l'historique).",
              {'id': {'type': 'integer', 'minimum': 1, 'description': 'Numéro du revenu (voir finances_revenus).'}}, ('id',),
              prepare_income_cancellation),
    Operation('releve', 'releve_import_preparer', 'Importer un relevé bancaire',
              "Importe un relevé PDF (lu sur le Mac, soldes contrôlés au cent près) ou OFX (.ofx, .qfx) déposé dans le dossier "
              "imports d'Agent Smith : nom du fichier seulement. Les opérations déjà importées sont ignorées; les règles classent "
              "ce qu'elles reconnaissent, le reste est à classer. Au premier import d'un compte : comptes = {aperçu (ex. "
              "« …4567 EOP ») : Dojo, Perso ou ignore}, demandé à l'utilisateur; un compte ignoré n'est plus demandé, et Dojo "
              "ou Perso le rattache. Section de la carte additionnelle d'une Cobalt : additionnel au premier relevé, remplace "
              "pour sa nouvelle carte, seulement sur la réponse de l'utilisateur.",
              {'fichier': {'type': 'string', 'maxLength': 100},
               'comptes': {'type': 'object', 'additionalProperties': {'type': 'string', 'enum': list(LINKS + ADDITIONAL)},
                           'maxProperties': 10}},
              ('fichier',), prepare_import),
    Operation('classer', 'operation_classer_preparer', 'Classer une opération bancaire',
              "Classe (ou reclasse) une opération bancaire : categorie = code T2125 (dépense du dojo, crée la dépense; aussi pour une "
              "dépense du dojo payée d'un compte perso) ou code de catégorie perso (finances_reference), dont virement_interne et depot_dojo.",
              {'id': {'type': 'integer', 'minimum': 1, 'description': "Numéro de l'opération (banque_a_classer)."},
               'categorie': {'type': 'string', 'maxLength': 40}, 'pourcentage': PCT},
              ('id', 'categorie'), prepare_classify),
    Operation('regle', 'regle_preparer', 'Règle de classement',
              "Ajoute une règle : une description bancaire qui contient motif (sans égard aux majuscules ni aux accents) va dans categorie. "
              "Classe aussi les opérations à classer qui correspondent. Choisis un motif court et distinctif (ex. « iga », « hydro-quebec »).",
              {'motif': {'type': 'string', 'minLength': 3, 'maxLength': 60}, 'categorie': {'type': 'string', 'maxLength': 40},
               'pourcentage': PCT},
              ('motif', 'categorie'), prepare_rule),
    Operation('regle_retrait', 'regle_retirer_preparer', 'Retirer une règle',
              "Retire une règle de classement (voir finances_reference); les opérations déjà classées le restent.",
              {'id': {'type': 'integer', 'minimum': 1}}, ('id',), prepare_rule_removal),
    Operation('objectif_points', 'objectif_points_preparer', 'Objectif Japon',
              "Fixe ou change l'objectif de points Membership Rewards du voyage au Japon : cible en points (celle qu'Ugo donne, "
              "jamais cherchée ni inventée), date_visee facultative (AAAA-MM-JJ, dans le futur; un mois seul : le premier jour "
              "du mois, dis-le à Ugo). L'ancien objectif reste dans l'historique.",
              {'cible': {'type': 'integer', 'minimum': 1, 'maximum': 10000000},
               'date_visee': {'type': 'string', 'pattern': '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'}},
              ('cible',), prepare_goal),
    Operation('objectif_points_retrait', 'objectif_points_retirer_preparer', "Retirer l'objectif Japon",
              "Retire l'objectif de points du voyage au Japon (il reste dans l'historique).", {}, (), prepare_goal_removal),
    Operation('budget', 'budget_preparer', 'Budget',
              "Fixe ou change le budget mensuel d'une catégorie de dépense perso (une catégorie par étape; plusieurs étapes dans "
              "le même lot). Montant en dollars donné par Ugo seulement, jamais proposé; debut (AAAA-MM) = le mois courant si "
              "Ugo n'en dit pas. Les mois d'avant gardent leur budget. Fenêtre Autoriser sur le Mac seulement si la catégorie a "
              "déjà un budget.",
              {'categorie': {'type': 'string', 'enum': list(SPENDING)},
               'montant': {'type': 'number', 'exclusiveMinimum': 0, 'maximum': 100000},
               'debut': {'type': 'string', 'pattern': '^[0-9]{4}-[0-9]{2}$'}},
              ('categorie', 'montant'), prepare_budget),
    Operation('budget_retrait', 'budget_retirer_preparer', 'Retirer un budget',
              "Retire le budget d'une catégorie perso à partir d'un mois (debut AAAA-MM, le mois courant par défaut); les mois "
              "d'avant le gardent. Fenêtre Autoriser sur le Mac.",
              {'categorie': {'type': 'string', 'enum': list(SPENDING)}, 'debut': {'type': 'string', 'pattern': '^[0-9]{4}-[0-9]{2}$'}},
              ('categorie',), prepare_budget_removal),
]}

YEAR = {'type': 'integer', 'minimum': 2000, 'maximum': 2100}

TOOLS = [
    {'name': 'finances_reference', 'title': 'Référence Finances',
     'description': 'Comptes, catégories de dépenses (code T2125, nom, pourcentage déductible par défaut) et modes de paiement.',
     'inputSchema': {'type': 'object', 'additionalProperties': False, 'properties': {}}},
    {'name': 'finances_depenses', 'title': 'Lister les dépenses',
     'description': 'Dépenses filtrées par annee, mois, categorie (code); incompletes=true : seulement celles où manque reçu, fournisseur ou mode '
                    '(champ manque). Fournisseur, description et pièce seulement avec details=true, quand la question les demande; '
                    'ceux d\'une dépense classée depuis une ligne bancaire retenue : retenu.',
     'inputSchema': {'type': 'object', 'additionalProperties': False,
                     'properties': {'annee': YEAR, 'mois': {'type': 'integer', 'minimum': 1, 'maximum': 12}, 'categorie': {'type': 'string'},
                                    'incompletes': {'type': 'boolean'}, 'annulees': {'type': 'boolean'}, 'details': {'type': 'boolean'},
                                    'inclure_texte_autrui': regles.INCLURE}}},
    {'name': 'finances_revenus', 'title': 'Lister les autres revenus',
     'description': 'Autres revenus (hors inscriptions du Dojo) filtrés par annee, mois, type. Description seulement avec details=true. '
                    "Revenus comptant exclus (comptant_exclu : nombre et total retirés), sauf inclure_comptant=true, seulement quand "
                    "l'utilisateur le demande explicitement.",
     'inputSchema': {'type': 'object', 'additionalProperties': False,
                     'properties': {'annee': YEAR, 'mois': {'type': 'integer', 'minimum': 1, 'maximum': 12},
                                    'type': {'type': 'string', 'enum': list(INCOME_TYPES)}, 'annules': {'type': 'boolean'},
                                    'details': {'type': 'boolean'}, 'inclure_comptant': {'type': 'boolean', 'description': "true seulement si l'utilisateur demande explicitement le comptant."}}}},
    {'name': 'finances_rapport', 'title': 'Rapport annuel',
     'description': "Rapport d'une année : revenus (paiements du Dojo + autres revenus) par mode, dépenses par ligne T2125 (brut et déductible), "
                    "revenu net estimé, et le nombre de dépenses à compléter (détail : finances_depenses). "
                    "Revenu comptant exclu (comptant.exclu : nombre et total retirés; dis-le toujours à l'utilisateur quand le total "
                    "n'est pas 0), sauf inclure_comptant=true, seulement quand l'utilisateur le demande explicitement.",
     'inputSchema': {'type': 'object', 'additionalProperties': False, 'required': ['annee'], 'properties': {'annee': YEAR, 'inclure_comptant': {'type': 'boolean', 'description': "true seulement si l'utilisateur demande explicitement le comptant."}}}},
    {'name': 'WR_SAVE_RAPPORT_FICHIER', 'title': 'WR_SAVE_RAPPORT_FICHIER',
     'description': "ÉCRITURE D'UN FICHIER (jamais dans les bases). Enregistre le rapport annuel en page HTML imprimable sur le Mac (dossier exports) et retourne son chemin. "
                    "Une fenêtre s'ouvre sur le Mac : rien n'est écrit tant que l'utilisateur n'y clique pas Enregistrer. "
                    "Revenu comptant exclu, avec le montant retiré en tête de page, sauf inclure_comptant=true sur demande explicite.",
     'inputSchema': {'type': 'object', 'additionalProperties': False, 'required': ['annee'], 'properties': {'annee': YEAR, 'inclure_comptant': {'type': 'boolean', 'description': "true seulement si l'utilisateur demande explicitement le comptant."}}}},
    {'name': 'banque_a_classer', 'title': 'Opérations à classer',
     'description': 'Opérations bancaires importées pas encore classées, avec leur nature et leur description (retenue si '
                    'inconnue), pour proposer une catégorie ou une règle.',
     'inputSchema': {'type': 'object', 'additionalProperties': False,
                     'properties': {'annee': YEAR, 'mois': MONTH, 'compte': ACCOUNT, 'inclure_texte_autrui': regles.INCLURE}}},
    {'name': 'banque_operations', 'title': 'Opérations bancaires',
     'description': "Opérations bancaires importées filtrées par annee, mois, compte, statut (a_classer, classee), categorie. "
                    "Chaque opération a sa nature; description retenue si inconnue, nom seulement pour un virement Interac.",
     'inputSchema': {'type': 'object', 'additionalProperties': False,
                     'properties': {'annee': YEAR, 'mois': MONTH, 'compte': ACCOUNT,
                                    'statut': {'type': 'string', 'enum': ['a_classer', 'classee']},
                                    'categorie': {'type': 'string', 'maxLength': 40}, 'inclure_texte_autrui': regles.INCLURE}}},
    {'name': 'banque_resume', 'title': 'Résumé bancaire',
     'description': "Entrées et sorties d'une année (ou d'un mois) par catégorie, hors virements internes et dépôts du dojo déjà comptés; "
                    "montant encore à classer. Pas un conseil financier.",
     'inputSchema': {'type': 'object', 'additionalProperties': False, 'required': ['annee'],
                     'properties': {'annee': YEAR, 'mois': MONTH, 'compte': ACCOUNT}}},
    {'name': 'releves_manquants', 'title': 'Relevés manquants',
     'description': "Pour chaque compte bancaire, carte ou marge lié (Dojo, Perso) : les périodes sans relevé depuis le 1er janvier "
                    "2026, les ruptures de solde entre deux relevés, et un relevé en retard. Dates et aperçus masqués seulement. "
                    "Dis à Ugo quels relevés déposer.",
     'inputSchema': {'type': 'object', 'additionalProperties': False, 'properties': {'compte': ACCOUNT}}},
    {'name': 'factures_a_classer', 'title': 'Factures à classer',
     'description': f"Factures (photos, PDF) déposées dans « {factures.INBOX} » de Google Drive, pas encore liées à une dépense : "
                    "nom, taille, date de dépôt, plus récentes d'abord.",
     'inputSchema': {'type': 'object', 'additionalProperties': False, 'properties': {}}},
    {'name': 'facture_voir', 'title': 'Voir une facture',
     'description': "Montre l'image d'une facture (première page d'un PDF) pour en lire la date, le montant et le fournisseur : "
                    "nom = facture à classer, ou depense = numéro d'une dépense qui a sa facture. Texte du fournisseur : texte_externe.",
     'inputSchema': {'type': 'object', 'additionalProperties': False,
                     'properties': {'nom': {'type': 'string', 'maxLength': 120}, 'depense': EXPENSE_ID}}},
    {'name': 'points_japon', 'title': 'Points pour le Japon',
     'description': "Points Membership Rewards de la Cobalt et avancement vers l'objectif du voyage au Japon : solde du dernier "
                    "relevé, écart, rythme de points gagnés sur les 6 derniers relevés, date estimée; historique=true pour le "
                    "détail relevé par relevé. Totaux de points seulement, sans fenêtre. Ne jamais deviner un mois manquant ni "
                    "le coût d'un vol.",
     'inputSchema': {'type': 'object', 'additionalProperties': False, 'properties': {'historique': {'type': 'boolean'}}}},
    {'name': 'budget_etat', 'title': 'Budget familial',
     'description': "État du budget familial du compte Perso : par catégorie budgétée, dépensé, budget, écart et pourcentage, "
                    "pour un mois (mois) ou depuis janvier (sans mois); dépenses sans budget; avertissements (à classer, relevés "
                    "manquants, mois en cours); historique=true pour les versions du budget de l'année. "
                    "Chiffres seulement : aucun conseil, aucun budget proposé.",
     'inputSchema': {'type': 'object', 'additionalProperties': False, 'required': ['annee'],
                     'properties': {'annee': {'type': 'integer', 'minimum': 2026, 'maximum': 2100}, 'mois': MONTH,
                                    'historique': {'type': 'boolean'}}}},
    {'name': 'finances_historique', 'title': 'Historique Finances',
     'description': "Dernières modifications des finances (journal d'audit, date UTC) : action et numéro, sans détails.",
     'inputSchema': {'type': 'object', 'additionalProperties': False,
                     'properties': {'nombre': {'type': 'integer', 'minimum': 1, 'maximum': 100}}}},
]
for tool in TOOLS:
    tool['annotations'] = {'title': tool['title'], **READ_ONLY}
# The printable report writes a file on the Mac (never the base): an uppercase tool, asked every time.
for tool in TOOLS:
    if tool['name'].isupper():
        tool['annotations'] = {**tool['annotations'], 'readOnlyHint': False, 'destructiveHint': False}

HANDLERS = {'finances_reference': reference, 'finances_depenses': expenses, 'finances_revenus': incomes, 'finances_rapport': report,
            'WR_SAVE_RAPPORT_FICHIER': printable_report, 'finances_historique': history,
            'factures_a_classer': receipts_waiting, 'facture_voir': receipt_view,
            'banque_a_classer': to_sort, 'banque_operations': bank_operations, 'banque_resume': bank_overview,
            'releves_manquants': statements_missing, 'points_japon': japan_points, 'budget_etat': budget_state}


class FinancesReads:
    tools, handlers, instructions = TOOLS, HANDLERS, READ_INSTRUCTIONS


def finances_lot(store):
    return Lot(store, tuple(OPERATIONS), catalog=OPERATIONS, variable=VARIABLE, prefix='finances_lot',
               confirm='WR_SAVE_FINANCES_BD', base='Finances', instructions=WRITE_INSTRUCTIONS)
