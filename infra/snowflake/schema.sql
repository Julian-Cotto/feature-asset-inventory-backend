-- =========================================================================
-- IT Asset Inventory: Snowflake DDL
-- Database/schema: test.test.*
-- Run as a role with USAGE on database test, schema test, and CREATE TABLE.
-- Note: Snowflake does NOT enforce FK or CHECK constraints; declared for
-- documentation, downstream tooling, and BI introspection.
-- =========================================================================

USE DATABASE test;
USE SCHEMA test.test;

-- -------------------------------------------------------------------------
-- 1. asset_statuses (lookup, extensible)
-- -------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS test.test.asset_statuses (
    code           VARCHAR(64)  NOT NULL,
    label          VARCHAR(128) NOT NULL,
    is_terminal    BOOLEAN      NOT NULL DEFAULT FALSE,
    sort_order     NUMBER(10,0) NOT NULL DEFAULT 0,
    is_active      BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at     TIMESTAMP_NTZ NOT NULL DEFAULT CURRENT_TIMESTAMP(),
    updated_at     TIMESTAMP_NTZ NOT NULL DEFAULT CURRENT_TIMESTAMP(),
    CONSTRAINT pk_asset_statuses PRIMARY KEY (code)
);

-- -------------------------------------------------------------------------
-- 2. locations (warehouses + sites, CRUD owned)
-- -------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS test.test.locations (
    id             NUMBER(38,0) NOT NULL AUTOINCREMENT START 1 INCREMENT 1,
    code           VARCHAR(64)  NOT NULL,
    name           VARCHAR(255) NOT NULL,
    type           VARCHAR(32)  NOT NULL,  -- 'warehouse' | 'site'
    address        VARCHAR(1024),
    is_active      BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at     TIMESTAMP_NTZ NOT NULL DEFAULT CURRENT_TIMESTAMP(),
    updated_at     TIMESTAMP_NTZ NOT NULL DEFAULT CURRENT_TIMESTAMP(),
    created_by_upn VARCHAR(320),
    updated_by_upn VARCHAR(320),
    CONSTRAINT pk_locations PRIMARY KEY (id),
    CONSTRAINT uq_locations_code UNIQUE (code),
    CONSTRAINT chk_locations_type CHECK (type IN ('warehouse','site'))
);

-- -------------------------------------------------------------------------
-- 3. assets (laptop / desktop / thin_client)
-- -------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS test.test.assets (
    id             NUMBER(38,0) NOT NULL AUTOINCREMENT START 1 INCREMENT 1,
    asset_tag      VARCHAR(64),
    serial_number  VARCHAR(128) NOT NULL,
    asset_type     VARCHAR(32)  NOT NULL,  -- 'laptop' | 'desktop' | 'thin_client'
    manufacturer   VARCHAR(128),
    model          VARCHAR(255),
    os             VARCHAR(64),
    os_version     VARCHAR(64),
    status_code    VARCHAR(64)  NOT NULL,
    location_id    NUMBER(38,0),
    assigned_upn   VARCHAR(320),
    assigned_at    TIMESTAMP_NTZ,
    onboarded_at   TIMESTAMP_NTZ NOT NULL DEFAULT CURRENT_TIMESTAMP(),
    archived_at    TIMESTAMP_NTZ,
    notes          VARCHAR(4000),
    created_at     TIMESTAMP_NTZ NOT NULL DEFAULT CURRENT_TIMESTAMP(),
    updated_at     TIMESTAMP_NTZ NOT NULL DEFAULT CURRENT_TIMESTAMP(),
    created_by_upn VARCHAR(320),
    updated_by_upn VARCHAR(320),
    CONSTRAINT pk_assets PRIMARY KEY (id),
    CONSTRAINT uq_assets_asset_tag UNIQUE (asset_tag),
    CONSTRAINT uq_assets_serial_number UNIQUE (serial_number),
    CONSTRAINT chk_assets_asset_type CHECK (asset_type IN ('laptop','desktop','thin_client')),
    CONSTRAINT fk_assets_status FOREIGN KEY (status_code) REFERENCES test.test.asset_statuses(code),
    CONSTRAINT fk_assets_location FOREIGN KEY (location_id) REFERENCES test.test.locations(id)
);

-- Optional clustering for large tables (uncomment when row count justifies):
-- ALTER TABLE test.test.assets CLUSTER BY (status_code, asset_type);

-- -------------------------------------------------------------------------
-- 4. asset_history (append-only audit trail)
-- -------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS test.test.asset_history (
    id                NUMBER(38,0) NOT NULL AUTOINCREMENT START 1 INCREMENT 1,
    asset_id          NUMBER(38,0) NOT NULL,
    event_type        VARCHAR(64)  NOT NULL,   -- onboard | assign | unassign | status_change | location_change | archive | note | update
    from_value        VARCHAR(1024),
    to_value          VARCHAR(1024),
    performed_by_upn  VARCHAR(320),
    performed_at      TIMESTAMP_NTZ NOT NULL DEFAULT CURRENT_TIMESTAMP(),
    notes             VARCHAR(4000),
    CONSTRAINT pk_asset_history PRIMARY KEY (id),
    CONSTRAINT fk_asset_history_asset FOREIGN KEY (asset_id) REFERENCES test.test.assets(id)
);

-- -------------------------------------------------------------------------
-- Seed: default statuses
-- -------------------------------------------------------------------------
MERGE INTO test.test.asset_statuses tgt
USING (
    SELECT 'in_warehouse' AS code, 'In Warehouse' AS label, FALSE AS is_terminal, 10 AS sort_order UNION ALL
    SELECT 'assigned',           'Assigned',         FALSE, 20 UNION ALL
    SELECT 'in_repair',          'In Repair',        FALSE, 30 UNION ALL
    SELECT 'lost',               'Lost',             TRUE,  90 UNION ALL
    SELECT 'retired',            'Retired',          TRUE,  99
) src
ON tgt.code = src.code
WHEN NOT MATCHED THEN INSERT (code, label, is_terminal, sort_order)
VALUES (src.code, src.label, src.is_terminal, src.sort_order);

-- -------------------------------------------------------------------------
-- (Optional) drop block — uncomment to teardown. Order matters (FKs).
-- -------------------------------------------------------------------------
-- DROP TABLE IF EXISTS test.test.asset_history;
-- DROP TABLE IF EXISTS test.test.assets;
-- DROP TABLE IF EXISTS test.test.locations;
-- DROP TABLE IF EXISTS test.test.asset_statuses;
