"""Consistent, deliberately narrow application backup and retention helpers."""

from __future__ import annotations

import json
import io
import os
import re
import sqlite3
import stat
import tarfile
import tempfile
import hashlib
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from operational import media_generation_barrier


APPLICATION_NAME = "vintage-ops"
DATABASE_MEMBER = "database.sqlite3"
ARCHIVE_RE = re.compile(
    r"^vintage-ops-backup-(\d{8}T\d{6}\.\d{6}Z)\.tar\.gz$"
)


class BackupError(RuntimeError):
    """A safe-to-display backup validation or filesystem failure."""


def _resolved(path: str | os.PathLike[str], label: str) -> Path:
    if not str(path).strip():
        raise BackupError(f"Configured {label} is missing")
    try:
        return Path(path).expanduser().resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise BackupError(f"Configured {label} is invalid") from exc


def _is_root(path: Path) -> bool:
    return path == Path(path.anchor)


def _beneath(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _paths(database_path, upload_directory, backup_directory):
    database = _resolved(database_path, "database path")
    uploads = _resolved(upload_directory, "upload directory")
    backups = _resolved(backup_directory, "backup directory")
    if _is_root(backups):
        raise BackupError("Backup directory cannot be a filesystem root")
    if backups == uploads:
        raise BackupError("Backup directory cannot equal the upload directory")
    if _beneath(backups, uploads):
        raise BackupError("Backup directory cannot be inside the upload directory")
    if not database.is_file() or database.is_symlink():
        raise BackupError("Configured database is not a regular file")
    if not uploads.is_dir() or uploads.is_symlink():
        raise BackupError("Configured upload directory is not a directory")
    return database, uploads, backups


def _safe_member(name: str) -> None:
    member = PurePosixPath(name)
    if member.is_absolute() or ".." in member.parts or not name or "\\" in name:
        raise BackupError("Unsafe archive member name")


def _regular_uploads(root: Path):
    files = []
    for directory, names, filenames in os.walk(root, topdown=True, followlinks=False):
        base = Path(directory)
        names[:] = sorted(
            name for name in names
            if not (base / name).is_symlink() and (base / name).is_dir()
        )
        for name in sorted(filenames):
            path = base / name
            try:
                mode = path.lstat().st_mode
                resolved = path.resolve(strict=True)
            except (OSError, RuntimeError):
                continue
            if not stat.S_ISREG(mode) or path.is_symlink() or not _beneath(resolved, root):
                continue
            relative = path.relative_to(root).as_posix()
            member = f"uploads/{relative}"
            _safe_member(member)
            files.append((path, relative, member))
    return files


def _tar_file(archive: tarfile.TarFile, source: Path, member: str, root: Path | None = None) -> None:
    _safe_member(member)
    if source.is_symlink() or (root is not None and not _beneath(source.resolve(strict=True), root)):
        raise BackupError("Backup input changed while it was being archived")
    info = archive.gettarinfo(str(source), arcname=member)
    if not info.isfile():
        raise BackupError("Backup input changed while it was being archived")
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    info.mtime = 0
    info.mode = 0o600
    with source.open("rb") as stream:
        archive.addfile(info, stream)

def _file_record(path, member):
    digest=hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda:stream.read(1024*1024),b""): digest.update(block)
    return {"path":member,"size":path.stat().st_size,"sha256":digest.hexdigest()}


def create_backup(database_path, upload_directory, backup_directory, *, now=None):
    """Create and atomically publish a verified SQLite/uploads archive."""
    database, uploads, backups = _paths(database_path, upload_directory, backup_directory)
    timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    stamp = timestamp.strftime("%Y%m%dT%H%M%S.%fZ")
    created = timestamp.isoformat(timespec="microseconds").replace("+00:00", "Z")
    final = backups / f"{APPLICATION_NAME}-backup-{stamp}.tar.gz"
    snapshot = partial = media_stage = None
    snapshot_fd = None
    try:
        backups.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(backups, 0o700)
        if final.exists() or final.is_symlink():
            raise BackupError("A backup archive with this timestamp already exists")
        snapshot_fd, snapshot_name = tempfile.mkstemp(prefix=".snapshot-", suffix=".sqlite3", dir=backups)
        snapshot = Path(snapshot_name)
        archive_fd, archive_name = tempfile.mkstemp(prefix=".archive-", suffix=".tar.gz", dir=backups)
        os.close(archive_fd)
        partial = Path(archive_name)
        os.chmod(snapshot, 0o600)
        os.chmod(partial, 0o600)
        os.close(snapshot_fd)
        snapshot_fd = None
        with media_generation_barrier(uploads,exclusive=True):
            source_uri = database.as_uri() + "?mode=ro"
            with sqlite3.connect(source_uri, uri=True) as source, sqlite3.connect(snapshot) as target:
                source.backup(target)
            with sqlite3.connect(snapshot.as_uri() + "?mode=ro", uri=True) as check:
                rows = [row[0] for row in check.execute("PRAGMA integrity_check")]
            if rows != ["ok"]:
                raise BackupError("Database snapshot failed integrity validation")
            # Copy to an immutable generation while application publication is barred.
            photos = _regular_uploads(uploads)
            media_stage=Path(tempfile.mkdtemp(prefix=".media-generation-",dir=backups))
            staged=[]
            for path,relative,member in photos:
                target=media_stage/relative; target.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(path,target); os.chmod(target,0o600); staged.append((target,relative,member))
        photos=staged
        files=[_file_record(snapshot,DATABASE_MEMBER)]+[_file_record(path,member) for path,_,member in photos]
        manifest = {
            "format_version": 2,
            "application": APPLICATION_NAME,
            "created_at": created,
            "database": DATABASE_MEMBER,
            "integrity_check": "ok",
            "photo_count": len(photos),
            "photos": [relative for _, relative, _ in photos],
            "files": files,
        }
        with tarfile.open(partial, "w:gz") as archive:
            _tar_file(archive, snapshot, DATABASE_MEMBER)
            data = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
            info = tarfile.TarInfo("manifest.json")
            info.size, info.mode, info.mtime = len(data), 0o600, 0
            archive.addfile(info, io.BytesIO(data))
            for path, _, member in photos:
                _tar_file(archive, path, member, media_stage)
        os.chmod(partial, 0o600)
        os.replace(partial, final)
        os.chmod(final, 0o600)
        return final, {"photo_count": len(photos), "integrity_check": "ok"}
    except BackupError:
        raise
    except (OSError, sqlite3.Error, tarfile.TarError) as exc:
        raise BackupError("Backup creation failed") from exc
    finally:
        if snapshot_fd is not None:
            try:
                os.close(snapshot_fd)
            except OSError:
                pass
        if snapshot is not None:
            snapshot.unlink(missing_ok=True)
        if partial is not None:
            partial.unlink(missing_ok=True)
        if media_stage is not None:
            shutil.rmtree(media_stage,ignore_errors=True)


def prune_backups(backup_directory, *, keep_days=None, keep_count=None, dry_run=False, now=None):
    """Prune only recognized direct-child archives using one bounded policy."""
    root = _resolved(backup_directory, "backup directory")
    if _is_root(root) or not root.is_dir() or root.is_symlink():
        raise BackupError("Configured backup directory is unsafe or missing")
    if (keep_days is None) == (keep_count is None):
        raise BackupError("Specify exactly one retention policy")
    if keep_days is not None and not 1 <= keep_days <= 3650:
        raise BackupError("keep-days must be between 1 and 3650")
    if keep_count is not None and not 1 <= keep_count <= 10000:
        raise BackupError("keep-count must be between 1 and 10000")
    candidates = []
    for path in root.iterdir():
        match = ARCHIVE_RE.fullmatch(path.name)
        if not match or path.is_symlink():
            continue
        try:
            if not path.is_file() or path.resolve(strict=True).parent != root:
                continue
            created = datetime.strptime(match.group(1), "%Y%m%dT%H%M%S.%fZ").replace(tzinfo=timezone.utc)
        except (OSError, ValueError):
            continue
        candidates.append((created, path))
    candidates.sort(reverse=True)
    if keep_count is not None:
        selected = candidates[keep_count:]
    else:
        cutoff = (now or datetime.now(timezone.utc)).astimezone(timezone.utc) - timedelta(days=keep_days)
        selected = [(created, path) for created, path in candidates if created < cutoff]
    names = []
    for _, path in selected:
        try:
            resolved = path.resolve(strict=True)
            if path.parent.resolve() != root or resolved.parent != root or not _beneath(resolved, root):
                raise BackupError("Backup candidate escaped the configured directory")
            if path.is_symlink() or not path.is_file():
                continue
            names.append(path.name)
            if not dry_run:
                path.unlink()
        except BackupError:
            raise
        except OSError as exc:
            raise BackupError("Backup pruning failed") from exc
    return names
