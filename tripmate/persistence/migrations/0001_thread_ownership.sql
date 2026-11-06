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

ALTER TABLE tripmate_users ALTER COLUMN issuer DROP NOT NULL;
ALTER TABLE tripmate_users ALTER COLUMN subject DROP NOT NULL;
CREATE TABLE IF NOT EXISTS tripmate_accounts (
    user_id UUID PRIMARY KEY REFERENCES tripmate_users(id) ON DELETE CASCADE,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS tripmate_sessions (
    token_hash TEXT PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES tripmate_users(id) ON DELETE CASCADE,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS tripmate_sessions_expiry ON tripmate_sessions (expires_at);
