"""Private S3 backup encryption, verification, retention, and restore helpers."""
from __future__ import annotations

import base64, hashlib, json, os, re, shutil, sqlite3, tarfile, tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from backups import APPLICATION_NAME, BackupError, DATABASE_MEMBER, create_backup

MAGIC = b"VOPS-BACKUP\x01"
OBJECT_RE = re.compile(r"^vintage-ops-(\d{4}-\d{2}-\d{2})\.tar\.gz\.enc$")
SAFE_SUMMARY = {"status", "date", "disposition", "verified_size", "checksum_status", "retained", "deleted"}

class BackupStorageError(BackupError):
    """Safe remote-backup failure."""


def decode_key(value: str) -> bytes:
    try: key=base64.urlsafe_b64decode(value.encode("ascii"))
    except Exception as exc: raise BackupStorageError("Backup encryption configuration is invalid") from exc
    if len(key)!=32: raise BackupStorageError("Backup encryption configuration is invalid")
    return key


def sha256_file(path):
    digest=hashlib.sha256()
    with open(path,"rb") as stream:
        for block in iter(lambda:stream.read(1024*1024),b""): digest.update(block)
    return digest.hexdigest()


def encrypt_archive(source, destination, encoded_key):
    """Encrypt a completed archive in a versioned AES-256-GCM envelope."""
    source,destination=Path(source),Path(destination); nonce=os.urandom(12)
    plaintext=source.read_bytes(); encrypted=AESGCM(decode_key(encoded_key)).encrypt(nonce,plaintext,MAGIC)
    destination.write_bytes(MAGIC+nonce+encrypted); os.chmod(destination,0o600)
    return {"plaintext_sha256":hashlib.sha256(plaintext).hexdigest(),
            "encrypted_sha256":sha256_file(destination),"size":destination.stat().st_size}


def decrypt_archive(source, destination, encoded_key):
    payload=Path(source).read_bytes()
    if not payload.startswith(MAGIC) or len(payload)<len(MAGIC)+12+16: raise BackupStorageError("Backup envelope is invalid")
    offset=len(MAGIC); nonce=payload[offset:offset+12]
    try: plaintext=AESGCM(decode_key(encoded_key)).decrypt(nonce,payload[offset+12:],MAGIC)
    except Exception as exc: raise BackupStorageError("Backup authentication failed") from exc
    Path(destination).write_bytes(plaintext); os.chmod(destination,0o600)
    return hashlib.sha256(plaintext).hexdigest()


class S3BackupStorage:
    def __init__(self,cfg,client=None):
        self.bucket=cfg.backup_s3_bucket; self.prefix=cfg.backup_s3_prefix.strip("/")
        if not self.prefix or ".." in self.prefix.split("/"): raise BackupStorageError("Backup prefix is invalid")
        if client is None:
            import boto3
            client=boto3.client("s3",endpoint_url=cfg.backup_s3_endpoint,
                region_name=cfg.backup_s3_region,aws_access_key_id=cfg.backup_s3_access_key_id,
                aws_secret_access_key=cfg.backup_s3_secret_access_key)
        self.client=client
    def key_for_date(self,date): return f"{self.prefix}/{APPLICATION_NAME}-{date}.tar.gz.enc"
    def _valid_key(self,key): return key.startswith(self.prefix+"/") and "/" not in key[len(self.prefix)+1:] and bool(OBJECT_RE.fullmatch(key.rsplit("/",1)[-1]))
    def head(self,key): return self.client.head_object(Bucket=self.bucket,Key=key)
    def verify(self,key,expected):
        try: head=self.head(key)
        except Exception as exc: raise BackupStorageError("Remote backup verification failed") from exc
        metadata={k.lower():v for k,v in head.get("Metadata",{}).items()}
        if head.get("ContentLength")!=expected["size"]: raise BackupStorageError("Remote backup size verification failed")
        if metadata.get("encrypted-sha256")!=expected["encrypted_sha256"] or metadata.get("plaintext-sha256")!=expected["plaintext_sha256"]:
            raise BackupStorageError("Remote backup checksum verification failed")
        return head["ContentLength"]
    def upload_verified(self,path,date,metadata):
        key=self.key_for_date(date); disposition="created"
        try:
            existing=self.head(key)
        except Exception as exc:
            response=getattr(exc,"response",{})
            if str(response.get("Error",{}).get("Code")) not in {"404","NoSuchKey","NotFound"}: raise BackupStorageError("Remote backup lookup failed") from exc
        else:
            em={k.lower():v for k,v in existing.get("Metadata",{}).items()}
            if (existing.get("ContentLength")==metadata["size"] and em.get("encrypted-sha256")==metadata["encrypted_sha256"] and em.get("plaintext-sha256")==metadata["plaintext_sha256"]):
                return key,"existing",existing["ContentLength"]
            raise BackupStorageError("Existing remote backup does not match")
        try:
            with open(path,"rb") as body:
                self.client.put_object(Bucket=self.bucket,Key=key,Body=body,IfNoneMatch="*",
                    Metadata={"encrypted-sha256":metadata["encrypted_sha256"],"plaintext-sha256":metadata["plaintext_sha256"],"format":"v1"})
        except Exception as exc: raise BackupStorageError("Remote backup upload failed") from exc
        return key,disposition,self.verify(key,metadata)
    def objects(self):
        token=None
        while True:
            kwargs={"Bucket":self.bucket,"Prefix":self.prefix+"/"}
            if token: kwargs["ContinuationToken"]=token
            try: page=self.client.list_objects_v2(**kwargs)
            except Exception as exc: raise BackupStorageError("Remote retention listing failed") from exc
            for item in page.get("Contents",[]):
                if self._valid_key(item.get("Key","")): yield item
            if not page.get("IsTruncated"): break
            token=page.get("NextContinuationToken")
            if not token: raise BackupStorageError("Remote retention pagination failed")
    def retain(self,daily=7,weekly=4):
        parsed=[]
        for item in self.objects():
            key=item["Key"]; match=OBJECT_RE.fullmatch(key.rsplit("/",1)[-1])
            try: day=datetime.strptime(match.group(1),"%Y-%m-%d").replace(tzinfo=timezone.utc)
            except (ValueError,AttributeError): continue
            parsed.append((day,key))
        parsed.sort(reverse=True); keep=set(); days=[]
        for day,key in parsed:
            if day.date() not in days and len(days)<daily: days.append(day.date()); keep.add(key)
        weeks=[]
        for day,key in parsed:
            week=day.isocalendar()[:2]
            if week not in weeks and len(weeks)<weekly: weeks.append(week); keep.add(key)
        removed=0
        for _,key in parsed:
            if key in keep: continue
            if not self._valid_key(key): raise BackupStorageError("Remote retention key escaped prefix")
            try: self.client.delete_object(Bucket=self.bucket,Key=key)
            except Exception as exc: raise BackupStorageError("Remote retention deletion failed") from exc
            removed+=1
        return len(keep),removed


def orchestrate_backup(cfg,db,storage=None,now=None):
    """Create, encrypt, upload, verify, clean up, then apply remote retention."""
    when=(now or datetime.now(timezone.utc)).astimezone(timezone.utc); date=when.date().isoformat()
    storage=storage or S3BackupStorage(cfg); plain=encrypted=None
    try:
        plain,_=create_backup(cfg.database_path,cfg.upload_directory,cfg.backup_directory,now=when)
        encrypted=Path(str(plain)+".enc")
        meta=encrypt_archive(plain,encrypted,cfg.backup_encryption_key)
        _,disposition,size=storage.upload_verified(encrypted,date,meta)
        plain.unlink(); encrypted.unlink(); plain=encrypted=None
        retained,deleted=storage.retain(cfg.backup_daily_retention,cfg.backup_weekly_retention)
        return {"status":"ok","date":date,"disposition":disposition,"verified_size":size,
                "checksum_status":"verified","retained":retained,"deleted":deleted}
    except BackupError: raise
    except Exception as exc: raise BackupStorageError("Scheduled backup failed") from exc


def _safe_destination(destination,cfg):
    raw=Path(destination).expanduser()
    if raw.is_symlink(): raise BackupStorageError("Restore destination is unsafe")
    dest=raw.resolve(strict=False)
    forbidden=[Path("/"),Path(cfg.database_path).resolve(),Path(cfg.upload_directory).resolve(),Path("/data")]
    if any(dest==p or (p==Path("/data") and (dest==p or p in dest.parents)) for p in forbidden): raise BackupStorageError("Restore destination is unsafe")
    if dest.exists() and (not dest.is_dir() or any(dest.iterdir())): raise BackupStorageError("Restore destination must be an empty directory")
    return dest


def restore_backup(cfg,object_key,destination,storage=None):
    storage=storage or S3BackupStorage(cfg)
    if not storage._valid_key(object_key): raise BackupStorageError("Remote backup key is invalid")
    dest=_safe_destination(destination,cfg); parent=dest.parent; parent.mkdir(parents=True,exist_ok=True)
    work=Path(tempfile.mkdtemp(prefix=".restore-",dir=parent)); encrypted=work/"object.enc"; archive=work/"archive.tar.gz"; staged=work/"validated"
    try:
        head=storage.head(object_key); metadata={k.lower():v for k,v in head.get("Metadata",{}).items()}
        storage.client.download_file(storage.bucket,object_key,str(encrypted))
        if encrypted.stat().st_size!=head.get("ContentLength") or sha256_file(encrypted)!=metadata.get("encrypted-sha256"): raise BackupStorageError("Downloaded backup checksum verification failed")
        if decrypt_archive(encrypted,archive,cfg.backup_encryption_key)!=metadata.get("plaintext-sha256"): raise BackupStorageError("Plaintext backup checksum verification failed")
        staged.mkdir(mode=0o700)
        with tarfile.open(archive,"r:gz") as tar:
            members=tar.getmembers(); names={m.name for m in members}
            if DATABASE_MEMBER not in names or "manifest.json" not in names: raise BackupStorageError("Backup manifest is invalid")
            for member in members:
                p=PurePosixPath(member.name)
                if p.is_absolute() or ".." in p.parts or "\\" in member.name or not member.isfile(): raise BackupStorageError("Backup archive member is unsafe")
                if member.name not in {DATABASE_MEMBER,"manifest.json"} and not member.name.startswith("uploads/"): raise BackupStorageError("Backup archive contains unexpected files")
            manifest=json.load(tar.extractfile("manifest.json"))
            photos=sorted(n[8:] for n in names if n.startswith("uploads/"))
            if manifest.get("format_version")!=2 or manifest.get("application")!=APPLICATION_NAME or manifest.get("database")!=DATABASE_MEMBER or manifest.get("integrity_check")!="ok" or manifest.get("photo_count")!=len(photos) or sorted(manifest.get("photos",[]))!=photos: raise BackupStorageError("Backup manifest is invalid")
            records=manifest.get("files")
            if not isinstance(records,list) or {r.get("path") for r in records if isinstance(r,dict)} != names-{"manifest.json"}: raise BackupStorageError("Backup file inventory is invalid")
            for member in members:
                target=staged/member.name; target.parent.mkdir(parents=True,exist_ok=True)
                target.write_bytes(tar.extractfile(member).read())
            for record in records:
                target=staged/record["path"]
                if (not isinstance(record.get("size"),int) or record["size"]<0 or
                    not re.fullmatch(r"[0-9a-f]{64}",str(record.get("sha256",""))) or
                    target.stat().st_size!=record["size"] or sha256_file(target)!=record["sha256"]):
                    raise BackupStorageError("Restored file failed manifest validation")
        with sqlite3.connect((staged/DATABASE_MEMBER).as_uri()+"?mode=ro",uri=True) as con:
            if [r[0] for r in con.execute("PRAGMA integrity_check")] != ["ok"]: raise BackupStorageError("Restored database failed integrity validation")
        if dest.exists(): dest.rmdir()
        os.replace(staged,dest); return {"status":"ok","object_key":object_key,"destination":str(dest)}
    except BackupStorageError: raise
    except Exception as exc: raise BackupStorageError("Restore failed") from exc
    finally: shutil.rmtree(work,ignore_errors=True)
