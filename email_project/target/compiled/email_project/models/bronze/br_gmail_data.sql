

SELECT 
    message_id,
    thread_id,
    gmail_thread_id,

    lower(sender_email) as sender_email,
    sender_display_name,
    sender_domain,
    sender_domain_type,

    subject,

    try_to_timestamp(full_date) as email_timestamp,

    year,
    quarter,
    month,
    week,

    day_name,
    is_weekend,

    hour,
    minute,

    folder,

    attachment_count,
    has_attachments,

    is_read,
    is_starred,
    is_replied,
    is_forwarded,

    thread_subject,
    thread_message_count,
    thread_duration_sec,
    thread_participant_count

from email_db.raw.gmail_data