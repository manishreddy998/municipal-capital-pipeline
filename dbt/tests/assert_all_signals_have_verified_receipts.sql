-- Fail if any infrastructure signal lacks a verified sha256 receipt, valid page number, or verbatim quote
select
    signal_id,
    latest_doc_id,
    latest_file_sha256,
    latest_page_number
from {{ ref('mart_infrastructure_signals') }}
where receipt_verified = false
   or latest_file_sha256 is null
   or length(latest_file_sha256) <> 64
   or latest_page_number < 1
   or latest_verbatim_quote is null
   or length(trim(latest_verbatim_quote)) = 0
