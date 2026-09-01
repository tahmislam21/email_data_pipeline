
  
    

create or replace transient table RAW.PUBLIC.test_model
    
    
    
    as (

select * from raw.jaffle_shop.customers
where id > 20
    )
;


  