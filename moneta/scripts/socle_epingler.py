"""moneta-socle-epingler <tag> (repris de Smith, spec 010 et 013) : le seul écrivain de socle/ et de socle.epingle.json, à la racine du dépôt.

moneta-socle-epingler --branche X.Y : la tête d'une branche X.Y du socle, avant son tag, pour le travail d'une spec;
l'épingle a la même forme et passe au tag avant la mise en service (le vérificateur ne connaît que les tags).

Lit l'arbre src/socle du tag avec git archive, met la copie en place par renommage, écrit l'épingle et affiche ce qui change.
Ne committe pas et ne pousse pas. Le dépôt est ~/_PersonalAgents/socle.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

# À la racine, à côté du paquet moneta : le socle s'importe sous son nom, sans que moneta/ soit dans le chemin d'import.
CODE = Path(__file__).resolve().parents[2]
DEPOT = Path.home() / '_PersonalAgents' / 'socle'
TAG = re.compile(r'^v(\d+\.\d+\.\d+)$')
BRANCHE = re.compile(r'^\d+\.\d+$')
ARBRE = 'src/socle'


class Refus(Exception):
    pass


def git(depot, *commande, texte=True):
    return subprocess.run(['/usr/bin/git', '-C', str(depot), *commande], capture_output=True, text=texte)


def extraire(depot, commit, cible):
    archive = git(depot, 'archive', '--format=tar', commit, ARBRE, texte=False)
    if archive.returncode != 0:
        raise Refus(f'Arbre {ARBRE} illisible au commit {commit}.')
    prefixe = ARBRE + '/'
    with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tar:
        for membre in tar.getmembers():
            if not membre.name.startswith(prefixe):
                continue
            relatif = membre.name[len(prefixe):]
            if membre.isdir() or not relatif:
                continue
            if not membre.isfile() or '..' in relatif.split('/'):
                raise Refus(f'Entrée refusée dans le tag : {membre.name}')
            chemin = cible / relatif
            chemin.parent.mkdir(parents=True, exist_ok=True)
            chemin.write_bytes(tar.extractfile(membre).read())


def empreintes(copie):
    """Computed here, never by the tag's own module: nothing from the tag is executed before its diff is read."""
    return {p.relative_to(copie).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(copie.rglob('*')) if p.is_file()}


def epingler(tag, depot=DEPOT, code=CODE, branche=False):
    depot, code = Path(depot), Path(code)
    forme = (BRANCHE if branche else TAG).match(tag)
    if not forme:
        raise Refus(f'Branche de la forme X.Y attendue : {tag}' if branche else f'Tag de la forme vX.Y.Z attendu : {tag}')
    if not depot.is_dir() or git(depot, 'rev-parse', '--git-dir').returncode != 0:
        raise Refus(f'Dépôt du socle absent : {depot}')
    commit = git(depot, 'rev-parse', '--verify', '--quiet', f'refs/{"heads" if branche else "tags"}/{tag}^{{commit}}')
    if commit.returncode != 0:
        raise Refus(f'Branche inconnue : {tag}' if branche else f'Tag inconnu : {tag}')
    copie, chemin_epingle = code / 'socle', code / 'socle.epingle.json'
    with tempfile.TemporaryDirectory(prefix='.socle-', dir=code) as temp:
        nouvelle = Path(temp) / 'socle'
        nouvelle.mkdir()
        extraire(depot, commit.stdout.strip(), nouvelle)
        init = nouvelle / '__init__.py'
        trouvee = re.search(r'''^__version__\s*=\s*["']([^"']*)["']''', init.read_text(encoding='utf-8'), re.M) if init.is_file() else None
        if branche:
            # Une version X.Y.Z de cette branche, jamais une autre.
            if not trouvee or not re.fullmatch(re.escape(tag) + r'\.\d+', trouvee.group(1)):
                raise Refus(f"__version__ ({trouvee.group(1) if trouvee else 'absente'}) n'est pas de la branche {tag}.")
        elif not trouvee or trouvee.group(1) != forme.group(1):
            raise Refus(f"__version__ ({trouvee.group(1) if trouvee else 'absente'}) diffère du tag {tag}.")
        fichiers = empreintes(nouvelle)
        avant = {}
        if chemin_epingle.is_file():
            try:
                avant = json.loads(chemin_epingle.read_text(encoding='utf-8')).get('fichiers', {})
            except ValueError:
                pass
        contenu = {'version': trouvee.group(1), 'commit': commit.stdout.strip(), 'fichiers': fichiers}
        partiel = Path(temp) / 'epingle.json'
        partiel.write_text(json.dumps(contenu, indent=1, ensure_ascii=False) + '\n', encoding='utf-8')
        # Hors du dossier temporaire : si le rétablissement échoue, l'ancienne copie n'est pas effacée avec lui.
        garde = Path(tempfile.mkdtemp(prefix='.socle-garde-', dir=code))
        ancienne = garde / 'socle'
        avait = copie.exists()
        try:
            if avait:
                copie.rename(ancienne)
            nouvelle.rename(copie)
            partiel.replace(chemin_epingle)
        except BaseException:
            try:
                if not avait or ancienne.exists():
                    if copie.exists():
                        shutil.rmtree(copie)
                    if avait:
                        ancienne.rename(copie)
            except OSError:
                raise Refus(f'Rétablissement impossible; ancienne copie gardée dans {garde}') from None
            shutil.rmtree(garde)
            raise
        shutil.rmtree(garde)
    changes = [f'{n} (ajouté)' for n in fichiers if n not in avant] + [f'{n} (retiré)' for n in avant if n not in fichiers] \
        + [f'{n} (changé)' for n in fichiers if n in avant and avant[n] != fichiers[n]]
    return contenu, sorted(changes)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('tag')
    parser.add_argument('--depot', default=str(DEPOT))
    parser.add_argument('--branche', action='store_true', help='tag est une branche X.Y du socle, avant son tag (spec 013)')
    arguments = parser.parse_args(argv)
    try:
        contenu, changes = epingler(arguments.tag, arguments.depot, branche=arguments.branche)
    except (Refus, OSError, ValueError) as exc:
        print(f'Refusé : {exc}', file=sys.stderr)
        return 1
    print(f"Socle {contenu['version']} ({contenu['commit'][:7]}), {len(contenu['fichiers'])} fichiers épinglés.")
    for ligne in changes:
        print(f'  {ligne}')
    print('Rien de commité : relis le diff, puis committe dans Moneta.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
