with latest as (
    select *
    from {{ ref('int_budget_amendment_chains') }}
    where is_latest_record = true
),
agg_flags as (
    select
        resolved_entity_id,
        max(case when project_code_stated <> '' then project_code_stated else null end) as canonical_project_code,
        bool_or(has_table_math_error) as any_table_math_error,
        bool_or(is_budget_amendment) as has_budget_amendment,
        bool_or(is_tabled_motion) as is_tabled_motion_flag,
        min(extraction_confidence) as min_confidence
    from {{ ref('int_budget_amendment_chains') }}
    group by 1
)
select
    l.resolved_entity_id as signal_id,
    coalesce(f.canonical_project_code, 'UNASSIGNED') as project_code,
    l.true_project_id,
    l.municipality_id,
    l.municipality_name,
    l.agency_alias,
    l.region,
    l.agency_type,
    l.mention_project_name as latest_project_title,
    l.asset_category,
    l.lifecycle_stage,
    l.resolution_status,
    l.initial_verified_budget_cad as initial_budget_cad,
    l.verified_sum_cad as latest_verified_budget_cad,
    l.active_approved_budget_cad,
    l.cumulative_amendment_delta_cad,
    l.total_appearances as document_appearances,
    l.first_seen_date,
    l.meeting_date as latest_meeting_date,
    l.fiscal_years,
    l.funding_source,
    l.regulatory_driver,
    f.has_budget_amendment,
    f.any_table_math_error as has_table_math_error,
    f.is_tabled_motion_flag as is_tabled_motion,
    l.doc_id as latest_doc_id,
    l.file_name as latest_file_name,
    l.file_sha256 as latest_file_sha256,
    l.page_number as latest_page_number,
    l.line_number as latest_line_number,
    l.verbatim_quote as latest_verbatim_quote,
    l.receipt_verified,
    f.min_confidence as signal_confidence
from latest l
inner join agg_flags f
    on l.resolved_entity_id = f.resolved_entity_id
