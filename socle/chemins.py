"""Dossiers et fichiers privés, écrits d'un seul coup et jamais à travers un lien."""
import os
from pathlib import Path
import secrets


def private_folder(folder):
    """The folder, created if needed and set to 700 so only the owner's account can open it; missing parents are left as they are."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    folder.chmod(0o700)
    return folder


def write_private(folder, name, data):
    """Writes data to folder/name, owner-only, all at once, and returns the path. The folder is held open (never through
    a link) and a temporary file beside it is renamed over the old one: a link put in place of the folder or of the file
    never redirects the write, and nobody ever reads half a file."""
    held = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        temporary = f'.{name}.{secrets.token_hex(4)}.partiel'
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=held)
        try:
            with os.fdopen(fd, 'wb') as file:
                file.write(data)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, name, src_dir_fd=held, dst_dir_fd=held)
        except BaseException:
            try:
                os.unlink(temporary, dir_fd=held)
            except OSError:
                pass
            raise
    finally:
        os.close(held)
    return Path(folder) / name
