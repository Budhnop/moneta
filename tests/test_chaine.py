"""Chaîne des relevés (spec 003, FR-017) : trous, ruptures de solde et retard depuis le 1er janvier 2026, sans aucun montant."""
from datetime import date
import json
import unittest

from moneta.finances.releves.chaine import chain, short

TODAY = date(2026, 5, 10)


class TestChaine(unittest.TestCase):
    def test_a_missing_month_is_a_hole_and_disappears_once_imported(self):
        full = [('2026-01-01', '2026-01-31', 0, 10), ('2026-02-01', '2026-02-28', 10, 20), ('2026-03-01', '2026-03-31', 20, 30),
                ('2026-04-01', '2026-04-30', 30, 40)]
        gap = [full[0], full[1], full[3]]
        self.assertEqual(chain(gap, TODAY)['trous'], [{'du': '2026-03-01', 'au': '2026-03-31'}])
        complete = chain(full, TODAY)
        self.assertEqual((complete['premier'], complete['dernier']), ('2026-01-01', '2026-04-30'))
        self.assertEqual((complete['trous'], complete['ruptures']), ([], []))
        self.assertNotIn('en_retard_depuis', complete)

    def test_a_first_statement_in_june_leaves_january_to_may_missing(self):
        found = chain([('2026-06-01', '2026-06-30', 0, 5)], date(2026, 7, 2))
        self.assertEqual(found['trous'], [{'du': '2026-01-01', 'au': '2026-05-31'}])

    def test_a_balance_that_does_not_follow_is_a_break(self):
        rows = [('2026-01-01', '2026-01-31', 0, 10), ('2026-02-01', '2026-02-28', 11, 20)]
        self.assertEqual(chain(rows, TODAY)['ruptures'], [{'fin': '2026-01-31', 'debut': '2026-02-01'}])

    def test_card_cycles_that_do_not_follow_the_calendar(self):
        rows = [('2026-01-01', '2026-01-14', 0, -5), ('2026-01-15', '2026-02-14', -5, -9), ('2026-02-15', '2026-03-14', -9, -1),
                ('2026-04-15', '2026-05-14', -3, -2)]
        found = chain(rows, date(2026, 5, 20))
        self.assertEqual(found['trous'], [{'du': '2026-03-15', 'au': '2026-04-14'}])
        self.assertEqual(found['ruptures'], [])

    def test_an_ofx_period_counts_as_covered_without_a_break_at_its_edges(self):
        rows = [('2026-01-01', '2026-01-31', 0, 10), ('2026-02-03', '2026-02-26', None, None), ('2026-03-01', '2026-03-31', 99, 100)]
        found = chain(rows, date(2026, 4, 2))
        # The OFX operations run from the 3rd to the 26th: the days around them are not covered.
        self.assertEqual(found['trous'], [{'du': '2026-02-01', 'au': '2026-02-02'}, {'du': '2026-02-27', 'au': '2026-02-28'}])
        self.assertEqual(found['ruptures'], [])

    def test_late_when_nothing_covers_the_last_five_weeks(self):
        rows = [('2026-01-01', '2026-01-31', 0, 10)]
        self.assertEqual(chain(rows, date(2026, 3, 9))['en_retard_depuis'], '2026-02-01')
        self.assertNotIn('en_retard_depuis', chain(rows, date(2026, 3, 8)))
        self.assertEqual(chain([], date(2026, 1, 10)), {'premier': None, 'dernier': None, 'trous': [], 'ruptures': []})

    def test_no_amount_ever_leaves(self):
        rows = [('2026-01-01', '2026-01-31', 123_456, 654_321), ('2026-02-01', '2026-02-28', 777_777, 888_888)]
        text = json.dumps(chain(rows, TODAY))
        for amount in ('123456', '654321', '777777', '888888'):
            self.assertNotIn(amount, text)

    def test_debut_moves_the_start_of_the_chain(self):
        # Spec 005: the points series starts with the first Cobalt statement, not on January 1st.
        rows = [('2026-05-01', '2026-05-31', 0, 10), ('2026-07-01', '2026-07-31', 10, 20)]
        found = chain(rows, date(2026, 8, 2), debut='2026-05-01')
        self.assertEqual(found['trous'], [{'du': '2026-06-01', 'au': '2026-06-30'}])
        self.assertEqual(chain(rows, date(2026, 8, 2))['trous'][0], {'du': '2026-01-01', 'au': '2026-04-30'})

    def test_short_keeps_the_recap_small(self):
        found = {'trous': [{'du': f'2026-{m:02d}-01', 'au': f'2026-{m:02d}-28'} for m in range(1, 10)],
                 'ruptures': [{'fin': '2026-10-31', 'debut': '2026-11-01'}]}
        self.assertEqual(short(found)[-1], 'et 4 autres')
        self.assertEqual(len(short(found)), 7)


if __name__ == '__main__':
    unittest.main()
