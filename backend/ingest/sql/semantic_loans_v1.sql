-- semantic_loans_v1: the versioned semantic view over silver (design 3.3).
--
-- Contract. The connection must expose two relations before this file is executed:
--   silver_loans(loan_id, lat, lon, geocode_status, bridge_score, is_bridge_loan,
--                classifier_version, signals, signals_unevaluable, last_ingested_at)
--   silver_instruments(instrument_id, doc_id, doc_type, town, address_norm, grantor, lender_norm,
--                lender_type, principal, recorded_date, maturity_date, term_months, recorded_book,
--                recorded_page, source_url, fields_json, ...)
-- (an Iceberg attach, registered Arrow tables or Parquet views all work).
--
-- DuckDB views do not persist across connections, so every consumer (publish, WU7, WU10) creates
-- the views from this file on each connection. v1 == silver loans: one row per loan, joined to
-- its mortgage instrument for the display columns. Changing the output columns requires a new
-- file (semantic_loans_v2.sql), never an edit of this one.

-- PII-free list view; column order is the published list Parquet schema.
CREATE OR REPLACE VIEW semantic_loans_v1 AS
SELECT
    l.loan_id,
    i.address_norm,
    i.lender_norm AS lender,
    i.lender_type,
    i.principal,
    i.recorded_date,
    i.maturity_date,
    i.term_months,
    l.bridge_score,
    l.is_bridge_loan,
    l.classifier_version,
    l.signals,
    l.geocode_status,
    l.lat,
    l.lon,
    i.town,
    l.signals_unevaluable,
    l.last_ingested_at
FROM silver_loans AS l
JOIN silver_instruments AS i ON i.instrument_id = l.loan_id;

-- Borrower (PII) detail view: never read by the list code path.
CREATE OR REPLACE VIEW semantic_loans_v1_borrower AS
SELECT
    l.loan_id,
    i.grantor AS borrower,
    i.doc_id,
    i.recorded_book,
    i.recorded_page,
    i.source_url,
    i.fields_json
FROM silver_loans AS l
JOIN silver_instruments AS i ON i.instrument_id = l.loan_id;
