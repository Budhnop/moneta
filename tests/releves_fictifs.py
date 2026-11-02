"""Relevés fictifs pour les tests : un PDF minimal écrit en Python (texte Helvetica à des positions exactes), sans aucun vrai
relevé. Noms et numéros inventés seulement."""


def _escape(text):
    raw = text.encode('cp1252')
    return raw.replace(b'\\', b'\\\\').replace(b'(', b'\\(').replace(b')', b'\\)')


def pdf(pages, width=612, height=792):
    """PDF bytes. pages: one list per page of (x, y_from_top, text); an empty list is a page without text (a black square,
    like a scan)."""
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>', None,
               b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>']
    kids = []
    for lines in pages:
        if lines:
            body = b''.join(b'BT /F1 9 Tf %.1f %.1f Td (%s) Tj ET\n' % (x, height - y, _escape(text)) for x, y, text in lines)
        else:
            body = b'0 0 0 rg 100 100 200 200 re f\n'
        objects.append(b'<< /Length %d >>\nstream\n%sendstream' % (len(body), body))
        content = len(objects)
        objects.append(b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %d %d] /Resources << /Font << /F1 3 0 R >> >> '
                       b'/Contents %d 0 R >>' % (width, height, content))
        kids.append(len(objects))
    objects[1] = b'<< /Type /Pages /Kids [%s] /Count %d >>' % (b' '.join(b'%d 0 R' % k for k in kids), len(kids))
    out, offsets = bytearray(b'%PDF-1.4\n'), []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b'%d 0 obj\n%s\nendobj\n' % (number, body)
    xref = len(out)
    out += b'xref\n0 %d\n0000000000 65535 f \n' % (len(objects) + 1)
    out += b''.join(b'%010d 00000 n \n' % offset for offset in offsets)
    out += b'trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n' % (len(objects) + 1, xref)
    return bytes(out)


# Petit programme Swift pour les tests seulement : écrit une copie chiffrée d'un PDF (mot de passe d'ouverture).
CHIFFRER_SWIFT = '''
import Foundation
import PDFKit
let args = CommandLine.arguments
let document = PDFDocument(url: URL(fileURLWithPath: args[1]))!
let ok = document.write(to: URL(fileURLWithPath: args[2]),
                        withOptions: [.userPasswordOption: "fictif", .ownerPasswordOption: "fictif-proprio"])
exit(ok ? 0 : 1)
'''


# Mise en page fictive d'un relevé (aucune banque réelle) -------------------------------------------------------------
# Colonnes : date x=40, description x=120, genre x=330 (cartes et marge), retrait x=400, dépôt x=480, solde x=520.
ROWS_PER_PAGE = 45


def money(cents):
    """'1 234,56' : comme un relevé imprimé au Québec."""
    whole, part = divmod(abs(cents), 100)
    return f'{whole:,}'.replace(',', ' ') + f',{part:02d}'


ADDITIONAL_NAME = 'Exemple Zoé'  # printed in each additional section, never read by the profile (spec 004, FR-005)


def layout(marker, du, au, comptes, points=None, additionnelles=None):
    """Pages of (x, y, text). comptes: [{numero, type, debut, lignes: [(date, description, cents, genre)]}]; cents and balances
    from Ugo's point of view (a debt is negative; a card prints it as a positive « solde dû »). The closing balance is computed
    unless an account gives 'fin' (to fake a wrong one). additionnelles: [{numero, lignes}], the sections of additional cards,
    printed in the last account before its closing balance, which counts them (the whole statement, spec 004)."""
    rows = [[(40, f'RELEVÉ FICTIF {marker}')], [(40, f'Période du {du} au {au}')]]

    def line_rows(lignes, debt):
        for day, description, cents, genre in lignes:
            row = [(40, day), (120, description)]
            if debt:
                row.append((330, genre))
            row.append((400 if cents < 0 else 480, money(cents)))
            rows.append(row)
    for n, compte in enumerate(comptes, 1):
        debt = compte['type'] != 'banque'
        rows.append([(40, f"Compte {compte['numero']}"), (300, f"type {compte['type']}")])
        rows.append([(40, 'Solde dû au départ' if debt else 'Solde de départ'), (520, money(compte['debut']))])
        line_rows(compte['lignes'], debt)
        sections = (additionnelles or []) if n == len(comptes) else []
        for section in sections:
            rows.append([(40, 'Titulaire additionnel'), (200, ADDITIONAL_NAME), (400, f"carte {section['numero']}")])
            line_rows(section['lignes'], debt)
        everything = compte['lignes'] + [line for section in sections for line in section['lignes']]
        closing = compte.get('fin', compte['debut'] + sum(line[2] for line in everything))
        rows.append([(40, 'Solde dû à la fin' if debt else 'Solde de fin'), (520, money(closing))])
    if points:
        rows.append([(40, 'Points'), (200, str(points[0])), (280, str(points[1])), (360, str(points[2])), (440, str(points[3]))])
    pages = []
    for start in range(0, len(rows), ROWS_PER_PAGE):
        pages.append([(x, 60 + 15 * n, text) for n, row in enumerate(rows[start:start + ROWS_PER_PAGE]) for x, text in row])
    return pages


def as_extracted(pages):
    """What moneta-releve answers for these pages, without building it: segments in reading order."""
    return [{'numero': n, 'largeur': 612, 'hauteur': 792,
             'lignes': [{'x': x, 'y': y, 'texte': text} for x, y, text in sorted(page, key=lambda s: (s[1], s[0]))]}
            for n, page in enumerate(pages, 1)]


# The profile that reads this layout lives in finances/releves/profils/fictif.py (registered on fictitious data only).
from moneta.finances.releves.profils.fictif import COBALT, FOLIO, MARGE  # noqa: E402,F401
