"""The write batch engine (spec 010, contract moteur-lots): steps rehearsed on a private copy; one frozen, single-use code
commits them all after one backup. The agent gives its preparations, how a step runs and how a copy is pointed at; the course
of a batch, its delays and the order of a commit are here.

Order of a commit: code used up, even if refused after; checks; lock; decision judged again, never softer; intention in the
journal, no journal no write; for a window, lock released then window; store.commit; result in the journal.
"""
from contextlib import ExitStack
from dataclasses import dataclass, replace
from datetime import datetime
import json
import secrets
import tempfile
import time
from pathlib import Path
from typing import Callable

from socle import confirmation, journal_ecritures, politique, signal_texte
from socle.confirmation import TooLong, check, describe, one_line
from socle.declaration import DeclarationAbsente, actuelle

PROPOSAL_SECONDS = 600
READ_ONLY = {'readOnlyHint': True, 'destructiveHint': False, 'idempotentHint': True, 'openWorldHint': False}
# One server serves every conversation: a step older than this leaves the lot, so one slipped into another conversation never
# waits for a real lot (fresh-Claude review, P2-1, D052).
STEP_SECONDS = 30 * 60
SHOWN_AGE_SECONDS = 5 * 60
NOTHING = "rien n'est préparé"
WHY = 'Pourquoi cette fenêtre :'
BUSY = "Une autre écriture ou une lecture est en cours sur le Mac; rien n'est enregistré. Réessaie dans un instant."
MODES_NEUTRES = {'go': 'go : attends le oui explicite; aucune fenêtre.',
                 'fenetre': f"fenêtre : dis d'y cliquer Enregistrer ({confirmation.SECONDS} secondes)."}


class ToolError(Exception):
    pass


def wall_clock():
    """The real time, the signal's own (F12, D091): a step ages while the Mac sleeps, as the signal does."""
    return time.time()


def age_note(added, now):
    """« (ajoutée il y a 12 min) » for a step added SHOWN_AGE_SECONDS ago or more, else nothing."""
    minutes = int(now - added) // 60
    return f' (ajoutée il y a {minutes} min)' if now - added >= SHOWN_AGE_SECONDS else ''


def too_old(added, now):
    return now - added > STEP_SECONDS


def expired_reason():
    return f'ajoutée il y a plus de {STEP_SECONDS // 60} minutes; ajoute-la de nouveau si elle est toujours voulue'


def unknown_result(prefix):
    """An interrupted write: maybe written, maybe not. The app reads « Résultat inconnu » as such (review 008, P2-2)."""
    return ("⚠ Résultat inconnu : l'enregistrement s'est interrompu. Les changements ont peut-être été enregistrés. "
            f"Le lot est vidé pour éviter un doublon : vérifie d'abord (journal des écritures, {prefix}_voir), puis "
            "prépare seulement ce qui manque.")


def args(arguments, required, optional=()):
    if not isinstance(arguments, dict) or not set(required) <= set(arguments) or set(arguments) - set(required) - set(optional):
        raise ToolError('Arguments attendus : ' + ', '.join(required) + (' (optionnels : ' + ', '.join(optional) + ')' if optional else '') + '.')
    return arguments


def why_lines(decision):
    """The head of the Mac's window: why it opens, then a blank line before the steps; nothing on the go."""
    return [WHY, *politique.libelles(decision), ''] if decision.mode == 'fenetre' else []


def mode_fields(decision):
    return {'confirmation': decision.mode, 'raisons_fenetre': politique.libelles(decision)}


def mode_message(mode):
    try:
        return actuelle().textes.modes[mode]
    except DeclarationAbsente:
        return MODES_NEUTRES[mode]


def journal_intention(tool, lot, decision, lines, origine='desktop'):
    """The intention, logged before any window or write: no journal, no write. origine: app for an app's instance."""
    try:
        return journal_ecritures.intention(tool, lot, decision, lines, origine=origine)
    except journal_ecritures.JournalIndisponible as exc:
        raise ToolError(str(exc)) from None


def not_confirmed():
    return ToolError(f"Non confirmé sur le Mac (Annuler, fenêtre fermée ou pas de réponse en {confirmation.SECONDS} s); "
                     "rien n'est enregistré. Ne réessaie pas de toi-même : demande à l'utilisateur s'il veut recommencer.")


def asked_on_mac(title, lines, ident):
    """The Mac's window of a write in fenetre mode; a refusal or no click is logged as the intention's result."""
    try:
        confirmed = confirmation.ask(title, lines)
    except confirmation.TooLong as exc:
        journal_ecritures.resultat(ident, 'refuse', str(exc))
        raise ToolError(str(exc)) from None
    if not confirmed:
        journal_ecritures.resultat(ident, 'non_confirme')
        raise not_confirmed()


def go_lock(held):
    """Takes into held (an ExitStack) the lock every writer of the signal takes (F7, F8): from a decision to the end of
    its write on the go, no other process shows someone else's text or decides on the same day's money."""
    try:
        held.enter_context(signal_texte.verrou())
    except (signal_texte.Occupe, OSError):
        raise ToolError(BUSY) from None


@dataclass(frozen=True)
class Proposal:
    summary: dict
    action: str
    arguments: list
    files: tuple = ()  # pieces moved with the commit: ({source, destination, sha256},)


@dataclass(frozen=True)
class Operation:
    key: str
    prepare_name: str
    title: str
    description: str
    properties: dict
    required: tuple
    prepare: Callable
    json_result: bool = False


@dataclass(frozen=True)
class Step:
    operation: str
    title: str
    summary: dict
    action: str
    arguments: list
    json_result: bool
    request: dict = None  # the preparer's own arguments: a rebuild prepares again from them, never reuses an old summary
    added: float = 0.0  # clock() when the step joined the lot; a rebuild keeps it
    files: tuple = ()  # the proposal's pieces, moved by the store's commit inside its transaction
    signal: str = None  # the signal when the step joined the lot (politique.signal_ajout): recent text keeps the window (D090)


class MoteurLot:
    """store: snapshot(directory) -> verified copy, signature() of the source, commit(steps, expected=) -> outputs, all or nothing.
    executer(step) -> (done, output) runs a step on the current copy; valider(step) says its arguments are allowed; pointer(copy) is
    a context manager that makes the agent's code aim at the copy. Any exception from the agent's functions at an add is a refusal.
    The lot's MCP tools (one preparation per operation, then _voir, _retirer, _vider, _preparer and the write) are here; the
    agent gives only its operations, and the consignes du lot in instructions."""
    par_app = False  # set by the agent's server for the app's instance: its writes log origine app (D098)

    def __init__(self, store, operations, clock=wall_clock, *, catalog, prefix, confirm, base_name, executer, valider, pointer,
                 cles_cibles=(), doublon=None, instructions=''):
        self.store, self.clock = store, clock
        self.operations = [catalog[key] for key in operations]
        self.catalog = catalog
        self.prefix, self.confirm, self.base_name = prefix, confirm, base_name
        self.executer, self.valider, self.pointer = executer, valider, pointer
        self.cles_cibles, self.doublon = tuple(cles_cibles), doublon or f'Cette pièce est déjà dans le lot; {NOTHING}.'
        self.steps, self.frozen = [], None
        self.folder = self.copy = self.base = None
        self.last = None
        self.instructions = instructions

    def origine(self):
        """desktop or app, for the journal."""
        return 'app' if self.par_app else 'desktop'

    @property
    def tools(self):
        out = []
        for op in self.operations:
            out.append({'name': op.prepare_name, 'title': f'Lot — {op.title}',
                        'description': op.description + ' Ajoute cette étape au lot, sans écrire dans la base; '
                        "l'étape est vérifiée sur une copie privée qui inclut les étapes précédentes du lot.",
                        'inputSchema': {'type': 'object', 'additionalProperties': False, 'required': list(op.required),
                                        'properties': op.properties},
                        'annotations': {'title': f'Lot — {op.title}', **READ_ONLY}})
        empty = {'type': 'object', 'additionalProperties': False, 'properties': {}}
        out += [
            {'name': f'{self.prefix}_voir', 'title': 'Voir le lot', 'description': f'Étapes du lot {self.base_name} en attente, numérotées.',
             'inputSchema': empty, 'annotations': {'title': 'Voir le lot', **READ_ONLY}},
            {'name': f'{self.prefix}_retirer', 'title': 'Retirer une étape du lot',
             'description': "Retire une étape du lot par son numéro; les étapes suivantes sont revérifiées.",
             'inputSchema': {'type': 'object', 'additionalProperties': False, 'required': ['numero'],
                             'properties': {'numero': {'type': 'integer', 'minimum': 1}}},
             'annotations': {'title': 'Retirer une étape du lot', **READ_ONLY}},
            {'name': f'{self.prefix}_vider', 'title': 'Vider le lot', 'description': 'Abandonne toutes les étapes du lot.',
             'inputSchema': empty, 'annotations': {'title': 'Vider le lot', **READ_ONLY}},
            {'name': f'{self.prefix}_preparer', 'title': 'Préparer l\'enregistrement du lot',
             'description': f'Récapitulatif complet du lot et code à usage unique valide {PROPOSAL_SECONDS // 60} minutes. '
                            'Toute modification du lot ensuite annule le code.',
             'inputSchema': empty, 'annotations': {'title': 'Préparer l\'enregistrement du lot', **READ_ONLY}},
            {'name': self.confirm, 'title': self.confirm,
             'description': f"ÉCRITURE DANS LA BASE {self.base_name.upper()}. Enregistre toutes les étapes du lot préparé, seulement après le oui explicite "
                            f"de l'utilisateur au récapitulatif de {self.prefix}_preparer. Le récapitulatif dit si une fenêtre s'ouvrira sur "
                            "le Mac : si oui, rien n'est écrit tant que l'utilisateur n'y clique pas Enregistrer. Une sauvegarde vérifiée avant, puis une seule "
                            "transaction : si une étape échoue, rien n'est enregistré.",
             'inputSchema': {'type': 'object', 'additionalProperties': False, 'required': ['code'],
                             'properties': {'code': {'type': 'string', 'description': f'Code retourné par {self.prefix}_preparer.'}}},
             'annotations': {'title': self.confirm, 'readOnlyHint': False, 'destructiveHint': True,
                             'idempotentHint': False, 'openWorldHint': False}},
        ]
        return out

    @property
    def preparations(self):
        # Read by the app's registry check (spec 006): which tools add a step to this lot.
        return tuple(op.prepare_name for op in self.operations)

    @property
    def handlers(self):
        out = {op.prepare_name: (lambda arguments, op=op: self.add(op, arguments)) for op in self.operations}
        out.update({f'{self.prefix}_voir': self.view, f'{self.prefix}_retirer': self.remove, f'{self.prefix}_vider': self.clear,
                    f'{self.prefix}_preparer': self.prepare, self.confirm: self.commit})
        return out

    def window(self, now=None):
        """What the Mac's window shows: every step, one line each, with its age once it is a few minutes old."""
        now = self.clock() if now is None else now
        return [describe(i, step.summary, step.title) + age_note(step.added, now) for i, step in enumerate(self.steps, 1)]

    def overview(self):
        return [{'numero': i, 'operation': step.title, **step.summary} for i, step in enumerate(self.steps, 1)]

    def decision(self, steps):
        """The confirmation policy on these steps, each named by its preparation in this lot's own catalogue (never the model)."""
        return politique.decider(self.prefix, [{'outil': self.catalog[step.operation].prepare_name, 'operation': step.operation,
                                                'resume': step.summary, 'signal': step.signal} for step in steps])

    @staticmethod
    def journal_lines(steps):
        """The window's lines without their age, as {numero, operation, resume}: « numero. operation — resume » is the line."""
        return [{'numero': i, 'operation': one_line(step.title), 'resume': describe(i, step.summary)[len(f'{i}. '):]}
                for i, step in enumerate(steps, 1)]

    def _valide(self, step):
        try:
            return bool(self.valider(step))
        except Exception:
            return False

    def _fresh_copy(self):
        if self.folder is None:
            self.folder = tempfile.TemporaryDirectory(prefix='socle-lot-')
        if self.copy is not None:
            for suffix in ('', '-wal', '-shm'):
                Path(str(self.copy) + suffix).unlink(missing_ok=True)
        self.base = self.store.signature()
        self.copy = self.store.snapshot(Path(self.folder.name))

    def _rehearse(self, step):
        with self.pointer(self.copy):
            try:
                return self.executer(step)
            except Exception as exc:
                return False, f"Erreur à l'essai ({type(exc).__name__})."

    def _prepare_again(self, step):
        """The step prepared again on the current copy: (step with a fresh summary, None) or (None, reason).
        A step whose target or command changed is dropped, never silently retargeted."""
        op = next((o for o in self.operations if o.key == step.operation), None)
        if step.request is None or op is None:
            return step, None
        try:
            with self.pointer(self.copy):
                proposal = op.prepare(json.loads(json.dumps(step.request)))
        except ToolError as exc:
            return None, str(exc)
        except Exception as exc:
            return None, f"Erreur à la vérification ({type(exc).__name__})."
        if not isinstance(proposal, Proposal):
            return None, "Déjà en place dans la base."
        moved = [k for k in self.cles_cibles if k in step.summary and proposal.summary.get(k) != step.summary[k]]
        if proposal.action != step.action or list(proposal.arguments) != step.arguments or tuple(proposal.files) != step.files or moved:
            return None, ("L'opération ne vise plus la même cible depuis que la base a changé"
                          + (f" ({', '.join(moved)})" if moved else '') + "; ajoute-la de nouveau si elle est toujours voulue.")
        return replace(step, summary=proposal.summary), None

    def _rebuild(self):
        """Prepare and replay the lot on a fresh copy; summaries are recomputed, steps that no longer apply are dropped and reported."""
        self._fresh_copy()
        kept, dropped = [], []
        for step in self.steps:
            fresh, reason = self._prepare_again(step)
            if fresh is not None:
                done, output = self._rehearse(fresh)
                if done:
                    kept.append(fresh)
                    continue
                reason = output
            dropped.append({'operation': step.title, **step.summary, 'raison': reason})
        self.steps = kept
        return dropped

    def _refus(self, detail, expirees=()):
        """The refusal of a step; a step expired or dropped on the way is named, so « inchangé » is said only when true."""
        retirees = [d['operation'] for d in (*expirees, *self._rebuild())]
        if retirees:
            return f"Étape refusée à la vérification; étapes retirées du lot : {', '.join(retirees)}. " + detail
        return 'Étape refusée à la vérification; le lot est inchangé. ' + detail

    def _expire(self):
        """Drops the steps older than STEP_SECONDS, and those that depended on them; returns what left."""
        now = self.clock()
        old = [step for step in self.steps if too_old(step.added, now)]
        if not old:
            return []
        self.frozen = None
        self.steps = [step for step in self.steps if not too_old(step.added, now)]
        # The private copy still holds the old steps: it is rebuilt, even for an empty lot.
        return [{'operation': step.title, **step.summary, 'raison': expired_reason()} for step in old] + self._rebuild()

    def add(self, op, arguments):
        expired = self._expire()
        self.frozen = None
        if self.copy is None:
            self._fresh_copy()
        try:
            with self.pointer(self.copy):
                proposal = op.prepare(arguments)
        except ToolError:
            raise
        except Exception as exc:
            # The one hardening over the old lot (spec 010): an unexpected failure is a refusal, never an admitted step.
            raise ToolError(self._refus(f'Erreur ({type(exc).__name__}).', expired)) from None
        if not isinstance(proposal, Proposal):
            return {**proposal, 'ajoute': False, 'message': "Rien à ajouter : c'est déjà en place.", 'lot': self.overview()}
        step = Step(op.key, op.title, proposal.summary, proposal.action, list(proposal.arguments), op.json_result,
                    json.loads(json.dumps(arguments)), self.clock(), tuple(proposal.files), politique.signal_ajout())
        if not self._valide(step):
            raise ToolError(f'Étape invalide; {NOTHING}.')
        sources = [f['source'] for s in self.steps for f in s.files] + [f['source'] for f in step.files]
        if len(sources) != len(set(sources)):
            raise ToolError(self.doublon)
        done, output = self._rehearse(step)
        if not done:
            raise ToolError(self._refus(output, expired))
        self.steps.append(step)
        return {'ajoute': True, 'etape': len(self.steps), **proposal.summary, 'lot': self.overview(),
                **({'retirees_car_trop_anciennes': expired} if expired else {}),
                'message': f"Ajouté au lot, rien n'est écrit. Continue d'ajouter, ou {self.prefix}_preparer pour tout enregistrer."}

    def view(self, arguments=None):
        args(arguments or {}, ())
        expired = self._expire()
        # The last commit stays visible: after a lost reply, check here before adding the same steps again.
        return {'lot': self.overview(), 'etapes': len(self.steps), **({'retirees_car_trop_anciennes': expired} if expired else {}),
                **({'dernier_enregistrement': self.last} if self.last else {})}

    def remove(self, arguments):
        number = args(arguments, ('numero',))['numero']
        expired = self._expire()
        if expired:
            # The numbers changed: removing by an old number could take the wrong step.
            raise ToolError(f'Des étapes trop anciennes ont quitté le lot et la numérotation a changé; rien n\'est retiré. '
                            f'Relis {self.prefix}_voir : ' + json.dumps(expired, ensure_ascii=False))
        if type(number) is not int or not 1 <= number <= len(self.steps):
            raise ToolError('Numéro d\'étape invalide.')
        self.frozen = None
        removed = self.steps.pop(number - 1)
        dropped = self._rebuild()
        return {'retire': {'operation': removed.title, **removed.summary}, 'aussi_retirees': dropped, 'lot': self.overview()}

    def clear(self, arguments=None):
        args(arguments or {}, ())
        count = len(self.steps)
        self.steps, self.frozen = [], None
        if self.folder is not None:
            self.folder.cleanup()
        self.folder = self.copy = self.base = None
        return {'message': f'Lot vidé ({count} étape(s) abandonnée(s)). Rien n\'est écrit.'}

    def prepare(self, arguments=None):
        args(arguments or {}, ())
        self.frozen = None
        expired = self._expire()
        if not self.steps:
            raise ToolError('Le lot est vide.' + (' Étapes trop anciennes retirées : ' + json.dumps(expired, ensure_ascii=False)
                                                 if expired else ''))
        dropped = self._rebuild() if self.store.signature() != self.base else []
        if not self.steps:
            raise ToolError('La base a changé et plus aucune étape ne s\'applique : ' + json.dumps(dropped, ensure_ascii=False))
        decision = self.decision(self.steps)
        try:
            # Measured with the ages the window will show when the code expires, the longest they can be.
            check(why_lines(decision) + self.window(self.clock() + PROPOSAL_SECONDS))
        except TooLong as exc:
            # Known now, before the yes: the Mac's window shows a lot whole or not at all.
            raise ToolError(f'{exc} Retire des étapes avec {self.prefix}_retirer, puis rappelle {self.prefix}_preparer.') from None
        code = secrets.token_hex(3)
        self.frozen = {'code': code, 'expires': self.clock() + PROPOSAL_SECONDS, 'steps': tuple(self.steps), 'base': self.base,
                       'decision': decision}
        out = {'avertissement': f'⚠ {len(expired)} étape(s) ajoutée(s) il y a plus de {STEP_SECONDS // 60} minutes retirée(s) '
                                "du lot (voir retirees_car_trop_anciennes). Montre cet avertissement avant tout.",
               'retirees_car_trop_anciennes': expired} if expired else {}
        return {**out, 'lot': self.overview(), 'etapes': len(self.steps), 'retirees_car_base_modifiee': dropped, 'code': code,
                'confirmer_avec': self.confirm, 'expire_dans_minutes': PROPOSAL_SECONDS // 60,
                **mode_fields(decision), 'montant_compte': float(decision.montant),
                'message': f'Montre tout ce récapitulatif et attends un oui explicite avant {self.confirm}. '
                           + mode_message(decision.mode)}

    def commit(self, arguments):
        a = args(arguments, ('code',))
        frozen, self.frozen = self.frozen, None
        if frozen is None or not isinstance(a['code'], str) or not secrets.compare_digest(frozen['code'], a['code']):
            raise ToolError(f"Aucun lot préparé pour ce code. Appelle {self.prefix}_preparer; rien n'est enregistré.")
        if self.clock() > frozen['expires']:
            raise ToolError(f"Code expiré. Appelle {self.prefix}_preparer; rien n'est enregistré.")
        if any(too_old(step.added, self.clock()) for step in self.steps):
            raise ToolError(f"Une étape a plus de {STEP_SECONDS // 60} minutes. Appelle {self.prefix}_preparer; rien n'est enregistré.")
        if frozen['steps'] != tuple(self.steps) or self.store.signature() != frozen['base']:
            raise ToolError(f"Le lot ou la base a changé depuis le récapitulatif. Appelle {self.prefix}_preparer; rien n'est enregistré.")
        if not all(self._valide(step) for step in self.steps):
            raise ToolError("Lot invalide; rien n'est enregistré.")
        with ExitStack() as held:
            # Held from the decision to the end of a write on the go (F7, F8); a window lets it go first.
            go_lock(held)
            # Judged again on the frozen steps: stricter than the summary if the signal changed since, never softer (D090).
            decision = politique.plus_stricte(frozen['decision'], self.decision(frozen['steps']))
            ident = journal_intention(self.confirm, self.prefix, decision, self.journal_lines(frozen['steps']), origine=self.origine())
            if decision.mode == 'fenetre':
                held.close()
                asked_on_mac(f'{self.confirm} — base {self.base_name}', why_lines(decision) + self.window(), ident)
            try:
                outputs = self.store.commit(self.steps, expected=frozen['base'])
            except ToolError as exc:
                journal_ecritures.resultat(ident, 'refuse', str(exc))
                raise
            except Exception as exc:
                # Interrupted inside the store: SQLite rolls back, but nothing here can prove it. Never a plain refusal
                # (review 008, P2-2): the lot is emptied so a retry cannot duplicate what may be written.
                journal_ecritures.resultat(ident, 'inconnu', type(exc).__name__)
                self.clear()
                raise ToolError(unknown_result(self.prefix)) from None
            except BaseException as exc:
                journal_ecritures.resultat(ident, 'inconnu', type(exc).__name__)
                raise
        warning = journal_ecritures.resultat(ident, 'enregistre')
        results = [{'numero': i, 'operation': step.title, 'resultat': output}
                   for i, (step, output) in enumerate(zip(self.steps, outputs), 1)]
        self.clear()
        self.last = {'heure': f'{datetime.now():%Y-%m-%d %H:%M:%S}', 'etapes': results}
        return {'enregistre': True, **mode_fields(decision), 'etapes': results, **({'avertissement': warning} if warning else {})}
