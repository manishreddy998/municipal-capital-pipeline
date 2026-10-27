-- Fail if any tabled or deferred council motion has a non-zero active approved budget
select
    signal_id,
    resolution_status,
    active_approved_budget_cad
from {{ ref('mart_infrastructure_signals') }}
where is_tabled_motion = true
  and active_approved_budget_cad <> 0
