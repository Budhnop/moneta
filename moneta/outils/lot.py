"""Le lot Finances de Moneta autour du moteur du socle (socle.lots.MoteurLot, spec 013), et la base qu'il écrit.

Repris de core/services/mcp_writes.py et de scripts/smith_mcp.py de Smith f9c1599 : comment une étape se lance, comment les
factures suivent la transaction. Les outils du lot et le tout ou rien sont au socle.
"""
from moneta.outils import factures
from moneta.outils.catalogue import validate_arguments
from moneta.outils.executeur import execute_safe
from socle.depot_sqlite import DepotSQLite, pointer_variable
from socle.lots import NOTHING, MoteurLot, ToolError, wall_clock

# Les mêmes que Smith : une étape dont la cible change pendant le lot est retirée (socle.lots).
TARGET_KEYS = ('session', 'famille', 'via_membre', 'membre', 'journee', 'nouveau_membre', 'desactiver', 'reactiver', 'retire')


class WithImages(dict):
    """A tool result that also shows images to the model: images = [(base64 JPEG)]. An image may carry someone else's text
    that no mark sees: it lights the signal, unless the tool vouches that it drew it from trusted parts only (F13, D091)."""
    images = ()
    images_de_confiance = False


def succeeded(reply):
    """The Finances writer exits 0 even after printing ERREUR: success is its explicit "OK"."""
    output = (reply.get('result') or '').strip()
    if not reply.get('ok'):
        return False, output or reply.get('error') or ''
    return output.startswith('OK'), output


def run(step):
    return succeeded(execute_safe(step.action, step.arguments, timeout=30))


class Lot(MoteurLot):
    """The socle's batch engine and its MCP tools, with Moneta's operations and how a step runs. par_app is set by the server for
    the app's instance (--app): its writes log origine app (D098)."""

    def __init__(self, store, operations, clock=wall_clock, *, catalog, variable, prefix, confirm, base, instructions):
        super().__init__(store, operations, clock, catalog=catalog, prefix=prefix, confirm=confirm, base_name=base,
                         executer=run, valider=lambda step: validate_arguments(step.action, step.arguments),
                         pointer=lambda copy: pointer_variable(copy, variable), cles_cibles=TARGET_KEYS,
                         doublon=f"Cette facture est déjà dans le lot; {NOTHING}.", instructions=instructions)
        self.variable = variable


class SourceStore(DepotSQLite):
    """finances.db: the all-or-nothing commit is the socle's; running a step and moving the receipts are Moneta's."""

    def __init__(self, source, backups, after=None, variable='MONETA_FINANCES_DB'):
        super().__init__(source, backups, after, variable, executer=run, deplacer=self.move, annuler=self.undo)

    @staticmethod
    def move(steps):
        # Receipts move last, still under the lock: a failed move rolls the base back, a failed COMMIT moves them back.
        return factures.move_all([move for step in steps for move in getattr(step, "files", ())])

    @staticmethod
    def undo(moved):
        stuck = factures.undo(moved)
        if stuck:
            raise ToolError("Enregistrement refusé, mais des factures n'ont pas pu revenir dans « à classer » : "
                            + ", ".join(f"{m['source']} -> {m['destination']}" for m in stuck)) from None
