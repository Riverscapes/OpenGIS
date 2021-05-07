create or replace function fn_store_q2_equation(p_watershed_id varchar(8), p_equation varchar(255))
    returns int
    language plpgsql
as
$$
declare
    p_row_count int;
begin
    UPDATE watersheds SET q2 = p_equation where watershed_id = p_watershed_id;
    GET DIAGNOSTICS p_row_count = ROW_COUNT;
    return p_row_count;
end
$$;

create or replace function fn_store_qlow_equation(p_watershed_id varchar(8), p_equation varchar(255))
    returns int
    language plpgsql
as
$$
declare
    p_row_count int;
begin
    UPDATE watersheds SET qlow = p_equation where watershed_id = p_watershed_id;
    GET DIAGNOSTICS p_row_count = ROW_COUNT;
    return p_row_count;
end
$$;

create or replace function fn_store_watershed_hydro_param(p_watershed_id varchar(8), p_param_name varchar(255),
                                                          p_value real)
    returns int
    language plpgsql
as
$$
declare
    p_param_id  int;
    p_row_count int;
begin
    select param_id into p_param_id from hydro_params where name = p_param_name;

    if (p_param_id is null) then
        raise 'Parameter with name % does not exists', p_param_name;
    end if;

    INSERT INTO watershed_hydro_params (watershed_id, param_id, value)
    VALUES (p_watershed_id, p_param_id, p_value)
    ON CONFLICT ON CONSTRAINT pk_watesrhed_hydro_params
        DO UPDATE SET value = p_value;

    GET DIAGNOSTICS p_row_count = ROW_COUNT;
    return p_row_count;
end
$$;