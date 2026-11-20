BEGIN;
-- Generated from data/schema-v1.json. Scope is part of every identity and relationship.

CREATE TABLE research_data."abilities" (
  "publication_id" text NOT NULL,
  "dataset_id" text NOT NULL,
  "snapshot_id" text NOT NULL,
  "ability_id" text NOT NULL,
  "unit_id" text NOT NULL,
  "variant" text NOT NULL,
  "form_source_key" text,
  "source_path" text NOT NULL,
  PRIMARY KEY ("publication_id","dataset_id","snapshot_id","ability_id"),
  FOREIGN KEY (publication_id,dataset_id,snapshot_id) REFERENCES research.publications(id,dataset_id,snapshot_id) ON DELETE CASCADE
);

ALTER TABLE research_data."abilities" ENABLE ROW LEVEL SECURITY;

CREATE POLICY published_read ON research_data."abilities" FOR SELECT TO anon,authenticated USING (EXISTS (SELECT 1 FROM research.publications p WHERE p.id=publication_id AND p.status='published'));

CREATE TABLE research_data."ability_values" (
  "publication_id" text NOT NULL,
  "dataset_id" text NOT NULL,
  "snapshot_id" text NOT NULL,
  "ability_id" text NOT NULL,
  "variable" text NOT NULL,
  "star_level" bigint NOT NULL,
  "value" double precision,
  "formula_json" text,
  "source_path" text NOT NULL,
  PRIMARY KEY ("publication_id","dataset_id","snapshot_id","ability_id","variable","star_level"),
  FOREIGN KEY (publication_id,dataset_id,snapshot_id) REFERENCES research.publications(id,dataset_id,snapshot_id) ON DELETE CASCADE
);

ALTER TABLE research_data."ability_values" ENABLE ROW LEVEL SECURITY;

CREATE POLICY published_read ON research_data."ability_values" FOR SELECT TO anon,authenticated USING (EXISTS (SELECT 1 FROM research.publications p WHERE p.id=publication_id AND p.status='published'));

CREATE TABLE research_data."assertions" (
  "publication_id" text NOT NULL,
  "dataset_id" text NOT NULL,
  "snapshot_id" text NOT NULL,
  "assertion_id" bigint NOT NULL,
  "entity_id" text,
  "field_path" text NOT NULL,
  "value_json" text NOT NULL,
  "source_id" text NOT NULL,
  "source_path" text NOT NULL,
  PRIMARY KEY ("publication_id","dataset_id","snapshot_id","assertion_id"),
  FOREIGN KEY (publication_id,dataset_id,snapshot_id) REFERENCES research.publications(id,dataset_id,snapshot_id) ON DELETE CASCADE
);

ALTER TABLE research_data."assertions" ENABLE ROW LEVEL SECURITY;

CREATE POLICY published_read ON research_data."assertions" FOR SELECT TO anon,authenticated USING (EXISTS (SELECT 1 FROM research.publications p WHERE p.id=publication_id AND p.status='published'));

CREATE TABLE research_data."assets" (
  "publication_id" text NOT NULL,
  "dataset_id" text NOT NULL,
  "snapshot_id" text NOT NULL,
  "entity_id" text NOT NULL,
  "asset_type" text NOT NULL,
  "asset_key" text NOT NULL,
  "url" text,
  "source_path" text NOT NULL,
  PRIMARY KEY ("publication_id","dataset_id","snapshot_id","entity_id","asset_type","asset_key"),
  FOREIGN KEY (publication_id,dataset_id,snapshot_id) REFERENCES research.publications(id,dataset_id,snapshot_id) ON DELETE CASCADE
);

ALTER TABLE research_data."assets" ENABLE ROW LEVEL SECURITY;

CREATE POLICY published_read ON research_data."assets" FOR SELECT TO anon,authenticated USING (EXISTS (SELECT 1 FROM research.publications p WHERE p.id=publication_id AND p.status='published'));

CREATE TABLE research_data."augments" (
  "publication_id" text NOT NULL,
  "dataset_id" text NOT NULL,
  "snapshot_id" text NOT NULL,
  "entity_id" text NOT NULL,
  "rarity" text,
  "augment_type" text,
  "rounds_json" text NOT NULL,
  "round_variants_json" text NOT NULL,
  "tags_json" text NOT NULL,
  "quest_related" bigint NOT NULL,
  PRIMARY KEY ("publication_id","dataset_id","snapshot_id","entity_id"),
  FOREIGN KEY (publication_id,dataset_id,snapshot_id) REFERENCES research.publications(id,dataset_id,snapshot_id) ON DELETE CASCADE
);

ALTER TABLE research_data."augments" ENABLE ROW LEVEL SECURITY;

CREATE POLICY published_read ON research_data."augments" FOR SELECT TO anon,authenticated USING (EXISTS (SELECT 1 FROM research.publications p WHERE p.id=publication_id AND p.status='published'));

CREATE TABLE research_data."change_events" (
  "publication_id" text NOT NULL,
  "dataset_id" text NOT NULL,
  "snapshot_id" text NOT NULL,
  "entity_id" text NOT NULL,
  "change_kind" text NOT NULL,
  "field_path" text NOT NULL,
  "before_json" text,
  "after_json" text,
  "classification" text NOT NULL,
  PRIMARY KEY ("publication_id","dataset_id","snapshot_id","entity_id","change_kind","field_path"),
  FOREIGN KEY (publication_id,dataset_id,snapshot_id) REFERENCES research.publications(id,dataset_id,snapshot_id) ON DELETE CASCADE
);

ALTER TABLE research_data."change_events" ENABLE ROW LEVEL SECURITY;

CREATE POLICY published_read ON research_data."change_events" FOR SELECT TO anon,authenticated USING (EXISTS (SELECT 1 FROM research.publications p WHERE p.id=publication_id AND p.status='published'));

CREATE TABLE research_data."collection_inventory" (
  "publication_id" text NOT NULL,
  "dataset_id" text NOT NULL,
  "snapshot_id" text NOT NULL,
  "source_id" text NOT NULL,
  "collection_path" text NOT NULL,
  "value_type" text NOT NULL,
  "row_count" bigint,
  PRIMARY KEY ("publication_id","dataset_id","snapshot_id","source_id","collection_path"),
  FOREIGN KEY (publication_id,dataset_id,snapshot_id) REFERENCES research.publications(id,dataset_id,snapshot_id) ON DELETE CASCADE
);

ALTER TABLE research_data."collection_inventory" ENABLE ROW LEVEL SECURITY;

CREATE POLICY published_read ON research_data."collection_inventory" FOR SELECT TO anon,authenticated USING (EXISTS (SELECT 1 FROM research.publications p WHERE p.id=publication_id AND p.status='published'));

CREATE TABLE research_data."condition_nodes" (
  "publication_id" text NOT NULL,
  "dataset_id" text NOT NULL,
  "snapshot_id" text NOT NULL,
  "node_id" text NOT NULL,
  "rule_id" text NOT NULL,
  "parent_id" text,
  "node_order" bigint NOT NULL,
  "operator" text NOT NULL,
  "description" text,
  "required_value" double precision,
  "milestones_json" text NOT NULL,
  "milestone_description" text,
  "implementing_ability" text,
  "raw_json" text NOT NULL,
  "source_path" text NOT NULL,
  PRIMARY KEY ("publication_id","dataset_id","snapshot_id","node_id"),
  FOREIGN KEY (publication_id,dataset_id,snapshot_id) REFERENCES research.publications(id,dataset_id,snapshot_id) ON DELETE CASCADE
);

ALTER TABLE research_data."condition_nodes" ENABLE ROW LEVEL SECURITY;

CREATE POLICY published_read ON research_data."condition_nodes" FOR SELECT TO anon,authenticated USING (EXISTS (SELECT 1 FROM research.publications p WHERE p.id=publication_id AND p.status='published'));

CREATE TABLE research_data."condition_predicates" (
  "publication_id" text NOT NULL,
  "dataset_id" text NOT NULL,
  "snapshot_id" text NOT NULL,
  "node_id" text NOT NULL,
  "tree_path" text NOT NULL,
  "subject" text NOT NULL,
  "metric" text NOT NULL,
  "comparison" text NOT NULL,
  "threshold_json" text NOT NULL,
  "unit" text,
  "scope" text,
  "qualifiers_json" text NOT NULL,
  "comparison_certainty" text NOT NULL,
  "negated" bigint NOT NULL,
  "logical_context_json" text NOT NULL,
  PRIMARY KEY ("publication_id","dataset_id","snapshot_id","node_id","tree_path"),
  FOREIGN KEY (publication_id,dataset_id,snapshot_id) REFERENCES research.publications(id,dataset_id,snapshot_id) ON DELETE CASCADE
);

ALTER TABLE research_data."condition_predicates" ENABLE ROW LEVEL SECURITY;

CREATE POLICY published_read ON research_data."condition_predicates" FOR SELECT TO anon,authenticated USING (EXISTS (SELECT 1 FROM research.publications p WHERE p.id=publication_id AND p.status='published'));

CREATE TABLE research_data."coverage_entries" (
  "publication_id" text NOT NULL,
  "dataset_id" text NOT NULL,
  "snapshot_id" text NOT NULL,
  "category" text NOT NULL,
  "observed" bigint NOT NULL,
  "expected" bigint,
  "known_fields" bigint,
  "applicable_fields" bigint,
  "status" text NOT NULL,
  "explanation" text NOT NULL,
  "source_ids_json" text NOT NULL,
  PRIMARY KEY ("publication_id","dataset_id","snapshot_id","category"),
  FOREIGN KEY (publication_id,dataset_id,snapshot_id) REFERENCES research.publications(id,dataset_id,snapshot_id) ON DELETE CASCADE
);

ALTER TABLE research_data."coverage_entries" ENABLE ROW LEVEL SECURITY;

CREATE POLICY published_read ON research_data."coverage_entries" FOR SELECT TO anon,authenticated USING (EXISTS (SELECT 1 FROM research.publications p WHERE p.id=publication_id AND p.status='published'));

CREATE TABLE research_data."curated_records" (
  "publication_id" text NOT NULL,
  "dataset_id" text NOT NULL,
  "snapshot_id" text NOT NULL,
  "record_id" text NOT NULL,
  "kind" text NOT NULL,
  "name" text NOT NULL,
  "description" text NOT NULL,
  "source_id" text NOT NULL,
  "source_locator" text NOT NULL,
  "channel" text NOT NULL,
  "mode" text NOT NULL,
  "evidence_origin" text NOT NULL,
  "corroboration_status" text NOT NULL,
  "fields_json" text NOT NULL,
  "relationships_json" text NOT NULL,
  PRIMARY KEY ("publication_id","dataset_id","snapshot_id","record_id"),
  FOREIGN KEY (publication_id,dataset_id,snapshot_id) REFERENCES research.publications(id,dataset_id,snapshot_id) ON DELETE CASCADE
);

ALTER TABLE research_data."curated_records" ENABLE ROW LEVEL SECURITY;

CREATE POLICY published_read ON research_data."curated_records" FOR SELECT TO anon,authenticated USING (EXISTS (SELECT 1 FROM research.publications p WHERE p.id=publication_id AND p.status='published'));

CREATE TABLE research_data."curated_values" (
  "publication_id" text NOT NULL,
  "dataset_id" text NOT NULL,
  "snapshot_id" text NOT NULL,
  "record_id" text NOT NULL,
  "field_path" text NOT NULL,
  "value_json" text NOT NULL,
  "source_id" text NOT NULL,
  "source_path" text NOT NULL,
  PRIMARY KEY ("publication_id","dataset_id","snapshot_id","record_id","field_path"),
  FOREIGN KEY (publication_id,dataset_id,snapshot_id) REFERENCES research.publications(id,dataset_id,snapshot_id) ON DELETE CASCADE
);

ALTER TABLE research_data."curated_values" ENABLE ROW LEVEL SECURITY;

CREATE POLICY published_read ON research_data."curated_values" FOR SELECT TO anon,authenticated USING (EXISTS (SELECT 1 FROM research.publications p WHERE p.id=publication_id AND p.status='published'));

CREATE TABLE research_data."discrepancies" (
  "publication_id" text NOT NULL,
  "dataset_id" text NOT NULL,
  "snapshot_id" text NOT NULL,
  "discrepancy_id" text NOT NULL,
  "subject" text NOT NULL,
  "description" text NOT NULL,
  "source_ids_json" text NOT NULL,
  "status" text NOT NULL,
  PRIMARY KEY ("publication_id","dataset_id","snapshot_id","discrepancy_id"),
  FOREIGN KEY (publication_id,dataset_id,snapshot_id) REFERENCES research.publications(id,dataset_id,snapshot_id) ON DELETE CASCADE
);

ALTER TABLE research_data."discrepancies" ENABLE ROW LEVEL SECURITY;

CREATE POLICY published_read ON research_data."discrepancies" FOR SELECT TO anon,authenticated USING (EXISTS (SELECT 1 FROM research.publications p WHERE p.id=publication_id AND p.status='published'));

CREATE TABLE research_data."effect_values" (
  "publication_id" text NOT NULL,
  "dataset_id" text NOT NULL,
  "snapshot_id" text NOT NULL,
  "entity_id" text NOT NULL,
  "collection" text NOT NULL,
  "variable" text NOT NULL,
  "coordinate" double precision NOT NULL,
  "value" double precision NOT NULL,
  "unit" text NOT NULL,
  "axis" text NOT NULL,
  "source_path" text NOT NULL,
  PRIMARY KEY ("publication_id","dataset_id","snapshot_id","entity_id","collection","variable","coordinate"),
  FOREIGN KEY (publication_id,dataset_id,snapshot_id) REFERENCES research.publications(id,dataset_id,snapshot_id) ON DELETE CASCADE
);

ALTER TABLE research_data."effect_values" ENABLE ROW LEVEL SECURITY;

CREATE POLICY published_read ON research_data."effect_values" FOR SELECT TO anon,authenticated USING (EXISTS (SELECT 1 FROM research.publications p WHERE p.id=publication_id AND p.status='published'));

CREATE TABLE research_data."entities" (
  "publication_id" text NOT NULL,
  "dataset_id" text NOT NULL,
  "snapshot_id" text NOT NULL,
  "entity_id" text NOT NULL,
  "kind" text NOT NULL,
  "source_key" text NOT NULL,
  "name" text NOT NULL,
  "description" text,
  "description_raw" text,
  "lifecycle_status" text NOT NULL,
  "source_id" text NOT NULL,
  "source_path" text NOT NULL,
  "raw_json" text NOT NULL,
  "variant_key" text NOT NULL,
  "evidence_origin" text NOT NULL,
  "corroboration_status" text NOT NULL,
  PRIMARY KEY ("publication_id","dataset_id","snapshot_id","entity_id"),
  FOREIGN KEY (publication_id,dataset_id,snapshot_id) REFERENCES research.publications(id,dataset_id,snapshot_id) ON DELETE CASCADE
);

ALTER TABLE research_data."entities" ENABLE ROW LEVEL SECURITY;

CREATE POLICY published_read ON research_data."entities" FOR SELECT TO anon,authenticated USING (EXISTS (SELECT 1 FROM research.publications p WHERE p.id=publication_id AND p.status='published'));

CREATE TABLE research_data."entity_aliases" (
  "publication_id" text NOT NULL,
  "dataset_id" text NOT NULL,
  "snapshot_id" text NOT NULL,
  "entity_id" text NOT NULL,
  "alias" text NOT NULL,
  "alias_kind" text NOT NULL,
  PRIMARY KEY ("publication_id","dataset_id","snapshot_id","entity_id","alias","alias_kind"),
  FOREIGN KEY (publication_id,dataset_id,snapshot_id) REFERENCES research.publications(id,dataset_id,snapshot_id) ON DELETE CASCADE
);

ALTER TABLE research_data."entity_aliases" ENABLE ROW LEVEL SECURITY;

CREATE POLICY published_read ON research_data."entity_aliases" FOR SELECT TO anon,authenticated USING (EXISTS (SELECT 1 FROM research.publications p WHERE p.id=publication_id AND p.status='published'));

CREATE TABLE research_data."entity_relationships" (
  "publication_id" text NOT NULL,
  "dataset_id" text NOT NULL,
  "snapshot_id" text NOT NULL,
  "source_entity_id" text NOT NULL,
  "target_entity_id" text NOT NULL,
  "relationship" text NOT NULL,
  "source_path" text NOT NULL,
  PRIMARY KEY ("publication_id","dataset_id","snapshot_id","source_entity_id","target_entity_id","relationship"),
  FOREIGN KEY (publication_id,dataset_id,snapshot_id) REFERENCES research.publications(id,dataset_id,snapshot_id) ON DELETE CASCADE
);

ALTER TABLE research_data."entity_relationships" ENABLE ROW LEVEL SECURITY;

CREATE POLICY published_read ON research_data."entity_relationships" FOR SELECT TO anon,authenticated USING (EXISTS (SELECT 1 FROM research.publications p WHERE p.id=publication_id AND p.status='published'));

CREATE TABLE research_data."field_gaps" (
  "publication_id" text NOT NULL,
  "dataset_id" text NOT NULL,
  "snapshot_id" text NOT NULL,
  "gap_id" bigint NOT NULL,
  "entity_id" text,
  "field" text NOT NULL,
  "reason" text NOT NULL,
  "detail" text NOT NULL,
  PRIMARY KEY ("publication_id","dataset_id","snapshot_id","gap_id"),
  FOREIGN KEY (publication_id,dataset_id,snapshot_id) REFERENCES research.publications(id,dataset_id,snapshot_id) ON DELETE CASCADE
);

ALTER TABLE research_data."field_gaps" ENABLE ROW LEVEL SECURITY;

CREATE POLICY published_read ON research_data."field_gaps" FOR SELECT TO anon,authenticated USING (EXISTS (SELECT 1 FROM research.publications p WHERE p.id=publication_id AND p.status='published'));

CREATE TABLE research_data."item_recipes" (
  "publication_id" text NOT NULL,
  "dataset_id" text NOT NULL,
  "snapshot_id" text NOT NULL,
  "result_id" text NOT NULL,
  "component_id" text NOT NULL,
  "quantity" bigint NOT NULL,
  "source_path" text NOT NULL,
  PRIMARY KEY ("publication_id","dataset_id","snapshot_id","result_id","component_id"),
  FOREIGN KEY (publication_id,dataset_id,snapshot_id) REFERENCES research.publications(id,dataset_id,snapshot_id) ON DELETE CASCADE
);

ALTER TABLE research_data."item_recipes" ENABLE ROW LEVEL SECURITY;

CREATE POLICY published_read ON research_data."item_recipes" FOR SELECT TO anon,authenticated USING (EXISTS (SELECT 1 FROM research.publications p WHERE p.id=publication_id AND p.status='published'));

CREATE TABLE research_data."items" (
  "publication_id" text NOT NULL,
  "dataset_id" text NOT NULL,
  "snapshot_id" text NOT NULL,
  "entity_id" text NOT NULL,
  "category" text NOT NULL,
  "unique_item" bigint,
  "stat_line" text,
  "tags_json" text NOT NULL,
  "collection" text NOT NULL,
  PRIMARY KEY ("publication_id","dataset_id","snapshot_id","entity_id"),
  FOREIGN KEY (publication_id,dataset_id,snapshot_id) REFERENCES research.publications(id,dataset_id,snapshot_id) ON DELETE CASCADE
);

ALTER TABLE research_data."items" ENABLE ROW LEVEL SECURITY;

CREATE POLICY published_read ON research_data."items" FOR SELECT TO anon,authenticated USING (EXISTS (SELECT 1 FROM research.publications p WHERE p.id=publication_id AND p.status='published'));

CREATE TABLE research_data."loot_entries" (
  "publication_id" text NOT NULL,
  "dataset_id" text NOT NULL,
  "snapshot_id" text NOT NULL,
  "loot_id" text NOT NULL,
  "entry_index" bigint NOT NULL,
  "reward_source_key" text,
  "quantity" double precision,
  "probability" double precision,
  "raw_json" text NOT NULL,
  "source_path" text NOT NULL,
  PRIMARY KEY ("publication_id","dataset_id","snapshot_id","loot_id","entry_index"),
  FOREIGN KEY (publication_id,dataset_id,snapshot_id) REFERENCES research.publications(id,dataset_id,snapshot_id) ON DELETE CASCADE
);

ALTER TABLE research_data."loot_entries" ENABLE ROW LEVEL SECURITY;

CREATE POLICY published_read ON research_data."loot_entries" FOR SELECT TO anon,authenticated USING (EXISTS (SELECT 1 FROM research.publications p WHERE p.id=publication_id AND p.status='published'));

CREATE TABLE research_data."loot_tables" (
  "publication_id" text NOT NULL,
  "dataset_id" text NOT NULL,
  "snapshot_id" text NOT NULL,
  "entity_id" text NOT NULL,
  "owner_source_key" text,
  "drop_table_source_key" text,
  "outcomes_status" text NOT NULL,
  PRIMARY KEY ("publication_id","dataset_id","snapshot_id","entity_id"),
  FOREIGN KEY (publication_id,dataset_id,snapshot_id) REFERENCES research.publications(id,dataset_id,snapshot_id) ON DELETE CASCADE
);

ALTER TABLE research_data."loot_tables" ENABLE ROW LEVEL SECURITY;

CREATE POLICY published_read ON research_data."loot_tables" FOR SELECT TO anon,authenticated USING (EXISTS (SELECT 1 FROM research.publications p WHERE p.id=publication_id AND p.status='published'));

CREATE TABLE research_data."mechanics" (
  "publication_id" text NOT NULL,
  "dataset_id" text NOT NULL,
  "snapshot_id" text NOT NULL,
  "entity_id" text NOT NULL,
  "mechanic_type" text NOT NULL,
  "activation_status" text NOT NULL,
  "tags_json" text NOT NULL,
  PRIMARY KEY ("publication_id","dataset_id","snapshot_id","entity_id"),
  FOREIGN KEY (publication_id,dataset_id,snapshot_id) REFERENCES research.publications(id,dataset_id,snapshot_id) ON DELETE CASCADE
);

ALTER TABLE research_data."mechanics" ENABLE ROW LEVEL SECURITY;

CREATE POLICY published_read ON research_data."mechanics" FOR SELECT TO anon,authenticated USING (EXISTS (SELECT 1 FROM research.publications p WHERE p.id=publication_id AND p.status='published'));

CREATE TABLE research_data."observation_comps" (
  "publication_id" text NOT NULL,
  "dataset_id" text NOT NULL,
  "snapshot_id" text NOT NULL,
  "comp_id" text NOT NULL,
  "model_id" bigint NOT NULL,
  "name_source_keys" text,
  "source_id" text NOT NULL,
  "source_path" text NOT NULL,
  "raw_json" text NOT NULL,
  PRIMARY KEY ("publication_id","dataset_id","snapshot_id","comp_id"),
  FOREIGN KEY (publication_id,dataset_id,snapshot_id) REFERENCES research.publications(id,dataset_id,snapshot_id) ON DELETE CASCADE
);

ALTER TABLE research_data."observation_comps" ENABLE ROW LEVEL SECURITY;

CREATE POLICY published_read ON research_data."observation_comps" FOR SELECT TO anon,authenticated USING (EXISTS (SELECT 1 FROM research.publications p WHERE p.id=publication_id AND p.status='published'));

CREATE TABLE research_data."observation_item_recommendations" (
  "publication_id" text NOT NULL,
  "dataset_id" text NOT NULL,
  "snapshot_id" text NOT NULL,
  "comp_id" text NOT NULL,
  "recommendation_index" bigint NOT NULL,
  "unit_key" text,
  "sample_count" bigint,
  "average_placement" double precision,
  "unit_numitems_count" bigint,
  "source_score" double precision,
  "placement_change" double precision,
  "evidence_origin" text NOT NULL,
  "window_status" text NOT NULL,
  "source_id" text NOT NULL,
  "source_path" text NOT NULL,
  "raw_json" text NOT NULL,
  PRIMARY KEY ("publication_id","dataset_id","snapshot_id","comp_id","recommendation_index"),
  FOREIGN KEY (publication_id,dataset_id,snapshot_id) REFERENCES research.publications(id,dataset_id,snapshot_id) ON DELETE CASCADE
);

ALTER TABLE research_data."observation_item_recommendations" ENABLE ROW LEVEL SECURITY;

CREATE POLICY published_read ON research_data."observation_item_recommendations" FOR SELECT TO anon,authenticated USING (EXISTS (SELECT 1 FROM research.publications p WHERE p.id=publication_id AND p.status='published'));

CREATE TABLE research_data."observation_memberships" (
  "publication_id" text NOT NULL,
  "dataset_id" text NOT NULL,
  "snapshot_id" text NOT NULL,
  "comp_id" text NOT NULL,
  "member_kind" text NOT NULL,
  "position" bigint NOT NULL,
  "source_key" text NOT NULL,
  "source_id" text NOT NULL,
  "source_path" text NOT NULL,
  PRIMARY KEY ("publication_id","dataset_id","snapshot_id","comp_id","member_kind","position"),
  FOREIGN KEY (publication_id,dataset_id,snapshot_id) REFERENCES research.publications(id,dataset_id,snapshot_id) ON DELETE CASCADE
);

ALTER TABLE research_data."observation_memberships" ENABLE ROW LEVEL SECURITY;

CREATE POLICY published_read ON research_data."observation_memberships" FOR SELECT TO anon,authenticated USING (EXISTS (SELECT 1 FROM research.publications p WHERE p.id=publication_id AND p.status='published'));

CREATE TABLE research_data."observation_metrics" (
  "publication_id" text NOT NULL,
  "dataset_id" text NOT NULL,
  "snapshot_id" text NOT NULL,
  "sample_id" text NOT NULL,
  "metric" text NOT NULL,
  "value" double precision,
  "unit" text NOT NULL,
  "numerator" double precision,
  "denominator" bigint,
  "evidence_origin" text NOT NULL,
  "formula" text NOT NULL,
  "source_id" text NOT NULL,
  "source_path" text NOT NULL,
  PRIMARY KEY ("publication_id","dataset_id","snapshot_id","sample_id","metric"),
  FOREIGN KEY (publication_id,dataset_id,snapshot_id) REFERENCES research.publications(id,dataset_id,snapshot_id) ON DELETE CASCADE
);

ALTER TABLE research_data."observation_metrics" ENABLE ROW LEVEL SECURITY;

CREATE POLICY published_read ON research_data."observation_metrics" FOR SELECT TO anon,authenticated USING (EXISTS (SELECT 1 FROM research.publications p WHERE p.id=publication_id AND p.status='published'));

CREATE TABLE research_data."observation_payloads" (
  "publication_id" text NOT NULL,
  "dataset_id" text NOT NULL,
  "snapshot_id" text NOT NULL,
  "source_id" text NOT NULL,
  "set_id" text NOT NULL,
  "queue_id" text NOT NULL,
  "model_id" bigint NOT NULL,
  "updated_at" text,
  "source_path" text NOT NULL,
  "raw_json" text NOT NULL,
  PRIMARY KEY ("publication_id","dataset_id","snapshot_id","source_id"),
  FOREIGN KEY (publication_id,dataset_id,snapshot_id) REFERENCES research.publications(id,dataset_id,snapshot_id) ON DELETE CASCADE
);

ALTER TABLE research_data."observation_payloads" ENABLE ROW LEVEL SECURITY;

CREATE POLICY published_read ON research_data."observation_payloads" FOR SELECT TO anon,authenticated USING (EXISTS (SELECT 1 FROM research.publications p WHERE p.id=publication_id AND p.status='published'));

CREATE TABLE research_data."observation_placements" (
  "publication_id" text NOT NULL,
  "dataset_id" text NOT NULL,
  "snapshot_id" text NOT NULL,
  "sample_id" text NOT NULL,
  "placement" bigint NOT NULL,
  "board_count" bigint NOT NULL,
  "source_id" text NOT NULL,
  "source_path" text NOT NULL,
  PRIMARY KEY ("publication_id","dataset_id","snapshot_id","sample_id","placement"),
  FOREIGN KEY (publication_id,dataset_id,snapshot_id) REFERENCES research.publications(id,dataset_id,snapshot_id) ON DELETE CASCADE
);

ALTER TABLE research_data."observation_placements" ENABLE ROW LEVEL SECURITY;

CREATE POLICY published_read ON research_data."observation_placements" FOR SELECT TO anon,authenticated USING (EXISTS (SELECT 1 FROM research.publications p WHERE p.id=publication_id AND p.status='published'));

CREATE TABLE research_data."observation_recommended_items" (
  "publication_id" text NOT NULL,
  "dataset_id" text NOT NULL,
  "snapshot_id" text NOT NULL,
  "comp_id" text NOT NULL,
  "recommendation_index" bigint NOT NULL,
  "item_position" bigint NOT NULL,
  "item_key" text NOT NULL,
  "source_id" text NOT NULL,
  "source_path" text NOT NULL,
  PRIMARY KEY ("publication_id","dataset_id","snapshot_id","comp_id","recommendation_index","item_position"),
  FOREIGN KEY (publication_id,dataset_id,snapshot_id) REFERENCES research.publications(id,dataset_id,snapshot_id) ON DELETE CASCADE
);

ALTER TABLE research_data."observation_recommended_items" ENABLE ROW LEVEL SECURITY;

CREATE POLICY published_read ON research_data."observation_recommended_items" FOR SELECT TO anon,authenticated USING (EXISTS (SELECT 1 FROM research.publications p WHERE p.id=publication_id AND p.status='published'));

CREATE TABLE research_data."observation_samples" (
  "publication_id" text NOT NULL,
  "dataset_id" text NOT NULL,
  "snapshot_id" text NOT NULL,
  "sample_id" text NOT NULL,
  "record_kind" text NOT NULL,
  "comp_id" text,
  "sample_count" bigint NOT NULL,
  "population_unit" text NOT NULL,
  "queue_id" text NOT NULL,
  "requested_patch" text NOT NULL,
  "requested_window_days" bigint NOT NULL,
  "exact_window_start" text,
  "exact_window_end" text,
  "rank_filter" text NOT NULL,
  "region" text,
  "client_build" text,
  "window_status" text NOT NULL,
  "updated_at" text,
  "source_id" text NOT NULL,
  "source_path" text NOT NULL,
  "raw_json" text NOT NULL,
  PRIMARY KEY ("publication_id","dataset_id","snapshot_id","sample_id"),
  FOREIGN KEY (publication_id,dataset_id,snapshot_id) REFERENCES research.publications(id,dataset_id,snapshot_id) ON DELETE CASCADE
);

ALTER TABLE research_data."observation_samples" ENABLE ROW LEVEL SECURITY;

CREATE POLICY published_read ON research_data."observation_samples" FOR SELECT TO anon,authenticated USING (EXISTS (SELECT 1 FROM research.publications p WHERE p.id=publication_id AND p.status='published'));

CREATE TABLE research_data."roles" (
  "publication_id" text NOT NULL,
  "dataset_id" text NOT NULL,
  "snapshot_id" text NOT NULL,
  "entity_id" text NOT NULL,
  "resource_type" text,
  "tags_json" text NOT NULL,
  PRIMARY KEY ("publication_id","dataset_id","snapshot_id","entity_id"),
  FOREIGN KEY (publication_id,dataset_id,snapshot_id) REFERENCES research.publications(id,dataset_id,snapshot_id) ON DELETE CASCADE
);

ALTER TABLE research_data."roles" ENABLE ROW LEVEL SECURITY;

CREATE POLICY published_read ON research_data."roles" FOR SELECT TO anon,authenticated USING (EXISTS (SELECT 1 FROM research.publications p WHERE p.id=publication_id AND p.status='published'));

CREATE TABLE research_data."snapshots" (
  "publication_id" text NOT NULL,
  "dataset_id" text NOT NULL,
  "snapshot_id" text NOT NULL,
  "set_number" bigint NOT NULL,
  "channel" text NOT NULL,
  "build" text,
  "generated_at" text,
  "schema_version" bigint NOT NULL,
  "collector_hash" text NOT NULL,
  "completeness" text NOT NULL,
  "exact_build" bigint NOT NULL,
  PRIMARY KEY ("publication_id","dataset_id","snapshot_id"),
  FOREIGN KEY (publication_id,dataset_id,snapshot_id) REFERENCES research.publications(id,dataset_id,snapshot_id) ON DELETE CASCADE
);

ALTER TABLE research_data."snapshots" ENABLE ROW LEVEL SECURITY;

CREATE POLICY published_read ON research_data."snapshots" FOR SELECT TO anon,authenticated USING (EXISTS (SELECT 1 FROM research.publications p WHERE p.id=publication_id AND p.status='published'));

CREATE TABLE research_data."source_collections" (
  "publication_id" text NOT NULL,
  "dataset_id" text NOT NULL,
  "snapshot_id" text NOT NULL,
  "source_path" text NOT NULL,
  "record_count" bigint,
  "data_type" text NOT NULL,
  "normalized_kind" text,
  "source_id" text NOT NULL,
  PRIMARY KEY ("publication_id","dataset_id","snapshot_id","source_path"),
  FOREIGN KEY (publication_id,dataset_id,snapshot_id) REFERENCES research.publications(id,dataset_id,snapshot_id) ON DELETE CASCADE
);

ALTER TABLE research_data."source_collections" ENABLE ROW LEVEL SECURITY;

CREATE POLICY published_read ON research_data."source_collections" FOR SELECT TO anon,authenticated USING (EXISTS (SELECT 1 FROM research.publications p WHERE p.id=publication_id AND p.status='published'));

CREATE TABLE research_data."source_documents" (
  "publication_id" text NOT NULL,
  "dataset_id" text NOT NULL,
  "snapshot_id" text NOT NULL,
  "source_id" text NOT NULL,
  "url" text NOT NULL,
  "title" text,
  "status" text NOT NULL,
  "retrieved_at" text,
  "published_at" text,
  "sha256" text,
  "raw_path" text,
  "media_type" text,
  "error" text,
  "evidence_origin" text NOT NULL,
  "capabilities_json" text NOT NULL,
  "stale" bigint NOT NULL,
  PRIMARY KEY ("publication_id","dataset_id","snapshot_id","source_id"),
  FOREIGN KEY (publication_id,dataset_id,snapshot_id) REFERENCES research.publications(id,dataset_id,snapshot_id) ON DELETE CASCADE
);

ALTER TABLE research_data."source_documents" ENABLE ROW LEVEL SECURITY;

CREATE POLICY published_read ON research_data."source_documents" FOR SELECT TO anon,authenticated USING (EXISTS (SELECT 1 FROM research.publications p WHERE p.id=publication_id AND p.status='published'));

CREATE TABLE research_data."supplemental_armory_choices" (
  "publication_id" text NOT NULL,
  "dataset_id" text NOT NULL,
  "snapshot_id" text NOT NULL,
  "choice_id" text NOT NULL,
  "armory_id" text NOT NULL,
  "choice_index" bigint,
  "pick_strategy" text,
  "required_tags_json" text,
  "excluded_tags_json" text,
  "used_by_json" text,
  "source_id" text NOT NULL,
  "source_path" text NOT NULL,
  "raw_json" text NOT NULL,
  PRIMARY KEY ("publication_id","dataset_id","snapshot_id","choice_id"),
  FOREIGN KEY (publication_id,dataset_id,snapshot_id) REFERENCES research.publications(id,dataset_id,snapshot_id) ON DELETE CASCADE
);

ALTER TABLE research_data."supplemental_armory_choices" ENABLE ROW LEVEL SECURITY;

CREATE POLICY published_read ON research_data."supplemental_armory_choices" FOR SELECT TO anon,authenticated USING (EXISTS (SELECT 1 FROM research.publications p WHERE p.id=publication_id AND p.status='published'));

CREATE TABLE research_data."supplemental_armory_items" (
  "publication_id" text NOT NULL,
  "dataset_id" text NOT NULL,
  "snapshot_id" text NOT NULL,
  "choice_id" text NOT NULL,
  "item_key" text NOT NULL,
  "source_id" text NOT NULL,
  "source_path" text NOT NULL,
  PRIMARY KEY ("publication_id","dataset_id","snapshot_id","choice_id","item_key"),
  FOREIGN KEY (publication_id,dataset_id,snapshot_id) REFERENCES research.publications(id,dataset_id,snapshot_id) ON DELETE CASCADE
);

ALTER TABLE research_data."supplemental_armory_items" ENABLE ROW LEVEL SECURITY;

CREATE POLICY published_read ON research_data."supplemental_armory_items" FOR SELECT TO anon,authenticated USING (EXISTS (SELECT 1 FROM research.publications p WHERE p.id=publication_id AND p.status='published'));

CREATE TABLE research_data."supplemental_augment_distribution" (
  "publication_id" text NOT NULL,
  "dataset_id" text NOT NULL,
  "snapshot_id" text NOT NULL,
  "sequence_id" bigint NOT NULL,
  "first_tier" text,
  "second_tier" text,
  "third_tier" text,
  "weight" double precision,
  "probability" double precision,
  "source_id" text NOT NULL,
  "source_path" text NOT NULL,
  "raw_json" text NOT NULL,
  PRIMARY KEY ("publication_id","dataset_id","snapshot_id","sequence_id"),
  FOREIGN KEY (publication_id,dataset_id,snapshot_id) REFERENCES research.publications(id,dataset_id,snapshot_id) ON DELETE CASCADE
);

ALTER TABLE research_data."supplemental_augment_distribution" ENABLE ROW LEVEL SECURITY;

CREATE POLICY published_read ON research_data."supplemental_augment_distribution" FOR SELECT TO anon,authenticated USING (EXISTS (SELECT 1 FROM research.publications p WHERE p.id=publication_id AND p.status='published'));

CREATE TABLE research_data."supplemental_baron_ensemble" (
  "publication_id" text NOT NULL,
  "dataset_id" text NOT NULL,
  "snapshot_id" text NOT NULL,
  "api_name" text NOT NULL,
  "name" text NOT NULL,
  "cost" bigint,
  "pool_count" bigint,
  "source_id" text NOT NULL,
  "source_path" text NOT NULL,
  "raw_json" text NOT NULL,
  PRIMARY KEY ("publication_id","dataset_id","snapshot_id","api_name"),
  FOREIGN KEY (publication_id,dataset_id,snapshot_id) REFERENCES research.publications(id,dataset_id,snapshot_id) ON DELETE CASCADE
);

ALTER TABLE research_data."supplemental_baron_ensemble" ENABLE ROW LEVEL SECURITY;

CREATE POLICY published_read ON research_data."supplemental_baron_ensemble" FOR SELECT TO anon,authenticated USING (EXISTS (SELECT 1 FROM research.publications p WHERE p.id=publication_id AND p.status='published'));

CREATE TABLE research_data."supplemental_beach_pop_instruments" (
  "publication_id" text NOT NULL,
  "dataset_id" text NOT NULL,
  "snapshot_id" text NOT NULL,
  "variation_key" text NOT NULL,
  "threshold" bigint NOT NULL,
  "instrument" text,
  "probability" double precision,
  "source_id" text NOT NULL,
  "source_path" text NOT NULL,
  "raw_json" text NOT NULL,
  PRIMARY KEY ("publication_id","dataset_id","snapshot_id","variation_key","threshold"),
  FOREIGN KEY (publication_id,dataset_id,snapshot_id) REFERENCES research.publications(id,dataset_id,snapshot_id) ON DELETE CASCADE
);

ALTER TABLE research_data."supplemental_beach_pop_instruments" ENABLE ROW LEVEL SECURITY;

CREATE POLICY published_read ON research_data."supplemental_beach_pop_instruments" FOR SELECT TO anon,authenticated USING (EXISTS (SELECT 1 FROM research.publications p WHERE p.id=publication_id AND p.status='published'));

CREATE TABLE research_data."supplemental_cashouts" (
  "publication_id" text NOT NULL,
  "dataset_id" text NOT NULL,
  "snapshot_id" text NOT NULL,
  "mechanic" text NOT NULL,
  "tier" bigint NOT NULL,
  "trait_key" text,
  "counter" text,
  "threshold_min" double precision,
  "threshold_max" double precision,
  "gold_value" double precision,
  "loot_key" text,
  "loot_table_key" text,
  "source_id" text NOT NULL,
  "source_path" text NOT NULL,
  "raw_json" text NOT NULL,
  PRIMARY KEY ("publication_id","dataset_id","snapshot_id","mechanic","tier"),
  FOREIGN KEY (publication_id,dataset_id,snapshot_id) REFERENCES research.publications(id,dataset_id,snapshot_id) ON DELETE CASCADE
);

ALTER TABLE research_data."supplemental_cashouts" ENABLE ROW LEVEL SECURITY;

CREATE POLICY published_read ON research_data."supplemental_cashouts" FOR SELECT TO anon,authenticated USING (EXISTS (SELECT 1 FROM research.publications p WHERE p.id=publication_id AND p.status='published'));

CREATE TABLE research_data."supplemental_collab_members" (
  "publication_id" text NOT NULL,
  "dataset_id" text NOT NULL,
  "snapshot_id" text NOT NULL,
  "group_key" text NOT NULL,
  "unit_key" text NOT NULL,
  "experimental" bigint NOT NULL,
  "source_id" text NOT NULL,
  "source_path" text NOT NULL,
  "raw_json" text NOT NULL,
  PRIMARY KEY ("publication_id","dataset_id","snapshot_id","group_key","unit_key"),
  FOREIGN KEY (publication_id,dataset_id,snapshot_id) REFERENCES research.publications(id,dataset_id,snapshot_id) ON DELETE CASCADE
);

ALTER TABLE research_data."supplemental_collab_members" ENABLE ROW LEVEL SECURITY;

CREATE POLICY published_read ON research_data."supplemental_collab_members" FOR SELECT TO anon,authenticated USING (EXISTS (SELECT 1 FROM research.publications p WHERE p.id=publication_id AND p.status='published'));

CREATE TABLE research_data."supplemental_collections" (
  "publication_id" text NOT NULL,
  "dataset_id" text NOT NULL,
  "snapshot_id" text NOT NULL,
  "collection_name" text NOT NULL,
  "row_count" bigint NOT NULL,
  "source_id" text NOT NULL,
  "source_path" text NOT NULL,
  "raw_json" text NOT NULL,
  PRIMARY KEY ("publication_id","dataset_id","snapshot_id","collection_name"),
  FOREIGN KEY (publication_id,dataset_id,snapshot_id) REFERENCES research.publications(id,dataset_id,snapshot_id) ON DELETE CASCADE
);

ALTER TABLE research_data."supplemental_collections" ENABLE ROW LEVEL SECURITY;

CREATE POLICY published_read ON research_data."supplemental_collections" FOR SELECT TO anon,authenticated USING (EXISTS (SELECT 1 FROM research.publications p WHERE p.id=publication_id AND p.status='published'));

CREATE TABLE research_data."supplemental_curves" (
  "publication_id" text NOT NULL,
  "dataset_id" text NOT NULL,
  "snapshot_id" text NOT NULL,
  "system" text NOT NULL,
  "row_name" text NOT NULL,
  "point_index" bigint NOT NULL,
  "x" double precision NOT NULL,
  "y" double precision NOT NULL,
  "unit" text NOT NULL,
  "source_id" text NOT NULL,
  "source_path" text NOT NULL,
  "raw_json" text NOT NULL,
  PRIMARY KEY ("publication_id","dataset_id","snapshot_id","system","row_name","point_index"),
  FOREIGN KEY (publication_id,dataset_id,snapshot_id) REFERENCES research.publications(id,dataset_id,snapshot_id) ON DELETE CASCADE
);

ALTER TABLE research_data."supplemental_curves" ENABLE ROW LEVEL SECURITY;

CREATE POLICY published_read ON research_data."supplemental_curves" FOR SELECT TO anon,authenticated USING (EXISTS (SELECT 1 FROM research.publications p WHERE p.id=publication_id AND p.status='published'));

CREATE TABLE research_data."supplemental_encounters" (
  "publication_id" text NOT NULL,
  "dataset_id" text NOT NULL,
  "snapshot_id" text NOT NULL,
  "api_name" text NOT NULL,
  "champion_hint" text,
  "pick_count" bigint,
  "probability" double precision,
  "source_id" text NOT NULL,
  "source_path" text NOT NULL,
  "raw_json" text NOT NULL,
  PRIMARY KEY ("publication_id","dataset_id","snapshot_id","api_name"),
  FOREIGN KEY (publication_id,dataset_id,snapshot_id) REFERENCES research.publications(id,dataset_id,snapshot_id) ON DELETE CASCADE
);

ALTER TABLE research_data."supplemental_encounters" ENABLE ROW LEVEL SECURITY;

CREATE POLICY published_read ON research_data."supplemental_encounters" FOR SELECT TO anon,authenticated USING (EXISTS (SELECT 1 FROM research.publications p WHERE p.id=publication_id AND p.status='published'));

CREATE TABLE research_data."supplemental_loot_nodes" (
  "publication_id" text NOT NULL,
  "dataset_id" text NOT NULL,
  "snapshot_id" text NOT NULL,
  "node_id" text NOT NULL,
  "parent_id" text,
  "group_name" text NOT NULL,
  "root_key" text NOT NULL,
  "api_name" text,
  "node_type" text,
  "strategy" text,
  "quantity" double precision,
  "pick_count" double precision,
  "weight" double precision,
  "probability" double precision,
  "is_reference" bigint NOT NULL,
  "is_dynamic" bigint NOT NULL,
  "filters_json" text,
  "mode" text NOT NULL,
  "source_id" text NOT NULL,
  "source_path" text NOT NULL,
  "raw_json" text NOT NULL,
  PRIMARY KEY ("publication_id","dataset_id","snapshot_id","node_id"),
  FOREIGN KEY (publication_id,dataset_id,snapshot_id) REFERENCES research.publications(id,dataset_id,snapshot_id) ON DELETE CASCADE
);

ALTER TABLE research_data."supplemental_loot_nodes" ENABLE ROW LEVEL SECURITY;

CREATE POLICY published_read ON research_data."supplemental_loot_nodes" FOR SELECT TO anon,authenticated USING (EXISTS (SELECT 1 FROM research.publications p WHERE p.id=publication_id AND p.status='published'));

CREATE TABLE research_data."supplemental_recommended_items" (
  "publication_id" text NOT NULL,
  "dataset_id" text NOT NULL,
  "snapshot_id" text NOT NULL,
  "table_key" text NOT NULL,
  "item_index" bigint NOT NULL,
  "item_key" text NOT NULL,
  "source_id" text NOT NULL,
  "source_path" text NOT NULL,
  "raw_json" text NOT NULL,
  PRIMARY KEY ("publication_id","dataset_id","snapshot_id","table_key","item_index"),
  FOREIGN KEY (publication_id,dataset_id,snapshot_id) REFERENCES research.publications(id,dataset_id,snapshot_id) ON DELETE CASCADE
);

ALTER TABLE research_data."supplemental_recommended_items" ENABLE ROW LEVEL SECURITY;

CREATE POLICY published_read ON research_data."supplemental_recommended_items" FOR SELECT TO anon,authenticated USING (EXISTS (SELECT 1 FROM research.publications p WHERE p.id=publication_id AND p.status='published'));

CREATE TABLE research_data."supplemental_recommended_tables" (
  "publication_id" text NOT NULL,
  "dataset_id" text NOT NULL,
  "snapshot_id" text NOT NULL,
  "table_key" text NOT NULL,
  "source_id" text NOT NULL,
  "source_path" text NOT NULL,
  "raw_json" text NOT NULL,
  PRIMARY KEY ("publication_id","dataset_id","snapshot_id","table_key"),
  FOREIGN KEY (publication_id,dataset_id,snapshot_id) REFERENCES research.publications(id,dataset_id,snapshot_id) ON DELETE CASCADE
);

ALTER TABLE research_data."supplemental_recommended_tables" ENABLE ROW LEVEL SECURITY;

CREATE POLICY published_read ON research_data."supplemental_recommended_tables" FOR SELECT TO anon,authenticated USING (EXISTS (SELECT 1 FROM research.publications p WHERE p.id=publication_id AND p.status='published'));

CREATE TABLE research_data."supplemental_rounds" (
  "publication_id" text NOT NULL,
  "dataset_id" text NOT NULL,
  "snapshot_id" text NOT NULL,
  "round_id" text NOT NULL,
  "stage" bigint NOT NULL,
  "round_number" bigint NOT NULL,
  "round_type" text NOT NULL,
  "source_id" text NOT NULL,
  "source_path" text NOT NULL,
  "raw_json" text NOT NULL,
  PRIMARY KEY ("publication_id","dataset_id","snapshot_id","round_id"),
  FOREIGN KEY (publication_id,dataset_id,snapshot_id) REFERENCES research.publications(id,dataset_id,snapshot_id) ON DELETE CASCADE
);

ALTER TABLE research_data."supplemental_rounds" ENABLE ROW LEVEL SECURITY;

CREATE POLICY published_read ON research_data."supplemental_rounds" FOR SELECT TO anon,authenticated USING (EXISTS (SELECT 1 FROM research.publications p WHERE p.id=publication_id AND p.status='published'));

CREATE TABLE research_data."supplemental_shop_pool" (
  "publication_id" text NOT NULL,
  "dataset_id" text NOT NULL,
  "snapshot_id" text NOT NULL,
  "shop_tier" bigint NOT NULL,
  "copies_per_unit" bigint,
  "unit_count" bigint,
  "total_copies" bigint,
  "source_id" text NOT NULL,
  "source_path" text NOT NULL,
  "raw_json" text NOT NULL,
  PRIMARY KEY ("publication_id","dataset_id","snapshot_id","shop_tier"),
  FOREIGN KEY (publication_id,dataset_id,snapshot_id) REFERENCES research.publications(id,dataset_id,snapshot_id) ON DELETE CASCADE
);

ALTER TABLE research_data."supplemental_shop_pool" ENABLE ROW LEVEL SECURITY;

CREATE POLICY published_read ON research_data."supplemental_shop_pool" FOR SELECT TO anon,authenticated USING (EXISTS (SELECT 1 FROM research.publications p WHERE p.id=publication_id AND p.status='published'));

CREATE TABLE research_data."supplemental_unit_recommendations" (
  "publication_id" text NOT NULL,
  "dataset_id" text NOT NULL,
  "snapshot_id" text NOT NULL,
  "unit_key" text NOT NULL,
  "table_key" text NOT NULL,
  "definition_status" text NOT NULL,
  "source_id" text NOT NULL,
  "source_path" text NOT NULL,
  "raw_json" text NOT NULL,
  PRIMARY KEY ("publication_id","dataset_id","snapshot_id","unit_key"),
  FOREIGN KEY (publication_id,dataset_id,snapshot_id) REFERENCES research.publications(id,dataset_id,snapshot_id) ON DELETE CASCADE
);

ALTER TABLE research_data."supplemental_unit_recommendations" ENABLE ROW LEVEL SECURITY;

CREATE POLICY published_read ON research_data."supplemental_unit_recommendations" FOR SELECT TO anon,authenticated USING (EXISTS (SELECT 1 FROM research.publications p WHERE p.id=publication_id AND p.status='published'));

CREATE TABLE research_data."supplemental_values" (
  "publication_id" text NOT NULL,
  "dataset_id" text NOT NULL,
  "snapshot_id" text NOT NULL,
  "source_path" text NOT NULL,
  "collection_name" text NOT NULL,
  "value_json" text NOT NULL,
  "value_number" double precision,
  "value_text" text,
  "source_id" text NOT NULL,
  PRIMARY KEY ("publication_id","dataset_id","snapshot_id","source_path"),
  FOREIGN KEY (publication_id,dataset_id,snapshot_id) REFERENCES research.publications(id,dataset_id,snapshot_id) ON DELETE CASCADE
);

ALTER TABLE research_data."supplemental_values" ENABLE ROW LEVEL SECURITY;

CREATE POLICY published_read ON research_data."supplemental_values" FOR SELECT TO anon,authenticated USING (EXISTS (SELECT 1 FROM research.publications p WHERE p.id=publication_id AND p.status='published'));

CREATE TABLE research_data."trait_breakpoints" (
  "publication_id" text NOT NULL,
  "dataset_id" text NOT NULL,
  "snapshot_id" text NOT NULL,
  "trait_id" text NOT NULL,
  "tier" bigint NOT NULL,
  "min_units" bigint,
  "max_units" bigint,
  "style" text,
  "description" text,
  "description_raw" text,
  "variables_json" text NOT NULL,
  "source_path" text NOT NULL,
  PRIMARY KEY ("publication_id","dataset_id","snapshot_id","trait_id","tier"),
  FOREIGN KEY (publication_id,dataset_id,snapshot_id) REFERENCES research.publications(id,dataset_id,snapshot_id) ON DELETE CASCADE
);

ALTER TABLE research_data."trait_breakpoints" ENABLE ROW LEVEL SECURITY;

CREATE POLICY published_read ON research_data."trait_breakpoints" FOR SELECT TO anon,authenticated USING (EXISTS (SELECT 1 FROM research.publications p WHERE p.id=publication_id AND p.status='published'));

CREATE TABLE research_data."traits" (
  "publication_id" text NOT NULL,
  "dataset_id" text NOT NULL,
  "snapshot_id" text NOT NULL,
  "entity_id" text NOT NULL,
  "trait_type" text,
  "effects_source" text,
  PRIMARY KEY ("publication_id","dataset_id","snapshot_id","entity_id"),
  FOREIGN KEY (publication_id,dataset_id,snapshot_id) REFERENCES research.publications(id,dataset_id,snapshot_id) ON DELETE CASCADE
);

ALTER TABLE research_data."traits" ENABLE ROW LEVEL SECURITY;

CREATE POLICY published_read ON research_data."traits" FOR SELECT TO anon,authenticated USING (EXISTS (SELECT 1 FROM research.publications p WHERE p.id=publication_id AND p.status='published'));

CREATE TABLE research_data."unit_stats" (
  "publication_id" text NOT NULL,
  "dataset_id" text NOT NULL,
  "snapshot_id" text NOT NULL,
  "entity_id" text NOT NULL,
  "stat" text NOT NULL,
  "star_level" bigint NOT NULL,
  "value" double precision,
  "unit" text NOT NULL,
  "source_path" text NOT NULL,
  PRIMARY KEY ("publication_id","dataset_id","snapshot_id","entity_id","stat","star_level"),
  FOREIGN KEY (publication_id,dataset_id,snapshot_id) REFERENCES research.publications(id,dataset_id,snapshot_id) ON DELETE CASCADE
);

ALTER TABLE research_data."unit_stats" ENABLE ROW LEVEL SECURITY;

CREATE POLICY published_read ON research_data."unit_stats" FOR SELECT TO anon,authenticated USING (EXISTS (SELECT 1 FROM research.publications p WHERE p.id=publication_id AND p.status='published'));

CREATE TABLE research_data."unit_traits" (
  "publication_id" text NOT NULL,
  "dataset_id" text NOT NULL,
  "snapshot_id" text NOT NULL,
  "unit_id" text NOT NULL,
  "trait_id" text NOT NULL,
  "membership_kind" text NOT NULL,
  "source_path" text NOT NULL,
  PRIMARY KEY ("publication_id","dataset_id","snapshot_id","unit_id","trait_id","membership_kind"),
  FOREIGN KEY (publication_id,dataset_id,snapshot_id) REFERENCES research.publications(id,dataset_id,snapshot_id) ON DELETE CASCADE
);

ALTER TABLE research_data."unit_traits" ENABLE ROW LEVEL SECURITY;

CREATE POLICY published_read ON research_data."unit_traits" FOR SELECT TO anon,authenticated USING (EXISTS (SELECT 1 FROM research.publications p WHERE p.id=publication_id AND p.status='published'));

CREATE TABLE research_data."units" (
  "publication_id" text NOT NULL,
  "dataset_id" text NOT NULL,
  "snapshot_id" text NOT NULL,
  "entity_id" text NOT NULL,
  "cost" bigint,
  "shop_unit" bigint,
  "shop_tier" bigint,
  "pool_count" bigint,
  "pool_cost" bigint,
  "role" text,
  "unit_kind" text NOT NULL,
  "locked" bigint NOT NULL,
  "board_slots" bigint,
  PRIMARY KEY ("publication_id","dataset_id","snapshot_id","entity_id"),
  FOREIGN KEY (publication_id,dataset_id,snapshot_id) REFERENCES research.publications(id,dataset_id,snapshot_id) ON DELETE CASCADE
);

ALTER TABLE research_data."units" ENABLE ROW LEVEL SECURITY;

CREATE POLICY published_read ON research_data."units" FOR SELECT TO anon,authenticated USING (EXISTS (SELECT 1 FROM research.publications p WHERE p.id=publication_id AND p.status='published'));

CREATE TABLE research_data."unlock_rules" (
  "publication_id" text NOT NULL,
  "dataset_id" text NOT NULL,
  "snapshot_id" text NOT NULL,
  "rule_id" text NOT NULL,
  "unit_id" text NOT NULL,
  "description" text NOT NULL,
  "level_required" bigint,
  "expression_json" text NOT NULL,
  "parse_status" text NOT NULL,
  "evaluation_timing" text,
  "completion_effect" text,
  "source_path" text NOT NULL,
  PRIMARY KEY ("publication_id","dataset_id","snapshot_id","rule_id"),
  FOREIGN KEY (publication_id,dataset_id,snapshot_id) REFERENCES research.publications(id,dataset_id,snapshot_id) ON DELETE CASCADE
);

ALTER TABLE research_data."unlock_rules" ENABLE ROW LEVEL SECURITY;

CREATE POLICY published_read ON research_data."unlock_rules" FOR SELECT TO anon,authenticated USING (EXISTS (SELECT 1 FROM research.publications p WHERE p.id=publication_id AND p.status='published'));

ALTER TABLE research_data."abilities" ADD FOREIGN KEY ("publication_id","dataset_id","snapshot_id","unit_id") REFERENCES research_data."units" ("publication_id","dataset_id","snapshot_id","entity_id") DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE research_data."abilities" ADD FOREIGN KEY ("publication_id","dataset_id","snapshot_id","ability_id") REFERENCES research_data."entities" ("publication_id","dataset_id","snapshot_id","entity_id") DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE research_data."ability_values" ADD FOREIGN KEY ("publication_id","dataset_id","snapshot_id","ability_id") REFERENCES research_data."abilities" ("publication_id","dataset_id","snapshot_id","ability_id") DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE research_data."assertions" ADD FOREIGN KEY ("publication_id","dataset_id","snapshot_id","entity_id") REFERENCES research_data."entities" ("publication_id","dataset_id","snapshot_id","entity_id") DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE research_data."assets" ADD FOREIGN KEY ("publication_id","dataset_id","snapshot_id","entity_id") REFERENCES research_data."entities" ("publication_id","dataset_id","snapshot_id","entity_id") DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE research_data."augments" ADD FOREIGN KEY ("publication_id","dataset_id","snapshot_id","entity_id") REFERENCES research_data."entities" ("publication_id","dataset_id","snapshot_id","entity_id") DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE research_data."collection_inventory" ADD FOREIGN KEY ("publication_id","dataset_id","snapshot_id","source_id") REFERENCES research_data."source_documents" ("publication_id","dataset_id","snapshot_id","source_id") DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE research_data."condition_nodes" ADD FOREIGN KEY ("publication_id","dataset_id","snapshot_id","parent_id") REFERENCES research_data."condition_nodes" ("publication_id","dataset_id","snapshot_id","node_id") DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE research_data."condition_nodes" ADD FOREIGN KEY ("publication_id","dataset_id","snapshot_id","rule_id") REFERENCES research_data."unlock_rules" ("publication_id","dataset_id","snapshot_id","rule_id") DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE research_data."condition_predicates" ADD FOREIGN KEY ("publication_id","dataset_id","snapshot_id","node_id") REFERENCES research_data."condition_nodes" ("publication_id","dataset_id","snapshot_id","node_id") DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE research_data."curated_records" ADD FOREIGN KEY ("publication_id","dataset_id","snapshot_id","source_id") REFERENCES research_data."source_documents" ("publication_id","dataset_id","snapshot_id","source_id") DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE research_data."curated_values" ADD FOREIGN KEY ("publication_id","dataset_id","snapshot_id","source_id") REFERENCES research_data."source_documents" ("publication_id","dataset_id","snapshot_id","source_id") DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE research_data."curated_values" ADD FOREIGN KEY ("publication_id","dataset_id","snapshot_id","record_id") REFERENCES research_data."curated_records" ("publication_id","dataset_id","snapshot_id","record_id") DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE research_data."effect_values" ADD FOREIGN KEY ("publication_id","dataset_id","snapshot_id","entity_id") REFERENCES research_data."entities" ("publication_id","dataset_id","snapshot_id","entity_id") DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE research_data."entity_aliases" ADD FOREIGN KEY ("publication_id","dataset_id","snapshot_id","entity_id") REFERENCES research_data."entities" ("publication_id","dataset_id","snapshot_id","entity_id") DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE research_data."entity_relationships" ADD FOREIGN KEY ("publication_id","dataset_id","snapshot_id","target_entity_id") REFERENCES research_data."entities" ("publication_id","dataset_id","snapshot_id","entity_id") DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE research_data."entity_relationships" ADD FOREIGN KEY ("publication_id","dataset_id","snapshot_id","source_entity_id") REFERENCES research_data."entities" ("publication_id","dataset_id","snapshot_id","entity_id") DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE research_data."field_gaps" ADD FOREIGN KEY ("publication_id","dataset_id","snapshot_id","entity_id") REFERENCES research_data."entities" ("publication_id","dataset_id","snapshot_id","entity_id") DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE research_data."item_recipes" ADD FOREIGN KEY ("publication_id","dataset_id","snapshot_id","component_id") REFERENCES research_data."items" ("publication_id","dataset_id","snapshot_id","entity_id") DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE research_data."item_recipes" ADD FOREIGN KEY ("publication_id","dataset_id","snapshot_id","result_id") REFERENCES research_data."items" ("publication_id","dataset_id","snapshot_id","entity_id") DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE research_data."items" ADD FOREIGN KEY ("publication_id","dataset_id","snapshot_id","entity_id") REFERENCES research_data."entities" ("publication_id","dataset_id","snapshot_id","entity_id") DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE research_data."loot_entries" ADD FOREIGN KEY ("publication_id","dataset_id","snapshot_id","loot_id") REFERENCES research_data."loot_tables" ("publication_id","dataset_id","snapshot_id","entity_id") DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE research_data."loot_tables" ADD FOREIGN KEY ("publication_id","dataset_id","snapshot_id","entity_id") REFERENCES research_data."entities" ("publication_id","dataset_id","snapshot_id","entity_id") DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE research_data."mechanics" ADD FOREIGN KEY ("publication_id","dataset_id","snapshot_id","entity_id") REFERENCES research_data."entities" ("publication_id","dataset_id","snapshot_id","entity_id") DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE research_data."observation_comps" ADD FOREIGN KEY ("publication_id","dataset_id","snapshot_id","source_id") REFERENCES research_data."observation_payloads" ("publication_id","dataset_id","snapshot_id","source_id") DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE research_data."observation_item_recommendations" ADD FOREIGN KEY ("publication_id","dataset_id","snapshot_id","comp_id") REFERENCES research_data."observation_comps" ("publication_id","dataset_id","snapshot_id","comp_id") DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE research_data."observation_memberships" ADD FOREIGN KEY ("publication_id","dataset_id","snapshot_id","comp_id") REFERENCES research_data."observation_comps" ("publication_id","dataset_id","snapshot_id","comp_id") DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE research_data."observation_metrics" ADD FOREIGN KEY ("publication_id","dataset_id","snapshot_id","sample_id") REFERENCES research_data."observation_samples" ("publication_id","dataset_id","snapshot_id","sample_id") DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE research_data."observation_placements" ADD FOREIGN KEY ("publication_id","dataset_id","snapshot_id","sample_id") REFERENCES research_data."observation_samples" ("publication_id","dataset_id","snapshot_id","sample_id") DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE research_data."observation_recommended_items" ADD FOREIGN KEY ("publication_id","dataset_id","snapshot_id","comp_id","recommendation_index") REFERENCES research_data."observation_item_recommendations" ("publication_id","dataset_id","snapshot_id","comp_id","recommendation_index") DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE research_data."observation_samples" ADD FOREIGN KEY ("publication_id","dataset_id","snapshot_id","comp_id") REFERENCES research_data."observation_comps" ("publication_id","dataset_id","snapshot_id","comp_id") DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE research_data."roles" ADD FOREIGN KEY ("publication_id","dataset_id","snapshot_id","entity_id") REFERENCES research_data."entities" ("publication_id","dataset_id","snapshot_id","entity_id") DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE research_data."supplemental_armory_items" ADD FOREIGN KEY ("publication_id","dataset_id","snapshot_id","choice_id") REFERENCES research_data."supplemental_armory_choices" ("publication_id","dataset_id","snapshot_id","choice_id") DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE research_data."supplemental_loot_nodes" ADD FOREIGN KEY ("publication_id","dataset_id","snapshot_id","parent_id") REFERENCES research_data."supplemental_loot_nodes" ("publication_id","dataset_id","snapshot_id","node_id") DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE research_data."supplemental_recommended_items" ADD FOREIGN KEY ("publication_id","dataset_id","snapshot_id","table_key") REFERENCES research_data."supplemental_recommended_tables" ("publication_id","dataset_id","snapshot_id","table_key") DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE research_data."trait_breakpoints" ADD FOREIGN KEY ("publication_id","dataset_id","snapshot_id","trait_id") REFERENCES research_data."traits" ("publication_id","dataset_id","snapshot_id","entity_id") DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE research_data."traits" ADD FOREIGN KEY ("publication_id","dataset_id","snapshot_id","entity_id") REFERENCES research_data."entities" ("publication_id","dataset_id","snapshot_id","entity_id") DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE research_data."unit_stats" ADD FOREIGN KEY ("publication_id","dataset_id","snapshot_id","entity_id") REFERENCES research_data."units" ("publication_id","dataset_id","snapshot_id","entity_id") DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE research_data."unit_traits" ADD FOREIGN KEY ("publication_id","dataset_id","snapshot_id","trait_id") REFERENCES research_data."traits" ("publication_id","dataset_id","snapshot_id","entity_id") DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE research_data."unit_traits" ADD FOREIGN KEY ("publication_id","dataset_id","snapshot_id","unit_id") REFERENCES research_data."units" ("publication_id","dataset_id","snapshot_id","entity_id") DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE research_data."units" ADD FOREIGN KEY ("publication_id","dataset_id","snapshot_id","entity_id") REFERENCES research_data."entities" ("publication_id","dataset_id","snapshot_id","entity_id") DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE research_data."unlock_rules" ADD FOREIGN KEY ("publication_id","dataset_id","snapshot_id","unit_id") REFERENCES research_data."units" ("publication_id","dataset_id","snapshot_id","entity_id") DEFERRABLE INITIALLY DEFERRED;

GRANT SELECT ON ALL TABLES IN SCHEMA research_data TO anon,authenticated;
GRANT ALL ON ALL TABLES IN SCHEMA research_data TO service_role;
COMMIT;
