#!/usr/bin/env python3
"""Volume-free, one-shot HTTPS daily-backup caller."""
import json, os, sys
import requests

SUCCESS_KEYS={"status","date","disposition","verified_size","checksum_status","retained","deleted"}
FAILURE_STATUSES={"unauthorized","busy","configuration","upload","verification","retention"}

def main():
    url=os.getenv("BACKUP_JOB_URL",""); token=os.getenv("BACKUP_SCHEDULED_JOB_TOKEN","")
    if not url.startswith("https://") or len(token)<32:
        print("backup caller configuration invalid",file=sys.stderr); return 2
    try:
        response=requests.post(url,headers={"Authorization":"Bearer "+token,"Accept":"application/json"},timeout=(5,900))
        data=response.json()
    except (requests.RequestException,ValueError):
        print("backup request failed",file=sys.stderr); return 1
    valid=isinstance(data,dict) and ((set(data)==SUCCESS_KEYS and data.get("status")=="ok") or (set(data)=={"status"} and data.get("status") in FAILURE_STATUSES))
    if not valid:
        print("backup endpoint returned an unexpected response",file=sys.stderr); return 1
    print(json.dumps(data,sort_keys=True,separators=(",",":")))
    return 0 if response.status_code==200 and data.get("status")=="ok" else 1

if __name__=="__main__": raise SystemExit(main())
