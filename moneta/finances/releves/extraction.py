"""Texte d'un relevé PDF lu sur le Mac par moneta-releve (PDFKit; bin/moneta-construire-releve), sans service extérieur.

Le programme reçoit une copie privée des octets déjà lus et vérifiés par moneta.finances.banque (l'empreinte porte donc sur ce qui
est lu). Il ne rend que des segments de texte positionnés; leur lecture appartient aux profils. Sa réponse est vérifiée ici
avant d'aller plus loin.
"""
import json
from pathlib import Path
import subprocess
import tempfile

from moneta.chemins import releve
from moneta.finances.banque import RefusBanque

TIMEOUT = 12  # inside the 15 s the finances command has (moneta/outils/executeur.py)
MAX_PAGES = 50
MAX_SEGMENTS = 100_000
MAX_TEXT = 500
MAX_OUTPUT = 2_000_000


def program():
    return releve()


def _number(value):
    return type(value) is int and 0 <= value <= 20_000


def check(reply):
    """[{numero, largeur, hauteur, lignes: [{x, y, texte}]}] from the program's reply, or RefusBanque."""
    if not isinstance(reply, dict):
        raise RefusBanque('Lecture du PDF : réponse illisible du programme.')
    if 'erreur' in reply:
        raise RefusBanque(f"PDF refusé : {str(reply['erreur'])[:120]}.")
    pages = reply.get('pages')
    if not isinstance(pages, list) or not 1 <= len(pages) <= MAX_PAGES:
        raise RefusBanque('Lecture du PDF : nombre de pages invalide.')
    count = 0
    out = []
    for number, page in enumerate(pages, 1):
        if not isinstance(page, dict) or page.get('numero') != number or not _number(page.get('largeur')) \
                or not _number(page.get('hauteur')) or not isinstance(page.get('lignes'), list):
            raise RefusBanque('Lecture du PDF : page invalide.')
        lines = []
        for line in page['lignes']:
            if not isinstance(line, dict) or not _number(line.get('x')) or not _number(line.get('y')) \
                    or not isinstance(line.get('texte'), str) or not 0 < len(line['texte']) <= MAX_TEXT:
                raise RefusBanque('Lecture du PDF : texte invalide.')
            lines.append({'x': line['x'], 'y': line['y'], 'texte': line['texte']})
        count += len(lines)
        if count > MAX_SEGMENTS:
            raise RefusBanque('Relevé trop long.')
        out.append({'numero': number, 'largeur': page['largeur'], 'hauteur': page['hauteur'], 'lignes': lines})
    return out


def pages(raw):
    """Positioned text of the PDF bytes raw, read by moneta-releve from a private temporary copy."""
    helper = program()
    if not helper.is_file():
        raise RefusBanque('Lecteur de relevés PDF absent sur le Mac : lancer bin/moneta-construire-releve.')
    if not raw.startswith(b'%PDF-'):
        raise RefusBanque("Ce fichier n'est pas un PDF.")
    with tempfile.TemporaryDirectory(prefix='moneta-releve-') as folder:
        copy = Path(folder) / 'releve.pdf'
        copy.write_bytes(raw)
        copy.chmod(0o600)
        try:
            done = subprocess.run([str(helper), str(copy)], capture_output=True, timeout=TIMEOUT)
        except subprocess.TimeoutExpired:
            raise RefusBanque(f'Lecture du PDF trop longue (plus de {TIMEOUT} s).') from None
    if len(done.stdout) > MAX_OUTPUT + 1000:
        raise RefusBanque('Relevé trop long (texte de plus de 2 Mo).')
    try:
        reply = json.loads(done.stdout.decode('utf-8').strip().split('\n')[-1])  # not splitlines: U+2028 is text
    except (IndexError, ValueError, UnicodeDecodeError):
        raise RefusBanque('Lecture du PDF : réponse illisible du programme.') from None
    if done.returncode and 'erreur' not in reply:
        raise RefusBanque('Lecture du PDF : échec du programme.')
    return check(reply)
