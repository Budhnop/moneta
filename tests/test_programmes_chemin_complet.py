"""Le code Python appelle les programmes système par leur chemin complet, comme les lanceurs (revue C13 de Smith, test_lanceurs) : un
programme du même nom placé plus tôt dans le PATH ne s'exécute jamais. Repris de Smith (spec 013, T073), où le vérificateur lançait « git » nu."""
import ast
from pathlib import Path
import unittest

ASSISTANT = Path(__file__).resolve().parents[1] / "moneta"
APPELS = ('run', 'Popen', 'check_output', 'check_call', 'call')


def nus():
    """[(fichier:ligne, programme)] de chaque liste de commande littérale qui ne commence pas par un chemin absolu."""
    trouves = []
    for fichier in sorted(ASSISTANT.rglob('*.py')):
        if {'__pycache__'} & set(fichier.relative_to(ASSISTANT).parts):
            continue
        for noeud in ast.walk(ast.parse(fichier.read_text(encoding='utf-8'))):
            if isinstance(noeud, ast.Call) and getattr(noeud.func, 'attr', '') in APPELS and noeud.args \
                    and isinstance(noeud.args[0], ast.List) and noeud.args[0].elts:
                tete = noeud.args[0].elts[0]
                if isinstance(tete, ast.Constant) and isinstance(tete.value, str) and not tete.value.startswith('/'):
                    trouves.append((f'{fichier.relative_to(ASSISTANT)}:{noeud.lineno}', tete.value))
    return trouves


class TestCheminComplet(unittest.TestCase):
    def test_aucun_programme_nu(self):
        self.assertEqual(nus(), [])


if __name__ == '__main__':
    unittest.main()
