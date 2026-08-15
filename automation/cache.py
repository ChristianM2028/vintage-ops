import hashlib, json
from dataclasses import asdict
from db import now
from .models import LocatorRecipe

def fingerprint(value): return hashlib.sha256(value.strip().casefold().encode()).hexdigest()

class SelectorCacheRepository:
    def __init__(self,db,failure_limit=3): self.db=db; self.failure_limit=failure_limit
    def find(self,marketplace,form,field,route,locale,version):
        with self.db.connect() as c:
            row=c.execute("SELECT * FROM automation_selector_cache WHERE marketplace=? AND form_identifier=? AND field_key=? AND route_family=? AND locale=? AND adapter_version=? AND quarantined_at IS NULL ORDER BY generation DESC LIMIT 1",(marketplace,form,field,route,locale or "",version)).fetchone()
            if not row:return None
            result=dict(row); result["recipe"]=LocatorRecipe(**json.loads(result.pop("locator_recipe_json"))); return result
    def store_generation(self,*,marketplace,form,field,route,locale,version,recipe,candidate,kind,resolution,classification,page_build=None):
        stamp=now()
        with self.db.transaction(immediate=True) as c:
            generation=c.execute("SELECT COALESCE(MAX(generation),0)+1 FROM automation_selector_cache WHERE marketplace=? AND form_identifier=? AND field_key=? AND route_family=? AND locale=? AND adapter_version=?",(marketplace,form,field,route,locale or "",version)).fetchone()[0]
            c.execute("INSERT INTO automation_selector_cache(marketplace,form_identifier,field_key,route_family,locale,adapter_version,generation,locator_recipe_json,control_tag,control_role,control_type,accessible_name_hash,container_fingerprint,interaction_kind,resolution_confidence,classification_confidence,created_at,page_build_fingerprint) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(marketplace,form,field,route,locale or "",version,generation,json.dumps(asdict(recipe)),candidate.tag,candidate.role,candidate.input_type,fingerprint(candidate.accessible_name or ""),recipe.container_fingerprint,kind.value,resolution,classification,stamp,page_build))
    def mark(self,cache_id,success):
        with self.db.transaction() as c:
            if success: c.execute("UPDATE automation_selector_cache SET verification_success_count=verification_success_count+1,consecutive_failure_count=0,first_success_at=COALESCE(first_success_at,?),last_success_at=? WHERE id=?",(now(),now(),cache_id))
            else: c.execute("UPDATE automation_selector_cache SET consecutive_failure_count=consecutive_failure_count+1,quarantined_at=CASE WHEN consecutive_failure_count+1>=? THEN ? ELSE quarantined_at END WHERE id=?",(self.failure_limit,now(),cache_id))
