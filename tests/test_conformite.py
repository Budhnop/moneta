"""La suite de conformité du socle, à la version épinglée de Moneta (spec 014 de Smith, EF-008) : les règles de D107 en tests."""
from pathlib import Path
import unittest

from socle.conformite import cas

TestConformite = cas(Path(__file__).resolve().parents[1], 'moneta')


if __name__ == '__main__':
    unittest.main()
