import json
import asyncio
import pytest
from automation.cache import SelectorCacheRepository
from automation.diagnostics import redact
from automation.exceptions import AmbiguousField, LowResolutionConfidence
from automation.models import (CandidateEvidence,FieldCandidate,FieldSpec,FieldValue,
    InteractionKind,LocatorRecipe)
from automation.orchestration import BrowserCommandAdapter
from automation.repository import AutomationRepository
from automation.resolver import ResolverConfig,SemanticResolver,alias_matches,normalize_label
from automation.worker import InProcessAutomationQueue
from automation.context import EncryptedStorageRepository
from cryptography.fernet import Fernet
from db import Database

def spec(): return FieldSpec("brand",("Brand","Designer"),FieldValue(text="Levi's"),expected_kinds=frozenset({InteractionKind.COMBOBOX}))
def candidate(name,*weights):
    return FieldCandidate(LocatorRecipe("label",name),"input",role="combobox",accessible_name=name,evidence=tuple(CandidateEvidence(str(i),str(i),w) for i,w in enumerate(weights)))

def test_normalization_is_unicode_aware_and_short_aliases_are_exact():
    assert normalize_label("  Levi’s! ")=="levis"
    assert alias_matches("Brand","brand")
    assert not alias_matches("Size","Plus Size")

def test_resolver_requires_threshold_and_margin():
    resolver=SemanticResolver()
    top,_=resolver.choose([candidate("Brand",40,35,15)],spec())
    assert top.score==.9
    with pytest.raises(LowResolutionConfidence): resolver.choose([candidate("Brand",40,15)],spec())
    with pytest.raises(AmbiguousField): resolver.choose([candidate("Brand",40,35,15),candidate("Brand",40,35,10)],spec())

def test_redaction_removes_secrets_paths_and_descriptions():
    safe=redact({"password":"secret","description":"personal text","photo":"/home/alice/private/a.jpg","message":"Bearer abc"})
    encoded=json.dumps(safe)
    assert "secret" not in encoded and "personal text" not in encoded and "/home/alice" not in encoded and "Bearer abc" not in encoded

@pytest.fixture
def db(tmp_path):
    value=Database(str(tmp_path/"db.sqlite")); value.migrate(); return value

def item(db): return db.create_item(title="Polo",description="Used polo",price="10",currency="USD",quantity=1,condition="Pre-owned - Good",category="Clothing > Polos",photo_urls=[])

def test_command_boundary_creates_durable_run_before_queue(db):
    queue=InProcessAutomationQueue(); value=BrowserCommandAdapter("ebay",db,queue).list(item(db)); job=queue.claim()
    run=AutomationRepository(db).get_run(job.run_id)
    assert value["automation_state"]=="queued" and run["state"]=="queued" and run["item_id"]==value["id"]

def test_repository_checkpoints_and_redacts_errors(db):
    repo=AutomationRepository(db); run=repo.create_run(item(db)["id"],"ebay","list")
    repo.record_field(run["id"],"title","verified",resolution=.9,classification=.98)
    repo.set_run_state(run["id"],"failed",{"password":"nope"})
    persisted=repo.get_run(run["id"])
    assert persisted["checkpoint_field"]=="title" and "nope" not in persisted["safe_error_json"]

def test_cache_generations_and_quarantine(db):
    cache=SelectorCacheRepository(db,failure_limit=2); c=candidate("Brand",40,35,15)
    args=dict(marketplace="ebay",form="create",field="brand",route="listing_form",locale="en-US",version="v1",recipe=c.recipe,candidate=c,kind=InteractionKind.COMBOBOX,resolution=.9,classification=.98)
    cache.store_generation(**args); hit=cache.find("ebay","create","brand","listing_form","en-US","v1")
    cache.mark(hit["id"],False); cache.mark(hit["id"],False)
    assert cache.find("ebay","create","brand","listing_form","en-US","v1") is None

def test_browser_storage_state_is_encrypted_and_revocable(db):
    storage=EncryptedStorageRepository(db,Fernet.generate_key().decode())
    state={"cookies":[{"name":"session","value":"private"}],"origins":[]}
    asyncio.run(storage.save_encrypted("ebay",state))
    with db.connect() as c: raw=c.execute("SELECT encrypted_storage_state FROM marketplace_accounts").fetchone()[0]
    assert b"private" not in raw and asyncio.run(storage.load_encrypted("ebay"))==state
    asyncio.run(storage.revoke("ebay")); assert asyncio.run(storage.load_encrypted("ebay")) is None
