"""Fail-closed single-instance, readiness, metrics, and safe logging helpers."""
import fcntl, json, os, sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

class InstanceOwnershipError(RuntimeError): pass

@contextmanager
def media_generation_barrier(upload_directory, *, exclusive=False):
    """Coordinate DB-referencing media writes with the short snapshot phase."""
    lock=Path(upload_directory).parent/".media-generation.lock"
    lock.parent.mkdir(parents=True,exist_ok=True)
    with open(lock,"a+b") as stream:
        os.fchmod(stream.fileno(),0o600)
        fcntl.flock(stream,fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        try: yield
        finally: fcntl.flock(stream,fcntl.LOCK_UN)

def validate_persistent_paths(database_path, upload_directory, mount="/data"):
    root=Path(mount).resolve()
    for label,value in (("database",database_path),("uploads",upload_directory)):
        path=Path(value).resolve()
        try: path.relative_to(root)
        except ValueError as exc: raise RuntimeError(f"{label} path must be beneath persistent mount") from exc
    if Path(database_path).resolve()==root or Path(upload_directory).resolve()==root:
        raise RuntimeError("persistent paths must not equal the mount root")

def acquire_instance_ownership(database_path, identity):
    """Hold an advisory lock for this process lifetime; never unlink the inode."""
    lock=Path(database_path).with_suffix(Path(database_path).suffix+".writer.lock")
    lock.parent.mkdir(parents=True,exist_ok=True); stream=open(lock,"a+",encoding="utf8")
    try: fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError as exc: stream.close(); raise InstanceOwnershipError("another independent application writer owns this database") from exc
    stream.seek(0); stream.truncate(); stream.write(json.dumps({"identity":identity,"pid":os.getpid()})); stream.flush(); os.fchmod(stream.fileno(),0o600)
    return stream

def readiness(db, cfg):
    checks={"database_read":False,"migration_state":False,"persistent_paths":False,"activation":False}
    try:
        with db.connect() as con:
            con.execute("SELECT 1").fetchone()
            expected={p.name for p in Path(__file__).with_name("migrations").glob("*.sql")}
            applied={r[0] for r in con.execute("SELECT version FROM schema_migrations")}
            checks["database_read"]=True; checks["migration_state"]=(applied==expected)
        for path in (Path(cfg.database_path).parent,Path(cfg.upload_directory)):
            probe=path/f".ready-{os.getpid()}"; probe.write_bytes(b""); probe.unlink()
        checks["persistent_paths"]=True
        checks["activation"]=(cfg.app_environment!="production" or bool(cfg.production_provider_activation_id and cfg.checkout_enabled is not None and cfg.ebay_listing_enabled is not None))
    except (OSError,sqlite3.Error): pass
    return {"status":"ready" if all(checks.values()) else "not_ready","checks":checks}

class AllowListJsonFormatter:
    ALLOWED=frozenset({"signal","category","count","age_seconds","duration_ms","status"})
    @classmethod
    def encode(cls, **values):
        return json.dumps({k:v for k,v in values.items() if k in cls.ALLOWED},sort_keys=True,separators=(",",":"))

def calculate_alert_state(metrics, thresholds):
    """Pure alert evaluation; output contains signal names only, never records."""
    return {name: bool(value is not None and value>=thresholds.get(name,float("inf"))) for name,value in metrics.items()}
