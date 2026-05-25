CREATE VIEW lakehouse.lh_stg_da.simpfy_challenges_vw SECURITY INVOKER AS

SELECT *

FROM

  lakehouse.lh_corp_simpfy.mv_challenges;

 

 

CREATE VIEW lakehouse.lh_stg_da.simpfy_kpi_value SECURITY INVOKER AS

SELECT

  project_version_id

, name

, kpivalue

FROM

  lakehouse.lh_corp_simpfy.mv_kpi_value;

 

 

CREATE VIEW lakehouse.lh_stg_da.simpfy_projects_vw SECURITY INVOKER AS

SELECT

  project_version_id

, project_id

, project_name

, project_type

, project_leader_name

, description

, general_impact_summary

, segment_id

, kpi_name

, segment_name

, segment_name_abbreviated

, segment_type

, business_unit_id

, business_units_name

, project_stage

, enabler_type_name

, pillar_enabler_name

, pillars

, enablers

, corp_initiative_id

, corp_initiative_name

, segment_initiative_name

, segment_initiative_id

, country_id

, project_country

, business_unit_name

, CAST(planned_start_date AS varchar) planned_start_date

, start_year

, CAST(projected_end_date AS varchar) projected_end_date

, end_year

, project_status

, project_type_id

, project_leader_full_name

, co_project

, stage_id

, total_cost

, socar_share

, yearly_breakdown_is_unknown

, project_completed

, is_latest_version

, is_latest_confirmed_version

, key_areas_of_concern

, milestone_number

, milestone_name

, milestone_description

, CAST(milestone_target_date AS varchar) milestone_target_date

, CAST(milestone_completion_date AS varchar) milestone_completion_date

, milestone_status

, milestone_reason_for_delay

, milestone_is_confirmed

, milestone_id

, milestone_impact

, project_years

, project_years_total_cost

, comment_year

, comment_quarter

, year_and_quarter

, comment

, challenge_type

, challenge_type_abbreviated

, key_highlights

, key_problem_areas

FROM

  lakehouse.lh_corp_simpfy.mv_projects;