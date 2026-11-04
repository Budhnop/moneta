"""moneta-installer --racine DIR [--ecosysteme] : dit à Moneta où vivent ses vraies données (spec 017).

Écrit installation.toml à la racine du code (600, hors Git) et prépare DIR/moneta (700, marqué réel) par socle.installation.
Avec --ecosysteme, DIR doit déjà porter un ecosysteme.toml qui inscrit moneta; sans, DIR ne doit pas en porter. Refuse de
réécrire une installation. Ne crée aucune base : les migrations s'appliquent ensuite (bin/moneta-migrer --appliquer).
"""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from socle import installation  # noqa: E402

CODE = Path(__file__).resolve().parents[2]


def main(argv=None):
    parser = argparse.ArgumentParser(description='Installe Moneta : où vivent ses vraies données.')
    parser.add_argument('--racine', required=True, help="le dossier des données de l'installation (hors du dépôt, hors d'un nuage)")
    parser.add_argument('--ecosysteme', action='store_true', help="Moneta fait partie d'un écosystème (ecosysteme.toml dans la racine)")
    args = parser.parse_args(argv)
    try:
        installation.installer(CODE, 'moneta', args.racine, args.ecosysteme)
    except (installation.Refus, OSError) as exc:
        print(f'Refusé, rien n’est changé : {exc}', file=sys.stderr)
        return 1
    reel = installation.dossier_reel(installation.lire(CODE), 'moneta')
    print(f'Moneta installé : données réelles dans {reel}.')
    print('Prochain pas : bin/moneta-migrer --appliquer, puis bin/moneta-verifier.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
