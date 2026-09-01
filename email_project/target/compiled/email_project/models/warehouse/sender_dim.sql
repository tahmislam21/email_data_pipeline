

with reorder_columns as(
SELECT 
distinct sender_email,
SENDER_NAME,
SENDER_DOMAIN_TYPE
FROM email_db.staging.stg_gmail_data
)
SELECT
SENDER_NAME, 
sender_email,
SENDER_DOMAIN_TYPE
FROM reorder_columns