#!/usr/bin/env python3
"""Un smith-lecture-paiements de test pour les captures de la spec 011 (tests/references/cas_paiements) : il lit le monde fictif
du cas (UGOD_DB) par le code de la spec 011 de Smith, agents.dojo.paiements, lancé par le Python de Smith (MONETA_TESTS_SMITH),
sans la copie vérifiée du vrai programme. Les mondes de ces captures ont des données anciennes atypiques (modes inconnus, famille
orpheline, colonne mode absente) que le vrai programme refuse, à raison; ici elles sont lues comme sur 3351db7.
Même sortie que le vrai programme (contrat lecture-paiements-moneta); une erreur de lecture : code 1, rien sur stdout."""
import os
from pathlib import Path
import subprocess
import sys

SMITH = Path(os.environ.get('MONETA_TESTS_SMITH') or Path.home() / '_PersonalAgents' / 'smith').expanduser()
CODE = ('import json, sqlite3, sys\n'
        'from agents.dojo import paiements\n'
        'commande, annee = sys.argv[1], int(sys.argv[2])\n'
        'try:\n'
        ' if commande == "totaux":\n'
        '  montants, nombres = paiements.totaux_annee(annee)\n'
        '  valeur = {"montants": montants, "nombres": nombres}\n'
        ' else:\n'
        '  valeur = {"lignes": [list(l) for l in paiements.lignes_annee(annee)]}\n'
        'except sqlite3.Error as exc:\n'
        ' sys.exit(f"Lecture des paiements refusée : {type(exc).__name__}.")\n'
        'sys.stdout.write(json.dumps({"version": 1, "commande": commande, "annee": annee, **valeur}, ensure_ascii=False) + "\\n")\n')

if len(sys.argv) != 3 or sys.argv[1] not in ('totaux', 'lignes') or not sys.argv[2].isdigit():
    sys.exit(2)
env = {k: v for k, v in os.environ.items() if not k.startswith('MONETA_')}
env.update(PYTHONPATH=str(SMITH / 'assistant'), PYTHONDONTWRITEBYTECODE='1')
fait = subprocess.run([str(SMITH / '.venv' / 'bin' / 'python'), '-c', CODE, *sys.argv[1:]], cwd=SMITH / 'assistant', env=env,
                      capture_output=True, text=True, timeout=60)
sys.stdout.write(fait.stdout)
sys.stderr.write(fait.stderr[-500:])
sys.exit(fait.returncode)
