"""Lance un programme de l'agent, jamais par un shell : la liste des programmes et le catalogue des outils sont les siens.

catalogue : un objet qui offre get_tool(nom) (un outil avec program et command, ou None) et validate_arguments(nom, arguments).
"""
import subprocess
import sys


class ExecutionError(Exception):
    pass


class Executeur:
    def __init__(self, programmes, catalogue):
        self.programmes, self.catalogue = programmes, catalogue

    def commande(self, action, arguments):
        tool = self.catalogue.get_tool(action)
        if tool is None:
            raise ExecutionError(f"Action non autorisée : {action}")
        if not self.catalogue.validate_arguments(action, arguments):
            raise ExecutionError(f"Arguments invalides pour : {action}")
        program = self.programmes.get(tool.program)
        if program is None:
            raise ExecutionError(f"Programme non enregistré : {tool.program}")
        if not program.is_file():
            raise ExecutionError(f"Programme introuvable : {program}")
        command = [sys.executable, str(program)]
        if tool.command:
            command.append(tool.command)
        command.extend(arguments)
        return command

    @staticmethod
    def lancer(action, command, timeout=15):
        """La commande déjà construite; stdout, ou ExecutionError avec la sortie d'erreur."""
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, shell=False)
        except subprocess.TimeoutExpired:
            raise ExecutionError(f"Délai dépassé pour l'action : {action}")
        except OSError as exc:
            raise ExecutionError(f"Impossible d'exécuter l'action : {action}") from exc
        stdout, stderr = result.stdout.strip(), result.stderr.strip()
        if result.returncode != 0:
            raise ExecutionError(f"Échec de l'action {action} : {stderr or stdout or 'Erreur inconnue'}")
        return stdout

    def execute(self, action, arguments, timeout=15):
        return self.lancer(action, self.commande(action, arguments), timeout)

    def execute_safe(self, action, arguments, timeout=15):
        return sur(action, lambda: self.execute(action, arguments, timeout))


def sur(action, lancer):
    """Le résultat de lancer() dans la forme d'execute_safe; une ExecutionError devient error."""
    try:
        return {"ok": True, "action": action, "result": lancer(), "error": None}
    except ExecutionError as exc:
        return {"ok": False, "action": action, "result": None, "error": str(exc)}
