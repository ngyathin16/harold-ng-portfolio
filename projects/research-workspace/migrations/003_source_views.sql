BEGIN;
SET LOCAL search_path=research_data,pg_catalog;
CREATE VIEW v_units WITH(security_invoker=true) AS SELECT e.*,u.cost,u.shop_unit,u.shop_tier,u.pool_count,u.pool_cost,u.role,u.unit_kind,u.locked,u.board_slots FROM entities e JOIN units u USING(publication_id,dataset_id,snapshot_id,entity_id);
CREATE VIEW v_items WITH(security_invoker=true) AS SELECT e.*,i.category,i.unique_item,i.stat_line,i.tags_json,i.collection FROM entities e JOIN items i USING(publication_id,dataset_id,snapshot_id,entity_id);
CREATE VIEW v_traits WITH(security_invoker=true) AS SELECT e.*,t.trait_type,t.effects_source FROM entities e JOIN traits t USING(publication_id,dataset_id,snapshot_id,entity_id);
CREATE VIEW v_augments WITH(security_invoker=true) AS SELECT e.*,a.rarity,a.augment_type,a.rounds_json,a.round_variants_json,a.tags_json,a.quest_related FROM entities e JOIN augments a USING(publication_id,dataset_id,snapshot_id,entity_id);
CREATE VIEW v_unlocks WITH(security_invoker=true) AS SELECT e.entity_id,e.source_key,e.name,u.cost,e.source_id,r.* FROM unlock_rules r JOIN units u ON u.publication_id=r.publication_id AND u.entity_id=r.unit_id JOIN entities e ON e.publication_id=r.publication_id AND e.entity_id=r.unit_id;
CREATE VIEW v_abilities WITH(security_invoker=true) AS SELECT e.*,a.unit_id,a.variant,a.form_source_key FROM abilities a JOIN entities e ON e.publication_id=a.publication_id AND e.entity_id=a.ability_id;
CREATE VIEW v_loot WITH(security_invoker=true) AS SELECT e.*,l.owner_source_key,l.drop_table_source_key,l.outcomes_status FROM entities e JOIN loot_tables l USING(publication_id,dataset_id,snapshot_id,entity_id);
CREATE VIEW v_effects WITH(security_invoker=true) AS SELECT e.name,e.kind,v.* FROM effect_values v JOIN entities e USING(publication_id,dataset_id,snapshot_id,entity_id);
CREATE VIEW v_system_curves WITH(security_invoker=true) AS SELECT * FROM supplemental_curves;
CREATE VIEW v_loot_outcomes WITH(security_invoker=true) AS SELECT * FROM supplemental_loot_nodes;
CREATE VIEW v_cashouts WITH(security_invoker=true) AS SELECT * FROM supplemental_cashouts;
CREATE VIEW v_recommended_items WITH(security_invoker=true) AS SELECT u.publication_id,u.dataset_id,u.snapshot_id,u.unit_key,u.table_key,i.item_key,i.item_index,u.definition_status,u.source_id,u.source_path AS unit_source_path,i.source_path AS item_source_path FROM supplemental_unit_recommendations u LEFT JOIN supplemental_recommended_items i ON u.publication_id=i.publication_id AND u.table_key=i.table_key;
CREATE VIEW v_observations WITH(security_invoker=true) AS SELECT s.*,c.name_source_keys FROM observation_samples s LEFT JOIN observation_comps c USING(publication_id,dataset_id,snapshot_id,comp_id);
CREATE VIEW v_comp_observations WITH(security_invoker=true) AS
 SELECT s.publication_id,s.dataset_id,s.snapshot_id,s.sample_id,s.comp_id,c.name_source_keys,s.sample_count,
 max(CASE WHEN m.metric='average_placement' THEN m.value END) AS average_placement,
 max(CASE WHEN m.metric='win_rate' THEN m.value END) AS win_rate,
 max(CASE WHEN m.metric='top_four_rate' THEN m.value END) AS top_four_rate,
 max(CASE WHEN m.metric='population_share' THEN m.value END) AS population_share,
 s.requested_window_days,s.window_status,s.updated_at,s.source_id,s.source_path
 FROM observation_samples s JOIN observation_comps c USING(publication_id,dataset_id,snapshot_id,comp_id)
 LEFT JOIN observation_metrics m USING(publication_id,dataset_id,snapshot_id,sample_id)
 WHERE s.record_kind='composition'
 GROUP BY s.publication_id,s.dataset_id,s.snapshot_id,s.sample_id,c.name_source_keys;
CREATE VIEW research_catalog WITH(security_invoker=true) AS
 SELECT publication_id,dataset_id,snapshot_id,entity_id AS id,kind,name,description,source_id,source_path AS locator FROM entities
 UNION ALL SELECT publication_id,dataset_id,snapshot_id,record_id,kind,name,description,source_id,source_locator FROM curated_records
 UNION ALL SELECT publication_id,dataset_id,snapshot_id,'observation:comp:' || comp_id,'composition',coalesce(name_source_keys,comp_id),
 'Observed PBE composition. Query v_comp_observations for sample counts, metrics, and window limits.',source_id,source_path FROM observation_comps;
CREATE VIEW v_evidence WITH(security_invoker=true) AS
 SELECT a.publication_id,a.dataset_id,a.snapshot_id,a.entity_id,a.field_path,a.value_json,a.source_id,a.source_path,d.url,d.sha256,d.retrieved_at FROM assertions a JOIN source_documents d USING(publication_id,dataset_id,snapshot_id,source_id)
 UNION ALL SELECT a.publication_id,a.dataset_id,a.snapshot_id,a.record_id,a.field_path,a.value_json,a.source_id,a.source_path,d.url,d.sha256,d.retrieved_at FROM curated_values a JOIN source_documents d USING(publication_id,dataset_id,snapshot_id,source_id)
 UNION ALL SELECT a.publication_id,a.dataset_id,a.snapshot_id,a.source_id || ':' || a.source_path,a.source_path,a.value_json,a.source_id,a.source_path,d.url,d.sha256,d.retrieved_at FROM supplemental_values a JOIN source_documents d USING(publication_id,dataset_id,snapshot_id,source_id)
 UNION ALL SELECT a.publication_id,a.dataset_id,a.snapshot_id,'observation:comp:' || a.comp_id,'$',a.raw_json,a.source_id,a.source_path,d.url,d.sha256,d.retrieved_at FROM observation_comps a JOIN source_documents d USING(publication_id,dataset_id,snapshot_id,source_id);
CREATE VIEW v_coverage WITH(security_invoker=true) AS SELECT *,
 CASE WHEN expected>0 THEN round(100.0*observed/expected,2) END AS inventory_percent,
 CASE WHEN applicable_fields>0 THEN round(100.0*known_fields/applicable_fields,2) END AS basic_field_percent FROM coverage_entries;

CREATE INDEX entities_kind_name ON entities(publication_id,kind,name,entity_id);
CREATE INDEX entities_search ON entities USING gin(to_tsvector('simple',coalesce(name,'') || ' ' || coalesce(entity_id,'') || ' ' || coalesce(description,'')));
CREATE INDEX aliases_lookup ON entity_aliases(publication_id,lower(alias),entity_id);
CREATE INDEX units_filter ON units(publication_id,cost,shop_unit,locked);
CREATE INDEX trait_members ON unit_traits(publication_id,trait_id,unit_id);
CREATE INDEX evidence_owner ON assertions(publication_id,entity_id,field_path);
CREATE INDEX gaps_owner ON field_gaps(publication_id,entity_id);
GRANT SELECT ON ALL TABLES IN SCHEMA research_data TO anon,authenticated;
GRANT ALL ON ALL TABLES IN SCHEMA research_data TO service_role;
COMMIT;
