-- Fail if any resolved entity has zero or more than one latest record
select
    resolved_entity_id,
    sum(case when is_latest_record then 1 else 0 end) as latest_rows
from {{ ref('int_budget_amendment_chains') }}
group by 1
having sum(case when is_latest_record then 1 else 0 end) <> 1
