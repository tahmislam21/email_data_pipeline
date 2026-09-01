{{config(materialized='table')}}
with msg_rank as(
    SELECT 
        MESSAGE_ID,
        LOWER(SENDER_EMAIL) AS sender_email,
        SENDER_DISPLAY_NAME AS SENDER_NAME,
        SENDER_DOMAIN,
        SENDER_DOMAIN_TYPE,
        CASE
            WHEN CC IS NULL THEN 'None'
            ELSE CC
        END AS CC,
        RECIPIENT_COUNT,    
        SUBJECT,    
        FOLDER,
        SPLIT(ALL_LABELS, ', INBOX')[0] AS ALL_LABELS,    
        SIZE_BYTES,
        ATTACHMENT_COUNT,
        HAS_ATTACHMENTS,  
        IS_READ,
        IS_STARRED,
        IS_REPLIED,
        THREAD_FIRST_MESSAGE_AT,           
        THREAD_PARTICIPANT_COUNT,
        RANK () OVER (PARTITION BY MESSAGE_ID ORDER BY THREAD_FIRST_MESSAGE_AT desc) AS rnk
    from {{ source('raw','gmail_data') }}
)
SELECT         
        ROW_NUMBER() OVER (ORDER BY THREAD_FIRST_MESSAGE_AT DESC) AS SL,
        SENDER_NAME,
        sender_email,
        SENDER_DOMAIN,
        SENDER_DOMAIN_TYPE,
        CC,
        RECIPIENT_COUNT,    
        SUBJECT,    
        FOLDER,
        ALL_LABELS,    
        SIZE_BYTES,
        ATTACHMENT_COUNT,
        HAS_ATTACHMENTS,  
        IS_READ,
        IS_STARRED,
        IS_REPLIED, 
        SPLIT(THREAD_FIRST_MESSAGE_AT, ' ')[0] ::DATE AS RECEIVE_DATE,
        SPLIT(THREAD_FIRST_MESSAGE_AT, ' ')[1]::TIME AS RECEIVE_TIME,
        EXTRACT(dow from RECEIVE_DATE) AS DAY_OF_WEEK,           
        THREAD_PARTICIPANT_COUNT, 
    FROM msg_rank WHERE rnk = 1