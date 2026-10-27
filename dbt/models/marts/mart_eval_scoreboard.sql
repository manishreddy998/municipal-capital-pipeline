with truth_vs_ext as (
    select
        t.mention_id,
        (t.asset_category = e.asset_category) as cat_ok,
        (t.lifecycle_stage = e.lifecycle_stage) as stage_ok,
        (cast(t.verified_sum_cad as bigint) = e.verified_sum_cad) as budget_ok,
        (cast(t.has_table_math_error as boolean) = e.has_table_math_error) as math_flag_ok,
        (cast(t.is_tabled_motion as boolean) = e.is_tabled_motion) as tabled_flag_ok,
        (t.fiscal_years = e.fiscal_years) as fy_ok,
        e.receipt_verified
    from {{ source('raw', 'truth_appearances') }} t
    inner join {{ ref('stg_extracted_mentions') }} e
        on t.mention_id = e.mention_id
)
select
    count(*) as total_mentions_evaluated,
    sum(case when cat_ok and stage_ok and budget_ok and math_flag_ok and tabled_flag_ok and fy_ok then 1 else 0 end) as all_fields_exact_mentions,
    round(
        (
            sum(case when cat_ok then 1 else 0 end)
            + sum(case when stage_ok then 1 else 0 end)
            + sum(case when budget_ok then 1 else 0 end)
            + sum(case when math_flag_ok then 1 else 0 end)
            + sum(case when tabled_flag_ok then 1 else 0 end)
            + sum(case when fy_ok then 1 else 0 end)
        ) * 1.0 / (count(*) * 6),
        4
    ) as overall_field_accuracy,
    sum(case when receipt_verified then 1 else 0 end) as verified_receipt_count,
    sum(case when not receipt_verified then 1 else 0 end) as unverified_receipt_count
from truth_vs_ext
