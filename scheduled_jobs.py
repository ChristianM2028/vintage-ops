"""Bounded, leased maintenance orchestration with an allow-listed result."""
import logging

logger=logging.getLogger(__name__)
FIELDS=("status","lease","expired_reservations","pages_fetched","orders_processed","duplicates","unknown_items","failure_categories")

def run_scheduled_job(db, ebay, *, cleanup=True, reconcile=True, limit=100, lease_seconds=240, job_name="scheduled-maintenance"):
    claim=db.acquire_scheduled_job(job_name,lease_seconds)
    summary={"status":"busy" if claim["disposition"]=="busy" else "ok","lease":claim["disposition"],"expired_reservations":0,"pages_fetched":0,"orders_processed":0,"duplicates":0,"unknown_items":0,"failure_categories":[]}
    if claim["disposition"]=="busy":
        logger.info("scheduled job summary",extra={"scheduled_job":summary}); return summary
    token=claim["ownership_token"]
    try:
        if cleanup: summary["expired_reservations"]=db.expire_reservations(limit)
        if reconcile and ebay is not None:
            result=ebay.reconcile_orders(max_orders=limit)
            summary.update({"pages_fetched":result.get("pages_fetched",0),"orders_processed":result.get("processed",0),"duplicates":result.get("duplicate",0),"unknown_items":result.get("unknown",0)})
    except Exception as exc:
        summary["status"]="failed"
        category=getattr(exc,"kind",None) or "internal"
        summary["failure_categories"]=[category if category in {"remote","authentication","configuration","rate_limit","internal"} else "provider"]
    finally:
        db.finish_scheduled_job(job_name,token,summary["status"]=="ok",summary)
    logger.info("scheduled job summary",extra={"scheduled_job":summary})
    return {key:summary[key] for key in FIELDS}
