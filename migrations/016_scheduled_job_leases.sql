CREATE TABLE scheduled_job_leases (
    job_name TEXT PRIMARY KEY,
    ownership_token TEXT NOT NULL,
    lease_expires_at TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('running','complete','failed')),
    result_metadata TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT
);
