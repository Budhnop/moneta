"""Le cadre d'appel MCP sur stdio (spec 013 : venu de Smith) : protocole, signal, journal des écritures hors lot.

Chaque agent étend ServeurBase de ses outils (modules : tools, handlers, instructions) et, pour l'instance de l'app, de ses
méthodes (methodes_app). Aucun import d'agent.
"""
import contextvars
import io
import json
import sys

from socle import externe, journal_ecritures, politique, signal_texte, schema, versionne
from socle.declaration import actuelle, DeclarationAbsente
from socle.lots import READ_ONLY, ToolError, asked_on_mac, journal_intention, mode_fields, why_lines  # noqa: F401

PROTOCOL_VERSIONS = ('2025-06-18', '2025-03-26', '2024-11-05')
MAX_MESSAGE = 1_000_000
SIGNAL_REFUSE = "Lecture refusée : le signal du texte d'autrui n'a pas pu être inscrit; rien n'est montré."
UNKNOWN_WRITE = ("⚠ Résultat inconnu : erreur imprévue pendant l'enregistrement. Les changements ont peut-être été "
                 "enregistrés : vérifie d'abord le journal des écritures avant de recommencer.")
UNKNOWN_READ = 'Lecture indisponible. Aucune donnée ne peut être confirmée.'
SANS_ECRITURE = " Aucune écriture n'est disponible."

# The intention of the WR_SAVE_* without a batch now running, for logged to log its result (spec 002, T029).
WRITE_LOGGED = contextvars.ContextVar('ecriture_hors_lot', default=None)


class Refus(Exception):
    """A request of the app refused before any handler runs (spec 006): -32602, nothing read or prepared."""


def confirmed_on_mac(tool, lines):
    """Every WR_SAVE_* without a batch asks the user on the Mac itself (socle.confirmation) right before writing; no click, no
    write. Its window is the policy's (always fenetre, hors_liste, and any other reason found), logged like a batch: the
    intention here, before the window; the result in logged, once the tool is done."""
    lines, name = list(lines), tool.split(' — ')[0]
    decision = politique.decider(None, [])
    ident = journal_intention(name, None, decision, [{'numero': 1, 'operation': name, 'resume': lines[0] if lines else ''}])
    asked_on_mac(tool, why_lines(decision) + lines, ident)
    WRITE_LOGGED.set((ident, decision))


def logged(handler, arguments):
    """handler(arguments); when it wrote after confirmed_on_mac, the result goes to the journal, and the mode to the result."""
    token = WRITE_LOGGED.set(None)
    try:
        try:
            raw = handler(arguments)
        except ToolError as exc:
            if WRITE_LOGGED.get():
                journal_ecritures.resultat(WRITE_LOGGED.get()[0], 'refuse', str(exc))
            raise
        except BaseException as exc:
            if WRITE_LOGGED.get():
                journal_ecritures.resultat(WRITE_LOGGED.get()[0], 'inconnu', type(exc).__name__)
            raise
        if WRITE_LOGGED.get():
            ident, decision = WRITE_LOGGED.get()
            warning = journal_ecritures.resultat(ident, 'enregistre')
            if isinstance(raw, dict):
                raw.update(mode_fields(decision))
                if warning:
                    raw['avertissement'] = ' '.join(filter(None, [raw.get('avertissement'), warning]))
        return raw
    finally:
        WRITE_LOGGED.reset(token)


def allumer(par=None):
    """The signal, before anything is shown; a signal that cannot be written shows nothing. par : the declared agent by default."""
    try:
        signal_texte.inscrire(par)
    except Exception:
        raise ToolError(SIGNAL_REFUSE) from None


def messages(stdin):
    """Each line, read MAX_MESSAGE characters at a time: a longer line is read through and dropped, never held whole (None).
    Text or bytes alike (serve reads the bytes under a text stdin, review 015, R03)."""
    fin = b'\n' if isinstance(stdin, io.BufferedIOBase) or getattr(stdin, 'mode', '').endswith('b') else '\n'
    while line := stdin.readline(MAX_MESSAGE + 1):
        if len(line) <= MAX_MESSAGE or line.endswith(fin):
            yield line
            continue
        while line and not line.endswith(fin):
            line = stdin.readline(MAX_MESSAGE + 1)
        yield None


def ecrire(stdout, response):
    """One line of JSON; ASCII escapes when the stream cannot take a character (lone surrogate, non UTF-8 terminal)."""
    try:
        texte = json.dumps(response, ensure_ascii=False) + '\n'
        try:
            stdout.write(texte)
        except UnicodeEncodeError:
            stdout.write(json.dumps(response, ensure_ascii=True) + '\n')
    except (ValueError, RecursionError):
        stdout.write(json.dumps(error(None, -32603, 'Internal error')) + '\n')
    stdout.flush()


def result(ident, value):
    return {'jsonrpc': '2.0', 'id': ident, 'result': value}


def error(ident, code, text):
    return {'jsonrpc': '2.0', 'id': ident, 'error': {'code': code, 'message': text}}


class ServeurBase:
    """info : serverInfo; instructions : consignes de l'agent, suivies de celles de chaque module; outils et handlers :
    ceux de l'agent lui-même. sans_autrui : outils dont le code ne laisse jamais sortir le texte d'autrui sans marque (D090).
    hors_wr : écritures sans le préfixe WR_SAVE_ (aucun rafraîchissement avant elles, résultat inconnu en cas de panne).
    app : l'instance de l'app; tools/call y est fermé, seules ses méthodes répondent. par : l'écrivain du signal (l'agent
    déclaré par défaut)."""

    def __init__(self, info, instructions, outils=(), handlers=None, modules=(), before_call=None, app=False,
                 sans_autrui=(), hors_wr=(), par=None, commandes=(), objet_vise=None):
        # before_call refreshes the private copies; it never receives model input.
        self.info, self.before_call, self.app, self.par = info, before_call, app, par
        self.sans_autrui, self.hors_wr = frozenset(sans_autrui), tuple(hors_wr)
        self.parts = list(modules)
        self.tools = list(outils) + [tool for part in self.parts for tool in part.tools]
        self.handlers = dict(handlers or {})
        for part in self.parts:
            if set(part.handlers) & set(self.handlers):
                raise ValueError('Outil MCP en double.')
            self.handlers.update(part.handlers)
        self.objet_vise = objet_vise
        self.commandes = {}
        for definition, fonction in commandes:
            nom = definition['name']
            if nom in self.commandes or not callable(fonction):
                raise ValueError('Commande MCP invalide ou en double.')
            self.commandes[nom] = (definition, fonction)
        self.schemas = {}
        try:
            versionnees = actuelle().regles.versionnees
        except DeclarationAbsente:
            versionnees = {}
        # Les registres historiques gardent leur validation métier; le nouveau profil charge ses schémas explicites.
        # Revue T069 de Thot, R03 et R09 : un agent versionné n'a pas de fenêtre du Mac; ses textes restent fidèles.
        self.nettoyer = externe.fidele if versionnees else externe.visible
        if versionnees:
            for outil in self.tools:
                self.schemas[outil['name']] = schema.charger(outil.get('inputSchema', {'type': 'object'}))
        self.instructions = instructions + "".join(part.instructions for part in self.parts) + (
            "" if versionnees or any(hasattr(part, "confirm") for part in self.parts) else SANS_ECRITURE)

    def methodes_app(self):
        """{méthode: fonction(params)} de l'instance de l'app; une fonction refuse par Refus."""
        return {}

    def certifie(self, name):
        """In the agent's closed list (D090): its code never lets someone else's text out unmarked."""
        return isinstance(name, str) and name in self.sans_autrui

    def ecriture(self, name):
        return isinstance(name, str) and (name.startswith('WR_SAVE_') or name in self.hors_wr or self.versionnee(name))

    def versionnee(self, name):
        try:
            info = actuelle().outil(name)
            return info is not None and info.type == 'versionnee'
        except DeclarationAbsente:
            return False

    def appeler_versionnee(self, handler, arguments, name):
        objet = self.objet_vise(name, arguments) if self.objet_vise else 'nouveau'
        ident = versionne.intention(name, objet)
        try:
            raw = handler(arguments)
        except ToolError as exc:
            # A refusal of the agent itself: nothing was written, and it is told as it is.
            versionne.resultat(ident, 'refuse')
            raise
        except BaseException:
            versionne.resultat(ident, 'inconnu')
            raise
        etat = 'refuse' if isinstance(raw, dict) and 'erreur' in raw else 'enregistre'
        cle_objet, cle_version = actuelle().regles.versionnees[name]
        objet, numero = (raw.get(cle_objet), raw.get(cle_version)) if isinstance(raw, dict) else (None, None)
        warning = versionne.resultat(ident, etat, objet, numero)
        if warning:
            if isinstance(raw, dict):
                raw = {**raw, 'avertissement': ' '.join(filter(None, [raw.get('avertissement'), warning]))}
            else:
                raw = {'resultat': raw, 'avertissement': warning}
        return raw

    def handle(self, message):
        """Return a response dict, or None for notifications."""
        if not isinstance(message, dict) or message.get('jsonrpc') != '2.0' or not isinstance(message.get('method'), str):
            return error(message.get('id') if isinstance(message, dict) else None, -32600, 'Invalid Request')
        method, ident = message['method'], message.get('id')
        if 'id' not in message:
            return None
        params = message.get('params') or {}
        if method == 'initialize':
            requested = params.get('protocolVersion') if isinstance(params, dict) else None
            version = requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
            return result(ident, {'protocolVersion': version, 'capabilities': {'tools': {'listChanged': False},
                                      **({'prompts': {'listChanged': False}} if self.commandes else {})},
                                  'serverInfo': self.info, 'instructions': self.instructions})
        if method == 'prompts/list' and self.commandes:
            return result(ident, {'prompts': [d for d, _ in self.commandes.values()]})
        if method == 'prompts/get' and self.commandes:
            name = params.get('name') if isinstance(params, dict) else None
            if not isinstance(name, str) or name not in self.commandes:
                return error(ident, -32602, 'Commande inconnue.')
            definition, fonction = self.commandes[name]
            arguments = params.get('arguments', {})
            if not isinstance(arguments, dict) or any(type(v) is not str for v in arguments.values()):
                return error(ident, -32602, 'Arguments de commande invalides.')
            for argument in definition.get('arguments', []):
                if argument.get('required') and argument['name'] not in arguments:
                    return error(ident, -32602, f"Argument « {argument['name']} » : obligatoire.")
            return result(ident, {**({'description': definition['description']} if 'description' in definition else {}),
                                  'messages': fonction(arguments)})
        if method == 'ping':
            return result(ident, {})
        if method == 'tools/list':
            return result(ident, {'tools': self.tools})
        if self.app:
            # The app's instance: nothing is called without naming an agent whose registry allows it (second lock, FR-008).
            if method == 'tools/call':
                return error(ident, -32601, 'Method not found')
            app = self.methodes_app().get(method)
            if app is not None:
                try:
                    return result(ident, app(params if isinstance(params, dict) else None))
                except Refus as exc:
                    return error(ident, -32602, str(exc))
        if method == 'tools/call':
            name = params.get('name') if isinstance(params, dict) else None
            handler = self.handlers.get(name) if isinstance(name, str) else None
            if handler is None:
                return error(ident, -32602, 'Outil inconnu.')
            arguments = params.get('arguments', {}) if self.versionnee(name) else params.get('arguments') or {}
            if not isinstance(arguments, dict):
                return error(ident, -32602, 'Arguments invalides.')
            return result(ident, self.call(handler, arguments, name))
        return error(ident, -32601, 'Method not found')

    def call(self, handler, arguments, name=None):
        try:
            # A write outside WR_SAVE_* reads no database: a failed refresh must not announce an unknown write (D104).
            versionnee = self.versionnee(name)
            if name in self.schemas:
                try:
                    arguments = self.schemas[name].valider(arguments)
                except ValueError as exc:
                    raise ToolError(str(exc)) from None
            if not versionnee and self.before_call is not None and name not in self.hors_wr:
                self.before_call()
            # Outside text is data: invisible characters removed both ways, and a notice on results that hold it.
            arguments = arguments if versionnee else externe.cleaned(arguments, self.nettoyer)[0]
            # What could blur the Mac's window is refused, never cleaned: what is written is what the user reads (D054).
            # A versioned agent has no window (review T069 of Thot, R09).
            reason = None if versionnee or self.nettoyer is externe.fidele else externe.blurred(arguments)
            if reason:
                raise ToolError(f'Texte refusé : {reason} (la fenêtre du Mac ne pourrait pas le montrer fidèlement); '
                                "rien n'est fait.")
            try:
                raw = self.appeler_versionnee(handler, arguments, name) if versionnee else logged(handler, arguments)
            except ToolError as exc:
                # An error of a tool outside the closed list may quote someone else's text too (D090): signal first.
                if not self.certifie(name) and not getattr(exc, 'juge', False):
                    allumer(self.par)
                raise
            images = getattr(raw, 'images', ())
            certified = self.certifie(name) or isinstance(raw, externe.Juge)
            data, autrui = externe.marked(raw, self.nettoyer)
            if autrui or not certified or (images and getattr(raw, 'images_de_confiance', False) is not True):
                # Someone else's text, or a result no one judged (D090): the signal first (spec 002, R4), so the next
                # write opens the window. No signal, no text.
                allumer(self.par)
        except ToolError as exc:
            return {'content': [{'type': 'text', 'text': externe.visible(str(exc))}], 'isError': True}
        except Exception:
            # An unexpected error in a write may come after the write itself: never told as a refusal (review 008, P2-2).
            message = UNKNOWN_WRITE if self.ecriture(name) else UNKNOWN_READ
            return {'content': [{'type': 'text', 'text': message}], 'isError': True}
        return {'content': [{'type': 'text', 'text': json.dumps(data, ensure_ascii=False)}]
                + [{'type': 'image', 'data': image, 'mimeType': 'image/jpeg'} for image in images],
                'structuredContent': data, 'isError': False}

    def serve(self, stdin=sys.stdin, stdout=sys.stdout):
        # One bad message gets an error reply; it never stops the server. A text stdin is read as bytes underneath, so an
        # invalid UTF-8 byte is one bad line, not the end of the server (review 015, R03).
        for line in messages(getattr(stdin, 'buffer', stdin)):
            if line is None:
                ecrire(stdout, error(None, -32600, 'Message trop long.'))
                continue
            try:
                texte = line.decode('utf-8') if isinstance(line, bytes) else line
                if not texte.strip():
                    continue
                message = json.loads(texte)
            except (ValueError, RecursionError):
                response = error(None, -32700, 'Parse error')
            else:
                try:
                    response = self.handle(message)
                except Exception:
                    response = error(message.get('id') if isinstance(message, dict) else None, -32603, 'Internal error')
            if response is not None:
                ecrire(stdout, response)
