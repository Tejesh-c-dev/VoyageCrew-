CREATE TABLE IF NOT EXISTS tripmate_users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    issuer TEXT NOT NULL,
    subject TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (issuer, subject)
);

CREATE TABLE IF NOT EXISTS tripmate_threads (
    thread_id TEXT PRIMARY KEY,
    owner_id UUID NOT NULL REFERENCES tripmate_users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
