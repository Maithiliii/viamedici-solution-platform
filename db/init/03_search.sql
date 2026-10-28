-- =====================================================================
-- Hybrid search index (schema "search", invisible to Directus).
-- Owned by the agent service: rebuilt from cases/solutions by the indexer.
-- =====================================================================

CREATE SCHEMA IF NOT EXISTS search;

CREATE TABLE search.knowledge_chunks (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_type varchar(10)  NOT NULL CHECK (entity_type IN ('case', 'solution')),
    entity_id   uuid         NOT NULL,
    chunk_type  varchar(20)  NOT NULL,          -- problem | resolution | issue | fix
    content     text         NOT NULL,
    codes       text         NOT NULL DEFAULT '',  -- error codes / identifiers for trigram matching
    embedding   vector(768),
    tsv         tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
    metadata    jsonb        NOT NULL DEFAULT '{}'::jsonb,
    -- metadata: module_codes[], version, version_major, integration_codes[],
    --           status, times_applied, times_successful, number, title
    updated_at  timestamptz  NOT NULL DEFAULT now(),
    UNIQUE (entity_type, entity_id, chunk_type)
);

CREATE INDEX knowledge_chunks_embedding_idx ON search.knowledge_chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX knowledge_chunks_tsv_idx       ON search.knowledge_chunks USING gin (tsv);
CREATE INDEX knowledge_chunks_codes_idx     ON search.knowledge_chunks USING gin (codes gin_trgm_ops);
CREATE INDEX knowledge_chunks_entity_idx    ON search.knowledge_chunks (entity_type, entity_id);

-- ---------------------------------------------------------------------
-- OR-query from free text: long case descriptions must not AND every word.
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION search.or_tsquery(p_text text) RETURNS tsquery AS $$
    SELECT CASE
        WHEN coalesce(trim(p_text), '') = '' THEN NULL
        ELSE nullif(replace(plainto_tsquery('english', p_text)::text, '&', '|'), '')::tsquery
    END;
$$ LANGUAGE sql IMMUTABLE;

-- ---------------------------------------------------------------------
-- Hybrid search: vector + full-text + trigram, fused with Reciprocal Rank
-- Fusion (RRF), then boosted with structured environment similarity.
-- Returns one row per entity with a transparent score breakdown.
--
--   p_modes: which retrievers to use, e.g. '{vector,fts,trgm}'; used by
--            the evaluation harness for the ablation study.
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION search.hybrid_search(
    p_query_text      text,
    p_query_embedding vector(768),
    p_codes           text      DEFAULT '',
    p_entity_types    text[]    DEFAULT ARRAY['case', 'solution'],
    p_module_codes    text[]    DEFAULT ARRAY[]::text[],
    p_version_major   int       DEFAULT NULL,
    p_integrations    text[]    DEFAULT ARRAY[]::text[],
    p_exclude_ids     uuid[]    DEFAULT ARRAY[]::uuid[],
    p_limit           int       DEFAULT 10,
    p_modes           text[]    DEFAULT ARRAY['vector', 'fts', 'trgm'],
    p_use_boost       boolean   DEFAULT true,
    p_rrf_k           int       DEFAULT 60
)
RETURNS TABLE (
    entity_type  text,
    entity_id    uuid,
    score        double precision,
    rrf_score    double precision,
    boost        double precision,
    vector_rank  int,
    fts_rank     int,
    trgm_rank    int,
    best_chunk   text,
    metadata     jsonb,
    breakdown    jsonb
)
LANGUAGE sql STABLE AS $$
WITH params AS (
    SELECT search.or_tsquery(p_query_text) AS q
),
candidates AS (
    SELECT c.*
    FROM search.knowledge_chunks c
    WHERE c.entity_type = ANY (p_entity_types)
      AND NOT (c.entity_id = ANY (p_exclude_ids))
),
vec AS (
    SELECT entity_type, entity_id, id AS chunk_id,
           row_number() OVER (ORDER BY embedding <=> p_query_embedding) AS r
    FROM candidates
    WHERE 'vector' = ANY (p_modes) AND p_query_embedding IS NOT NULL AND embedding IS NOT NULL
    ORDER BY embedding <=> p_query_embedding
    LIMIT 60
),
fts AS (
    SELECT c.entity_type, c.entity_id, c.id AS chunk_id,
           row_number() OVER (ORDER BY ts_rank_cd(c.tsv, p.q) DESC) AS r
    FROM candidates c, params p
    WHERE 'fts' = ANY (p_modes) AND p.q IS NOT NULL AND c.tsv @@ p.q
    ORDER BY ts_rank_cd(c.tsv, p.q) DESC
    LIMIT 60
),
trg AS (
    SELECT entity_type, entity_id, id AS chunk_id,
           row_number() OVER (ORDER BY word_similarity(p_codes, codes) DESC) AS r
    FROM candidates
    WHERE 'trgm' = ANY (p_modes) AND coalesce(p_codes, '') <> '' AND codes <> ''
      AND word_similarity(p_codes, codes) > 0.35
    ORDER BY word_similarity(p_codes, codes) DESC
    LIMIT 30
),
per_entity AS (
    SELECT entity_type, entity_id,
           min(r) FILTER (WHERE src = 'vector') AS vector_rank,
           min(r) FILTER (WHERE src = 'fts')    AS fts_rank,
           min(r) FILTER (WHERE src = 'trgm')   AS trgm_rank,
           (array_agg(chunk_id ORDER BY r))[1]  AS best_chunk_id
    FROM (
        SELECT entity_type, entity_id, chunk_id, r, 'vector' AS src FROM vec
        UNION ALL SELECT entity_type, entity_id, chunk_id, r, 'fts'  FROM fts
        UNION ALL SELECT entity_type, entity_id, chunk_id, r, 'trgm' FROM trg
    ) u
    GROUP BY entity_type, entity_id
),
fused AS (
    SELECT pe.*,
           coalesce(1.0 / (p_rrf_k + pe.vector_rank), 0)
         + coalesce(1.0 / (p_rrf_k + pe.fts_rank), 0)
         + coalesce(1.0 / (p_rrf_k + pe.trgm_rank), 0) AS rrf
    FROM per_entity pe
),
meta AS (
    SELECT DISTINCT ON (c.entity_type, c.entity_id)
           c.entity_type, c.entity_id, c.metadata
    FROM search.knowledge_chunks c
    JOIN fused f ON f.entity_type = c.entity_type AND f.entity_id = c.entity_id
),
boosted AS (
    SELECT f.*, m.metadata,
           -- same module as the new case
           CASE WHEN cardinality(p_module_codes) > 0
                 AND EXISTS (SELECT 1 FROM jsonb_array_elements_text(m.metadata -> 'module_codes') x
                             WHERE x = ANY (p_module_codes))
                THEN 0.30 ELSE 0 END AS b_module,
           -- shares an integration (e.g. Shopware 6)
           CASE WHEN cardinality(p_integrations) > 0
                 AND EXISTS (SELECT 1 FROM jsonb_array_elements_text(m.metadata -> 'integration_codes') x
                             WHERE x = ANY (p_integrations))
                THEN 0.20 ELSE 0 END AS b_integration,
           -- same major version (cases) / version inside the validity range (solutions)
           CASE WHEN p_version_major IS NULL THEN 0
                WHEN f.entity_type = 'case'
                 AND (m.metadata ->> 'version_major')::int = p_version_major THEN 0.10
                WHEN f.entity_type = 'solution'
                 AND coalesce((m.metadata ->> 'min_major')::int, 0)  <= p_version_major
                 AND coalesce((m.metadata ->> 'max_major')::int, 99) >= p_version_major THEN 0.10
                ELSE 0 END AS b_version,
           -- proven solutions rank higher (KCS: reuse is review)
           CASE WHEN f.entity_type = 'solution'
                 AND coalesce((m.metadata ->> 'times_applied')::int, 0) > 0
                THEN 0.20 * (m.metadata ->> 'times_successful')::numeric
                          / (m.metadata ->> 'times_applied')::numeric
                ELSE 0 END AS b_track_record
    FROM fused f
    JOIN meta m ON m.entity_type = f.entity_type AND m.entity_id = f.entity_id
)
SELECT b.entity_type::text,
       b.entity_id,
       (b.rrf * CASE WHEN p_use_boost
                     THEN 1 + b.b_module + b.b_integration + b.b_version + b.b_track_record
                     ELSE 1 END)::double precision                                     AS score,
       b.rrf::double precision                                                          AS rrf_score,
       CASE WHEN p_use_boost THEN (b.b_module + b.b_integration + b.b_version + b.b_track_record)
            ELSE 0 END::double precision                                                AS boost,
       b.vector_rank::int, b.fts_rank::int, b.trgm_rank::int,
       (SELECT content FROM search.knowledge_chunks WHERE id = b.best_chunk_id)         AS best_chunk,
       b.metadata,
       jsonb_build_object(
           'vector_rank', b.vector_rank, 'fts_rank', b.fts_rank, 'trgm_rank', b.trgm_rank,
           'rrf', round(b.rrf::numeric, 5),
           'boost_module', b.b_module, 'boost_integration', b.b_integration,
           'boost_version', b.b_version, 'boost_track_record', round(b.b_track_record::numeric, 3)
       )                                                                               AS breakdown
FROM boosted b
ORDER BY score DESC
LIMIT p_limit;
$$;
