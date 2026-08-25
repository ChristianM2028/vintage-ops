#!/usr/bin/env python3
"""Volume-free, one-shot HTTPS scheduled-maintenance caller."""
import json, os, sys
import requests

SAFE_KEYS={"status","lease","expired_reservations","pages_fetched","orders_processed","duplicates","unknown_items","failure_categories"}

def main():
    url=os.getenv("SCHEDULED_JOB_URL",""); token=os.getenv("SCHEDULED_JOB_TOKEN","")
    if not url.startswith("https://") or not token:
        print("scheduled job configuration invalid",file=sys.stderr); return 2
    try:
        response=requests.post(url,headers={"Authorization":"Bearer "+token,"Accept":"application/json"},timeout=(5,240))
        data=response.json()
        if not isinstance(data,dict) or set(data)!=SAFE_KEYS:
            print("scheduled job returned an unexpected response",file=sys.stderr); return 1
        print(json.dumps(data,sort_keys=True,separators=(",",":")))
        return 0 if response.status_code==200 and data["status"]=="ok" else 1
    except (requests.RequestException,ValueError):
        print("scheduled job request failed",file=sys.stderr); return 1

if __name__=="__main__": raise SystemExit(main())
