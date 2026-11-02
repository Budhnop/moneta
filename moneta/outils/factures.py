"""Receipts (factures) in Ugo's Google Drive: dropped in « DojoD/05 Administration/Factures/à classer », moved by
WR_SAVE_FINANCES_BD to « …/Factures/AAAA/AAAA-MM-JJ_montant.ext » (no free text: F4, D091) and linked to their expense by that path,
relative to the Factures folder (D063; moved from « Agent Smith/factures » in D066, the stored paths unchanged).

Same rules as the Drive copy (mcp_drive): only inside the Factures folder, never through a link, folders held open and checked
by what they really are. A receipt is identified by its SHA-256 when prepared: another file under the same name is refused."""
from datetime import datetime
import fcntl
import hashlib
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
import time
import unicodedata

from moneta.chemins import drive
from socle.lots import ToolError
from socle import sorties
from socle.sorties import same_name

# Le dossier DojoD d'Ugo (D066), comme dans core/services/mcp_drive.py de Smith : les sauvegardes y restent réservées (D056).
DOJOD = 'DojoD'
ADMIN = f'{DOJOD}/05 Administration'
RESERVED = (f'{ADMIN}/Sauvegardes', 'Agent Smith/sauvegardes', 'Agent Smith sauvegardes')


def reserved(root, target):
    return sorties.reserved(root, target, RESERVED)

FOLDER = f'{ADMIN}/Factures'
INBOX_NAME = 'à classer'
INBOX = f'{FOLDER}/{INBOX_NAME}'
EXTENSIONS = ('.jpg', '.jpeg', '.png', '.heic', '.pdf')
MAX_BYTES = 20_000_000
MAX_LISTED = 100
# The path kept on the expense, relative to FOLDER: year, date, amount, optional rank, extension. No free text: the name
# reaches Drive on the go (F4, D091). Receipts filed before keep their supplier between date and amount.
STORED = re.compile(r'^(\d{4})/(\d{4}-\d{2}-\d{2})(_[a-z0-9-]{1,40})?_\d{1,6}\.\d{2}(-[2-9])?\.(jpg|jpeg|png|heic|pdf)$')
PREVIEW_PIXELS = 1600


class Missing(ToolError):
    pass


def valid_stored(path):
    match = STORED.match(path) if isinstance(path, str) else None
    return bool(match) and match.group(1) == match.group(2)[:4]


def _hold(relative):
    """A folder under FOLDER held open after checking what it really is."""
    root = drive()
    if not root.is_dir():
        raise ToolError("Google Drive introuvable sur le Mac (l'app Google Drive est-elle ouverte?); rien n'est fait.")
    try:
        held = os.open(root.joinpath(*Path(relative).parts), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        raise Missing(f"Dossier introuvable dans Google Drive : « {relative} ».") from None
    return _checked(held, relative)


def _year(year):
    """The year folder of FOLDER held open, created inside the checked FOLDER itself (never through a link)."""
    parent = _hold(FOLDER)
    try:
        try:
            os.mkdir(year, 0o700, dir_fd=parent)
        except FileExistsError:
            pass
        try:
            held = os.open(year, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        except OSError:
            raise ToolError(f"« {FOLDER}/{year} » n'est pas un dossier; rien n'est enregistré.") from None
    finally:
        os.close(parent)
    return _checked(held, f'{FOLDER}/{year}')


def _checked(held, relative):
    root = drive()
    try:
        # What the folder really is, not its name: a link named like it may lead anywhere.
        real, top = Path(os.fsdecode(fcntl.fcntl(held, fcntl.F_GETPATH, bytes(1024)).rstrip(b'\0'))), root.resolve()
        inside = [same_name(p) for p in real.relative_to(top).parts] if real.is_relative_to(top) else []
        wanted = [same_name(p) for p in Path(FOLDER).parts]
        if inside[:len(wanted)] != wanted or reserved(top, real):
            raise ToolError(f"« {relative} » n'est pas dans « {FOLDER} »; rien n'est fait.")
    except BaseException:
        os.close(held)
        raise
    return held


def _name(value):
    """One file name of the inbox, as given: no folder, no hidden file, a known extension."""
    if (not isinstance(value, str) or not 0 < len(value) <= 120 or '/' in value or value.startswith('.')
            or any(ord(c) < 32 for c in value) or Path(value).suffix.lower() not in EXTENSIONS):
        raise ToolError(f"Nom de facture invalide : un fichier de « {INBOX} » ({', '.join(EXTENSIONS)}); rien n'est fait.")
    return value


def _read(folder_fd, name, label):
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=folder_fd)
    except OSError:
        raise ToolError(f"Facture introuvable : « {label} »; rien n'est fait.") from None
    with os.fdopen(fd, 'rb') as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
            raise ToolError(f"« {label} » n'est pas un fichier ordinaire de 20 Mo au plus; rien n'est fait.")
        return handle.read(MAX_BYTES + 1)


def dossier_factures():
    """The receipts' folder of the Drive, every year in it: the pieces of the night's image (scripts.sauvegarde_quotidienne)."""
    return drive().joinpath(*Path(FOLDER).parts)


def inbox_present():
    """True when the inbox is a real folder of the Drive, not a link (moneta-verifier; its name only, nothing read in it)."""
    folder = drive().joinpath(*Path(INBOX).parts)
    return folder.is_dir() and not folder.is_symlink()


def inbox():
    """The receipts waiting in the inbox: [{nom, ko, depose_le}], newest first."""
    try:
        held = _hold(INBOX)
    except Missing:
        raise ToolError(f"Crée d'abord le dossier « {INBOX} » dans Google Drive, puis dépose-y les factures.") from None
    try:
        rows = []
        for entry in os.scandir(held):
            if entry.name.startswith('.') or Path(entry.name).suffix.lower() not in EXTENSIONS:
                continue
            info = entry.stat(follow_symlinks=False)
            if stat.S_ISREG(info.st_mode):
                rows.append((info.st_mtime, {'nom': unicodedata.normalize('NFC', entry.name), 'ko': round(info.st_size / 1000),
                                             'depose_le': f'{datetime.fromtimestamp(info.st_mtime):%Y-%m-%d %H:%M}'}))
    finally:
        os.close(held)
    rows.sort(key=lambda r: r[0], reverse=True)
    return [row for _, row in rows]


def waiting(name):
    """(name, sha256) of one receipt of the inbox."""
    name = _name(name)
    held = _hold(INBOX)
    try:
        data = _read(held, name, name)
    finally:
        os.close(held)
    return name, hashlib.sha256(data).hexdigest()


def destination(day, cents, name, taken=()):
    """The free stored path for a receipt: rank -2 to -9 when the same name is already used in Drive or in `taken`."""
    year, ext = day[:4], Path(name).suffix.lower()
    base = f'{day}_{cents // 100}.{cents % 100:02d}'
    root = drive().joinpath(*Path(FOLDER).parts, year)
    for rank in ('', '-2', '-3', '-4', '-5', '-6', '-7', '-8', '-9'):
        path = f'{year}/{base}{rank}{ext}'
        if path not in taken and not (root / f'{base}{rank}{ext}').exists() and not (root / f'{base}{rank}{ext}').is_symlink():
            return path
    raise ToolError("Trop de factures du même jour et du même montant; rien n'est fait.")


def move_all(moves):
    """Moves each {source, destination, sha256} from the inbox to its place; returns the moves done, for undo().
    Any problem undoes what was moved and raises: the caller's transaction is then rolled back."""
    done = []
    try:
        for move in moves:
            year = move['destination'].split('/')[0]
            source = _hold(INBOX)
            try:
                target = _year(year)
                try:
                    if hashlib.sha256(_read(source, move['source'], move['source'])).hexdigest() != move['sha256']:
                        raise ToolError(f"La facture « {move['source']} » a changé depuis le récapitulatif; rien n'est enregistré.")
                    final = move['destination'].split('/')[1]
                    try:
                        os.stat(final, dir_fd=target, follow_symlinks=False)
                        raise ToolError(f"« {FOLDER}/{move['destination']} » existe déjà; rien n'est enregistré.")
                    except FileNotFoundError:
                        pass
                    os.rename(move['source'], final, src_dir_fd=source, dst_dir_fd=target)
                    done.append(move)
                finally:
                    os.close(target)
            finally:
                os.close(source)
    except BaseException:
        undo(done)
        raise
    return done


def undo(done):
    """Puts moved receipts back in the inbox; returns those that could not be."""
    stuck = []
    for move in reversed(done):
        year, final = move['destination'].split('/')
        try:
            source, target = _hold(f'{FOLDER}/{year}'), _hold(INBOX)
            try:
                os.rename(final, move['source'], src_dir_fd=source, dst_dir_fd=target)
            finally:
                os.close(source)
                os.close(target)
        except Exception:
            stuck.append(move)
    return stuck


def preview(name=None, stored=None):
    """A JPEG of the receipt (first page of a PDF), at most PREVIEW_PIXELS on its long side, made by macOS's sips from a
    private copy of the file: bytes."""
    if stored is not None:
        if not valid_stored(stored):
            raise ToolError("Chemin de facture invalide.")
        year, label = stored.split('/')
        held = _hold(f'{FOLDER}/{year}')
    else:
        label = _name(name)
        held = _hold(INBOX)
    try:
        data = _read(held, label, label)
    finally:
        os.close(held)
    with tempfile.TemporaryDirectory(prefix='smith-facture-') as folder:
        source, out = Path(folder) / ('source' + Path(label).suffix.lower()), Path(folder) / 'apercu.jpg'
        source.write_bytes(data)
        result = subprocess.run(['/usr/bin/sips', '-s', 'format', 'jpeg', '-s', 'formatOptions', '70', '-Z', str(PREVIEW_PIXELS),
                                 str(source), '--out', str(out)], capture_output=True, timeout=30)
        if result.returncode != 0 or not out.is_file():
            raise ToolError(f"Aperçu impossible pour « {label} » (image ou PDF illisible).")
        return out.read_bytes()


DEPOT_PROFILE = Path(__file__).with_name('sips_depot.sb')
CONVERSION_REFUSED = 'Conversion HEIC impossible; rien n’est déposé.'
UNREADABLE = 'Image ou PDF illisible; rien n’est déposé.'
DEPOSIT_REFUSED = 'Dépôt impossible : vérifie Google Drive et le dossier « DojoD/05 Administration/Factures/à classer ».'
DEPOT_PREFIX = 'smith-depot-'
# Everything else (position, date, device, C2PA, comments) leaves the JPG; the orientation comes back on its own.
KEPT_SEGMENTS = ((0xe0, b'JFIF\0'), (0xe2, b'ICC_PROFILE\0'), (0xee, b'Adobe'))


def _kind(data):
    if not 1 <= len(data) <= MAX_BYTES:
        raise ToolError('Facture vide ou trop volumineuse (20 Mo au plus).')
    for signature, ext in ((b'\xff\xd8\xff', '.jpg'), (b'\x89PNG\r\n\x1a\n', '.png'), (b'%PDF-', '.pdf')):
        if data.startswith(signature):
            return ext
    if data[4:8] == b'ftyp' and data[8:12] in (b'heic', b'heix', b'hevc', b'mif1'):
        return '.heic'
    raise ToolError('Format attendu : HEIC, JPEG, PNG ou PDF.')


def _orientation(exif):
    import struct
    if not exif.startswith(b'Exif\0\0'):
        return None
    tiff = exif[6:]
    try:
        endian = {b'II': '<', b'MM': '>'}[tiff[:2]]
        if struct.unpack_from(endian + 'H', tiff, 2)[0] != 42:
            raise ValueError
        offset = struct.unpack_from(endian + 'I', tiff, 4)[0]
        count = struct.unpack_from(endian + 'H', tiff, offset)[0]
        if offset + 2 + 12 * count + 4 > len(tiff):
            raise ValueError
        for i in range(count):
            tag, kind, n = struct.unpack_from(endian + 'HHI', tiff, offset + 2 + 12 * i)
            if tag == 0x0112 and kind == 3 and n == 1:
                return struct.unpack_from(endian + 'H', tiff, offset + 10 + 12 * i)[0]
    except (KeyError, struct.error, ValueError):
        raise ToolError(CONVERSION_REFUSED) from None
    return None


def _without_gps(data):
    """Walk every JPEG segment, including between scans; retain only orientation from Exif."""
    import struct
    if not data.startswith(b'\xff\xd8'):
        raise ToolError(CONVERSION_REFUSED)
    output, pos, orientation, scanned = bytearray(data[:2]), 2, None, False
    frame = False
    while pos < len(data):
        start = pos
        if data[pos] != 255:
            raise ToolError(CONVERSION_REFUSED)
        while pos < len(data) and data[pos] == 255:
            pos += 1
        if pos == len(data):
            raise ToolError(CONVERSION_REFUSED)
        marker = data[pos]
        pos += 1
        if marker == 0xd9:
            if pos != len(data) or not scanned:
                raise ToolError(CONVERSION_REFUSED)
            output.extend(data[start:pos])
            if orientation is not None and 2 <= orientation <= 8:
                exif = b'Exif\0\0II' + struct.pack('<HIHHHIHHI', 42, 8, 1, 0x0112, 3, 1, orientation, 0, 0)
                # TIFF: one SHORT orientation entry and a null next-IFD pointer.
                output[2:2] = b'\xff\xe1' + struct.pack('>H', len(exif) + 2) + exif
            return bytes(output)
        if marker in (0, 0xd8) or 0xd0 <= marker <= 0xd7:
            raise ToolError(CONVERSION_REFUSED)
        if marker == 1:
            output.extend(data[start:pos])
            continue
        if pos + 2 > len(data):
            raise ToolError(CONVERSION_REFUSED)
        length = int.from_bytes(data[pos:pos + 2], 'big')
        end = pos + length
        if length < 2 or end > len(data):
            raise ToolError(CONVERSION_REFUSED)
        if marker in (0xc0, 0xc1, 0xc2):
            payload = data[pos + 2:end]
            if len(payload) < 6 or len(payload) != 6 + 3 * payload[5] or not payload[5] or not all(
                    int.from_bytes(payload[i:i + 2], 'big') for i in (1, 3)):
                raise ToolError(CONVERSION_REFUSED)
            frame = True
        if marker == 0xda:
            payload = data[pos + 2:end]
            if not frame or not payload or not payload[0] or len(payload) != 4 + 2 * payload[0]:
                raise ToolError(CONVERSION_REFUSED)
        if marker == 0xe1:
            value = _orientation(data[pos + 2:end])
            if value is not None:
                orientation = value
        elif not (0xe0 <= marker <= 0xef or marker == 0xfe) or any(
                marker == kept and data[pos + 2:end].startswith(head) for kept, head in KEPT_SEGMENTS):
            output.extend(data[start:end])
        pos = end
        if marker == 0xda:
            scanned = True
            start = pos
            while pos < len(data):
                if data[pos] != 255:
                    pos += 1
                    continue
                next_pos = pos + 1
                while next_pos < len(data) and data[next_pos] == 255:
                    next_pos += 1
                if next_pos >= len(data):
                    raise ToolError(CONVERSION_REFUSED)
                if data[next_pos] == 0 or 0xd0 <= data[next_pos] <= 0xd7:
                    pos = next_pos + 1
                else:
                    break
            output.extend(data[start:pos])
    raise ToolError(CONVERSION_REFUSED)


def _temp():
    # CPython may omit the Darwin name; 65537 is _CS_DARWIN_USER_TEMP_DIR in unistd.h.
    return os.path.realpath(os.confstr(os.confstr_names.get('CS_DARWIN_USER_TEMP_DIR', 65537)))


def _sips(temp, refusal, *args):
    if not DEPOT_PROFILE.is_file():
        raise ToolError(refusal)
    command = ['/usr/bin/sandbox-exec', '-D', 'TEMP=' + temp, '-f', str(DEPOT_PROFILE), '/usr/bin/sips', *args]
    result = subprocess.run(command, capture_output=True, timeout=60)
    if result.returncode:
        raise ToolError(refusal)
    return result.stdout


def _dimensions(temp, refusal, path):
    text = _sips(temp, refusal, '-g', 'pixelWidth', '-g', 'pixelHeight', str(path))
    values = [re.findall(rb'\b' + key + rb':\s*(\d+)\b', text) for key in (b'pixelWidth', b'pixelHeight')]
    if any(len(v) != 1 or int(v[0]) <= 0 for v in values):
        raise ToolError(refusal)
    return tuple(int(v[0]) for v in values)


def _private_copy(temp):
    return tempfile.TemporaryDirectory(prefix=DEPOT_PREFIX, dir=temp)


def _check_readable(data, ext):
    """What facture_voir could not show is refused now, not after it has left for Google. The file itself is not changed."""
    try:
        temp = _temp()
        with _private_copy(temp) as folder:
            source = Path(folder) / ('source' + ext)
            source.write_bytes(data)
            _dimensions(temp, UNREADABLE, source)
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        raise ToolError(UNREADABLE) from None


def _convert(data):
    try:
        temp = _temp()
        with _private_copy(temp) as folder:
            source, out = Path(folder) / 'source.heic', Path(folder) / 'facture.jpg'
            source.write_bytes(data)
            before = _dimensions(temp, CONVERSION_REFUSED, source)
            _sips(temp, CONVERSION_REFUSED, '-s', 'format', 'jpeg', '-s', 'formatOptions', '85', str(source), '--out', str(out))
            with out.open('rb') as handle:
                converted = handle.read(MAX_BYTES + 1)
            if _kind(converted) != '.jpg':
                raise ToolError(CONVERSION_REFUSED)
            out.write_bytes(_without_gps(converted))
            if _dimensions(temp, CONVERSION_REFUSED, out) != before:
                raise ToolError(CONVERSION_REFUSED)
            return out.read_bytes()
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        raise ToolError(CONVERSION_REFUSED) from None


def purge_deposit_copies(older_than=3600):
    """The private copies of a logic stopped during a conversion (the HEIC still holds its position)."""
    limit = time.time() - older_than
    try:
        with os.scandir(_temp()) as entries:
            for entry in entries:
                try:
                    if (entry.name.startswith(DEPOT_PREFIX) and entry.is_dir(follow_symlinks=False)
                            and entry.stat(follow_symlinks=False).st_mtime < limit):
                        shutil.rmtree(entry.path, ignore_errors=True)
                except OSError:
                    pass  # another conversion just ended
    except (OSError, ValueError, TypeError):
        pass  # never stops the app; the next start tries again


def _name_taken(name):
    held = None
    try:
        held = _hold(INBOX)
        os.stat(name, dir_fd=held, follow_symlinks=False)
        return True
    except FileNotFoundError:
        return False
    except (OSError, ToolError):
        raise ToolError(DEPOSIT_REFUSED) from None
    finally:
        if held is not None:
            os.close(held)


def deposer(octets):
    """Deposit a chosen file, without a database or a window (D104). Only generated names reach the journal."""
    from decimal import Decimal
    from types import SimpleNamespace
    from socle import journal_ecritures as journal
    ext = _kind(octets)
    converted = ext == '.heic'
    if converted:
        octets, ext = _convert(octets), '.jpg'
    else:
        _check_readable(octets, ext)
    base = datetime.now().strftime('%Y-%m-%d_%H%M%S')
    for rank in ('', '-2', '-3', '-4', '-5', '-6', '-7', '-8', '-9'):
        name = base + rank + ext
        if _name_taken(name):
            continue
        try:
            ident = journal.intention('facture_deposer', None,
                SimpleNamespace(mode='go', raisons=[], montant=Decimal('0.00')),
                [{'numero': 1, 'operation': 'facture_deposer', 'resume': f'{INBOX_NAME}/{name}'}], origine='app')
        except journal.JournalIndisponible:
            raise ToolError(journal.INDISPONIBLE) from None
        held, created = None, False
        try:
            held = _hold(INBOX)
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=held)
            created = True
            with os.fdopen(fd, 'wb') as handle:
                handle.write(octets)
                handle.flush()
                os.fsync(handle.fileno())
        except (OSError, ToolError) as exc:
            if created:
                os.unlink(name, dir_fd=held)
            collision = isinstance(exc, FileExistsError) and not created
            journal.resultat(ident, 'refuse', 'nom déjà pris' if collision else None)
            if collision:
                continue
            raise ToolError(DEPOSIT_REFUSED) from None
        finally:
            if held is not None:
                os.close(held)
        warning = journal.resultat(ident, 'enregistre')
        return {'depose': True, 'nom': name, 'converti': converted, **({'avertissement': warning} if warning else {})}
    raise ToolError('Trop de dépôts dans la même seconde; réessaie.')
