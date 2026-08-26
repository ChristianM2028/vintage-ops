import json, tarfile
from datetime import datetime, timezone
from pathlib import Path
import pytest

from backups import create_backup
from operational import (AllowListJsonFormatter, InstanceOwnershipError,
                         acquire_instance_ownership, calculate_alert_state,
                         validate_persistent_paths)
from privacy import anonymize_expired


def test_mount_validation_and_instance_ownership(tmp_path):
    validate_persistent_paths('/data/app/db.sqlite','/data/app/uploads')
    with pytest.raises(RuntimeError): validate_persistent_paths(tmp_path/'db','/data/uploads')
    first=acquire_instance_ownership(tmp_path/'db.sqlite','first')
    with pytest.raises(InstanceOwnershipError): acquire_instance_ownership(tmp_path/'db.sqlite','second')
    first.close()


def test_manifest_has_size_hash_and_media_is_restorable(tmp_path):
    import sqlite3
    db=tmp_path/'db.sqlite'; uploads=tmp_path/'uploads'; backups=tmp_path/'backups'
    uploads.mkdir(); (uploads/'photo.jpg').write_bytes(b'photo-generation')
    with sqlite3.connect(db) as con: con.execute('create table example(id integer)')
    archive,_=create_backup(db,uploads,backups,now=datetime(2026,1,1,tzinfo=timezone.utc))
    with tarfile.open(archive) as tar:
        manifest=json.load(tar.extractfile('manifest.json'))
        assert {r['path'] for r in manifest['files']} == {'database.sqlite3','uploads/photo.jpg'}
        assert all(r['size'] >= 0 and len(r['sha256']) == 64 for r in manifest['files'])


def test_anonymization_is_bounded_and_idempotent(order_factory):
    _,db,_,public_id,_=order_factory(sold=True)
    with db.transaction(immediate=True) as con:
        con.execute("UPDATE storefront_orders SET created_at='2020-01-01',customer_email='private@example.test',shipping_name='Private' WHERE public_id=?",(public_id,))
    assert anonymize_expired(db,retention_days=30,limit=1)==1
    assert anonymize_expired(db,retention_days=30,limit=1)==0
    with db.connect() as con:
        row=con.execute('SELECT customer_email,shipping_name,anonymized_at FROM storefront_orders WHERE public_id=?',(public_id,)).fetchone()
        assert row['customer_email'] is None and row['shipping_name'] is None and row['anonymized_at']


def test_structured_logging_and_alerts_are_safe():
    encoded=AllowListJsonFormatter.encode(signal='checkout_failure',count=2,email='private@example.test',token='secret')
    assert 'private' not in encoded and 'secret' not in encoded
    assert calculate_alert_state({'backup_age':27},{'backup_age':26}) == {'backup_age':True}
