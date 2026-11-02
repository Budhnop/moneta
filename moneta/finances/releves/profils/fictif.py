"""Profil des relevés fictifs des tests (tests/releves_fictifs.py) : une mise en page inventée, « RELEVÉ FICTIF … », qu'aucune
banque n'imprime. Le registre ne l'ajoute que sur des données fictives (confirmation.fictitious()) : il permet aux tests de
suivre tout le parcours, sous-processus compris, et n'existe pas pour les vraies données.
"""


class ProfilFictif:
    """A profile in the shape of contracts/profil-releve.md, for the fictitious layout of tests/releves_fictifs.py."""
    def __init__(self, source, marker):
        self.SOURCE, self.marker = source, marker

    def reconnait(self, pages):
        return any(line['texte'] == f'RELEVÉ FICTIF {self.marker}' for line in pages[0]['lignes'])

    @staticmethod
    def cents(text):
        whole, part = text.replace(' ', ' ').replace(' ', '').split(',')
        return int(whole) * 100 + int(part)

    def lire(self, pages):
        rows = []
        for page in pages:
            current, last = None, None
            for line in page['lignes']:
                if last is None or abs(line['y'] - last) > 4:
                    current = {}
                    rows.append(current)
                current[line['x'] // 10 * 10] = line['texte']
                last = line['y']
        data = {'source': self.SOURCE, 'comptes': [], 'points': None}
        compte = lines = None
        for row in rows:
            first = row.get(40, '')
            if first.startswith('Période du '):
                _, _, data['du'], _, data['au'] = first.split(' ')
            elif first.startswith('Compte '):
                compte = {'numero': first[7:], 'type': row[300][5:], 'lignes': [], 'additionnelles': []}
                data['comptes'].append(compte)
                lines = compte['lignes']
            elif first.startswith('Solde'):
                value = self.cents(row[520]) * (-1 if compte['type'] != 'banque' else 1)
                compte['solde_debut_cents' if 'départ' in first else 'solde_fin_cents'] = value
            elif first == 'Points':
                values = [int(row[x]) for x in (200, 280, 360, 440)]
                data['points'] = dict(zip(('precedent', 'gagnes', 'utilises', 'nouveau'), values))
            elif first == 'Titulaire additionnel':
                # The card number only: the name printed beside it is never read (spec 004, FR-005).
                section = {'numero': row[400][6:], 'lignes': []}
                compte['additionnelles'].append(section)
                lines = section['lignes']
            elif len(first) == 10 and first[4] == '-':
                amount = -self.cents(row[400]) if 400 in row else self.cents(row[480])
                lines.append({'date': first, 'description': row.get(120, ''), 'montant_cents': amount,
                                         'genre': row.get(330, 'operation')})
        return data


FOLIO = ProfilFictif('folio_desjardins', 'FOLIO EXEMPLE')
COBALT = ProfilFictif('cobalt', 'CARTE EXEMPLE')
MARGE = ProfilFictif('marge_desjardins', 'MARGE EXEMPLE')
