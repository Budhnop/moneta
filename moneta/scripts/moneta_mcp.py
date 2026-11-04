"""Entrée MCP de Moneta pour Claude Desktop et l'app (spec 013). Les lectures passent par une copie privée vérifiée; un lot
confirmé atteint la base après une sauvegarde, tout ou rien."""
import argparse
import os
from pathlib import Path
import signal
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from moneta.agent.droits import REGISTRE, verifier  # noqa: E402
from moneta.chemins import PAQUET, backups, environnement_reel, finances_db, home  # noqa: E402
from moneta.outils.lot import SourceStore  # noqa: E402
from moneta.outils.mcp_finances import VARIABLE, FinancesReads, finances_lot  # noqa: E402
from moneta.outils.serveur import McpServer  # noqa: E402
from socle import confirmation, installation  # noqa: E402
from socle.copie_privee import CopiePrivee  # noqa: E402
from socle.migrations import migrate, pending  # noqa: E402

MIGRATIONS = PAQUET / "migrations" / "finances"
FICTIF_PREFIXE = "moneta-mcp-fictif-"
PAIEMENTS_FICTIFS = PAQUET / "scripts" / "paiements_fictifs.py"


def log(text):
    # stdout carries JSON-RPC only.
    print(text, file=sys.stderr, flush=True)


def serveur_fictif(folder, ecriture=True, registre=None):
    """Server on a fictitious base inside folder: every default place (exports, backups, journals, Drive) inside it too."""
    os.environ["MONETA_HOME"] = folder
    try:
        suivi = home() == Path(folder)
    except installation.EtatRefuse:  # not installed: no real folder to fall back on
        suivi = False
    if not suivi:
        # A temporary folder inside a real one (TMPDIR not writable: Python falls back to the current folder) is never
        # followed, so every default place would be the real one: stop before anything is written.
        log("Moneta MCP --fictif : le dossier temporaire est dans un dossier réel; arrêt.")
        sys.exit(2)
    (Path(folder) / "drive").mkdir(exist_ok=True)
    os.environ["MONETA_DRIVE_DOSSIER"] = str(Path(folder) / "drive")
    # Never Smith's real program: it would read the real Dojo base. The fictitious world has no payment.
    os.environ["MONETA_LECTURE_PAIEMENTS"] = str(PAIEMENTS_FICTIFS)
    ledger = Path(folder) / "finances.db"
    migrate(ledger, Path(folder) / "backups", MIGRATIONS)
    ledger.chmod(0o600)
    os.environ[VARIABLE] = str(ledger)
    modules = [FinancesReads()] + ([finances_lot(SourceStore(ledger, Path(folder) / "backups", variable=VARIABLE))] if ecriture else [])
    log("Moneta MCP : base fictive temporaire; " + ("avec écritures" if ecriture else "en lecture") + ".")
    return McpServer(modules=modules, registre=registre)


def mode_app(server, registre):
    """--app: the registry must own every tool this process loaded; a tool declared but not loaded is only unavailable."""
    problems = verifier(registre, server.tools, server.lots(), complet=False)
    if problems:
        log("Moneta MCP --app : registre incomplet; arrêt. " + " ".join(problems))
        sys.exit(2)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Serveur MCP de Moneta.")
    parser.add_argument("--fictif", action="store_true", help="Base synthétique temporaire au lieu d'une copie de production.")
    parser.add_argument("--ecriture", action="store_true", help="Écritures : le lot Finances et le rapport.")
    parser.add_argument("--app", action="store_true", help="Instance de l'app Smith : registre, sources et appels par agent (spec 006).")
    args = parser.parse_args(argv)
    registre = REGISTRE if args.app else None
    # SIGHUP too: closing the app's terminal must still run every finally (private copy removed).
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    signal.signal(signal.SIGHUP, lambda *_: sys.exit(0))
    # Real data: every write waits for Ugo's click on the Mac, whatever the environment says; fictitious data needs none.
    confirmation.AUTOMATIC = args.fictif
    if args.fictif:
        with tempfile.TemporaryDirectory(prefix=FICTIF_PREFIXE) as folder:
            server = serveur_fictif(folder, args.ecriture, registre)
            if registre is not None:
                mode_app(server, registre)
            server.serve()
        return
    environnement_reel()
    ledger = finances_db()
    if not ledger.is_file() or pending(ledger, MIGRATIONS):
        log("Moneta MCP : base absente ou migrations en attente; lancer bin/moneta-migrer --appliquer.")
        sys.exit(1)
    books = CopiePrivee(ledger, backups() / "database" / "mcp-lecture", VARIABLE)
    books.purge_stale()
    try:
        books.refresh()
        modules = [FinancesReads()]
        if args.ecriture:
            modules.append(finances_lot(SourceStore(books.source, backups() / "database" / "moneta", after=books.refresh,
                                                    variable=VARIABLE)))
        log("Moneta MCP : copie privée vérifiée; " + ("avec écritures" if args.ecriture else "en lecture") + ".")
        server = McpServer(before_call=books.refresh, modules=modules, registre=registre)
        if registre is not None:
            mode_app(server, registre)
        server.serve()
    finally:
        books.purge()


if __name__ == "__main__":
    main()
