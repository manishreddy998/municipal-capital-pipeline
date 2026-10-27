with ordered as (
    select
        *,
        row_number() over (
            partition by resolved_entity_id
            order by meeting_date asc, doc_id asc, mention_id asc
        ) as appearance_seq,
        row_number() over (
            partition by resolved_entity_id
            order by meeting_date desc, doc_id desc, mention_id desc
        ) as recency_rank,
        count(*) over (
            partition by resolved_entity_id
        ) as total_appearances,
        first_value(verified_sum_cad) over (
            partition by resolved_entity_id
            order by meeting_date asc, doc_id asc, mention_id asc
        ) as initial_verified_budget_cad,
        first_value(doc_id) over (
            partition by resolved_entity_id
            order by meeting_date asc, doc_id asc, mention_id asc
        ) as first_seen_doc_id,
        first_value(meeting_date) over (
            partition by resolved_entity_id
            order by meeting_date asc, doc_id asc, mention_id asc
        ) as first_seen_date
    from {{ ref('int_project_entity_links') }}
)
select
    *,
    (recency_rank = 1) as is_latest_record,
    ( recency_rank > 1 ) as is_superseded,
    (verified_sum_cad - initial_verified_budget_cad) as cumulative_amendment_delta_cad
from ordered
