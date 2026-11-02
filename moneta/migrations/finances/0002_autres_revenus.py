"""Autres revenus : ventes et paiements qui ne passent pas par les inscriptions du Dojo (équipement, kimonos, guides, passages,
abonnements saisis hors Agent Smith). Le rapport annuel les additionne aux paiements de dojo.db."""


def deja_appliquee(con):
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='revenus'").fetchone() is not None


def appliquer(con):
    con.execute("""CREATE TABLE revenus (
    id INTEGER PRIMARY KEY,
    compte_id INTEGER NOT NULL REFERENCES comptes(id),
    date TEXT NOT NULL CHECK (date GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]'),
    montant_cents INTEGER NOT NULL CHECK (montant_cents > 0),
    type TEXT NOT NULL CHECK (type IN ('abonnement', 'equipement', 'kimono', 'guide', 'passage', 'autre')),
    mode TEXT CHECK (mode IS NULL OR mode IN ('comptant', 'carte', 'virement', 'cheque')),
    description TEXT,
    annule INTEGER NOT NULL DEFAULT 0 CHECK (annule IN (0, 1)),
    cree_le TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)""")
    con.execute("CREATE INDEX idx_revenus_date ON revenus(date)")
