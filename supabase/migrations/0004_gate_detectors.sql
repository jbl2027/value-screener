-- Which qualitative-gate detectors fired (llm, going_concern, material_weakness, restatement,
-- major_impairment).
alter table valuations_latest add column if not exists qualitative_detectors text;

create or replace view latest_screen with (security_invoker = true) as
select v.ticker, v.name, v.sector_model, v.gics_sector, v.gics_industry_group,
       v.status, v.status_reason, v.confidence, v.confidence_reasons, v.warnings,
       v.price, v.fair_value, v.mos, v.company_multiple, v.fair_multiple, v.peer_group,
       v.qualitative_gate, v.qualitative_reasons,
       s.composite, s.composite_confidence, s.composite_rank, s.components_available,
       v.as_of, v.qualitative_detectors
from valuations_latest v
left join scores_latest s using (cik);
