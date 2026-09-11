-- Synthetic consumer projection only, not a producer migration or schema copy.
-- Native JSON, UTC DATETIME(3) and Decimal widths exercise MySQL 5.7 / 8.4.
-- Relations deliberately have no foreign keys so tests can insert broken joins.
CREATE TABLE v2_brand (
  id BIGINT UNSIGNED PRIMARY KEY, code VARCHAR(64) NOT NULL,
  name_zh VARCHAR(128) NOT NULL, status VARCHAR(16) NOT NULL
) ENGINE=InnoDB;
CREATE TABLE v2_category (
  id BIGINT UNSIGNED PRIMARY KEY, code VARCHAR(64) NOT NULL,
  attribute_profile_code VARCHAR(64) NOT NULL, attribute_profile_version VARCHAR(32) NOT NULL,
  enabled BOOLEAN NOT NULL
) ENGINE=InnoDB;
CREATE TABLE v2_catalog_item (
  id BIGINT UNSIGNED PRIMARY KEY, category_id BIGINT UNSIGNED NOT NULL,
  brand_id BIGINT UNSIGNED, canonical_key VARCHAR(64) NOT NULL,
  item_type VARCHAR(24) NOT NULL, name VARCHAR(255) NOT NULL,
  series_name VARCHAR(128), model_number VARCHAR(128), base_attributes JSON NOT NULL,
  status VARCHAR(24) NOT NULL
) ENGINE=InnoDB;
CREATE TABLE v2_item_variant (
  id BIGINT UNSIGNED PRIMARY KEY, catalog_item_id BIGINT UNSIGNED NOT NULL,
  variant_key VARCHAR(64) NOT NULL, name VARCHAR(255) NOT NULL,
  manufacturer_part_number VARCHAR(128), condition_code VARCHAR(24) NOT NULL,
  measure_type VARCHAR(16) NOT NULL, quantity_value NUMERIC(20,6),
  quantity_min NUMERIC(20,6), quantity_max NUMERIC(20,6),
  base_unit VARCHAR(16) NOT NULL, package_count INT UNSIGNED,
  attributes JSON NOT NULL, status VARCHAR(24) NOT NULL
) ENGINE=InnoDB;
CREATE TABLE v2_source_channel (
  id BIGINT UNSIGNED PRIMARY KEY, code VARCHAR(64) NOT NULL,
  name VARCHAR(128) NOT NULL, source_type VARCHAR(32) NOT NULL,
  business_mode VARCHAR(24) NOT NULL, allowed_domains JSON NOT NULL,
  currency VARCHAR(3) NOT NULL, enabled BOOLEAN NOT NULL
) ENGINE=InnoDB;
CREATE TABLE v2_merchant (
  id BIGINT UNSIGNED PRIMARY KEY, source_channel_id BIGINT UNSIGNED NOT NULL,
  name VARCHAR(255) NOT NULL, seller_type VARCHAR(32) NOT NULL,
  verification_status VARCHAR(24) NOT NULL, status VARCHAR(16) NOT NULL
) ENGINE=InnoDB;
CREATE TABLE v2_source_listing (
  id BIGINT UNSIGNED PRIMARY KEY, source_channel_id BIGINT UNSIGNED NOT NULL,
  merchant_id BIGINT UNSIGNED NOT NULL, external_product_id VARCHAR(128),
  external_sku_id VARCHAR(128), price_nature VARCHAR(32) NOT NULL,
  canonical_url VARCHAR(1024) NOT NULL, current_revision_id BIGINT UNSIGNED,
  lifecycle_status VARCHAR(24) NOT NULL
) ENGINE=InnoDB;
CREATE TABLE v2_crawl_run (
  id BIGINT UNSIGNED PRIMARY KEY, source_channel_id BIGINT UNSIGNED NOT NULL,
  status VARCHAR(24) NOT NULL, started_at DATETIME(3) NOT NULL,
  finished_at DATETIME(3)
) ENGINE=InnoDB;
CREATE TABLE v2_crawl_record (
  id BIGINT UNSIGNED PRIMARY KEY, crawl_run_id BIGINT UNSIGNED NOT NULL,
  source_listing_id BIGINT UNSIGNED, entity_type VARCHAR(32) NOT NULL,
  entity_key VARCHAR(255) NOT NULL, request_url VARCHAR(1024) NOT NULL,
  final_url VARCHAR(1024) NOT NULL, fetch_status VARCHAR(24) NOT NULL,
  parse_status VARCHAR(24) NOT NULL, validation_status VARCHAR(24) NOT NULL,
  fetched_at DATETIME(3) NOT NULL
) ENGINE=InnoDB;
CREATE TABLE v2_listing_revision (
  id BIGINT UNSIGNED PRIMARY KEY, source_listing_id BIGINT UNSIGNED NOT NULL,
  first_crawl_record_id BIGINT UNSIGNED NOT NULL, source_title VARCHAR(512) NOT NULL,
  source_attributes JSON NOT NULL, normalized_attributes JSON NOT NULL,
  condition_code VARCHAR(24) NOT NULL, measure_type VARCHAR(16) NOT NULL,
  base_quantity_value NUMERIC(20,6), base_quantity_min NUMERIC(20,6),
  base_quantity_max NUMERIC(20,6), base_unit VARCHAR(16), package_count INT UNSIGNED,
  normalizer_version VARCHAR(64) NOT NULL, quality_status VARCHAR(24) NOT NULL,
  rejection_code VARCHAR(64)
) ENGINE=InnoDB;
CREATE TABLE v2_listing_match (
  id BIGINT UNSIGNED PRIMARY KEY, listing_revision_id BIGINT UNSIGNED NOT NULL,
  item_variant_id BIGINT UNSIGNED NOT NULL, match_status VARCHAR(24) NOT NULL,
  effective_from DATETIME(3) NOT NULL, effective_to DATETIME(3),
  INDEX ix_test_match_revision (listing_revision_id, match_status, effective_to)
) ENGINE=InnoDB;
CREATE TABLE v2_price_observation (
  id BIGINT UNSIGNED PRIMARY KEY, source_listing_id BIGINT UNSIGNED NOT NULL,
  listing_revision_id BIGINT UNSIGNED NOT NULL, crawl_record_id BIGINT UNSIGNED NOT NULL,
  supersedes_observation_id BIGINT UNSIGNED, region_scope VARCHAR(24) NOT NULL,
  region_code VARCHAR(32) NOT NULL, currency VARCHAR(3) NOT NULL,
  original_price NUMERIC(18,2), original_price_type VARCHAR(32) NOT NULL,
  current_price NUMERIC(18,2), price_nature VARCHAR(32) NOT NULL,
  price_type VARCHAR(32) NOT NULL, pricing_basis VARCHAR(24) NOT NULL,
  promotion_label VARCHAR(128), availability VARCHAR(24) NOT NULL,
  unit_price NUMERIC(20,6), unit_price_unit VARCHAR(16), fee_status VARCHAR(32) NOT NULL,
  quality_status VARCHAR(24) NOT NULL, rejection_code VARCHAR(64), observed_at DATETIME(3) NOT NULL
) ENGINE=InnoDB;
CREATE TABLE v2_price_current (
  id BIGINT UNSIGNED PRIMARY KEY, source_listing_id BIGINT UNSIGNED NOT NULL,
  listing_revision_id BIGINT UNSIGNED NOT NULL, price_observation_id BIGINT UNSIGNED NOT NULL,
  region_scope VARCHAR(24) NOT NULL, region_code VARCHAR(32) NOT NULL,
  observed_at DATETIME(3) NOT NULL,
  UNIQUE KEY uq_test_current_region (source_listing_id, region_scope, region_code)
) ENGINE=InnoDB;
