select
    municipality_id,
    municipality_name,
    region,
    agency_type,
    asset_category,
    count(*) as total_projects,
    sum(case when not is_tabled_motion then 1 else 0 end) as active_approved_projects,
    sum(case when is_tabled_motion then 1 else 0 end) as tabled_deferred_projects,
    sum(active_approved_budget_cad) as total_active_budget_cad,
    sum(case when has_budget_amendment then cumulative_amendment_delta_cad else 0 end) as net_amendment_expansion_cad,
    sum(case when has_table_math_error then 1 else 0 end) as math_discrepancy_projects
from {{ ref('mart_infrastructure_signals') }}
group by 1, 2, 3, 4, 5
