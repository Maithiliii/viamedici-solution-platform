-- Extensions used by the Solution Enablement Platform
CREATE EXTENSION IF NOT EXISTS pgcrypto;   -- gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS vector;     -- pgvector: semantic similarity
CREATE EXTENSION IF NOT EXISTS pg_trgm;    -- trigram: fuzzy matching of error codes / identifiers
