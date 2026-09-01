
  
    

create or replace transient table email_db.warehouse.email_fct
    
    
    
    as (

SELECT MESSAGE_ID,
sender_email
from email_db.staging.stg_gmail_data
    )
;


  