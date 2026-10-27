select
    doc_id,
    municipality_id,
    municipality_name,
    agency_alias,
    region,
    agency_type,
    doc_type,
    meeting_date,
    file_name,
    file_sha256,
    cast(file_bytes as bigint) as file_bytes,
    cast(total_pages as integer) as total_pages,
    cast(total_lines as integer) as total_lines,
    cast(has_adversarial_comment as boolean) as has_adversarial_comment
from {{ source('raw', 'municipal_documents') }}
