CREATE TABLE customer_sample_cust_lineage AS
        SELECT
            State,
            Sex AS Gender,
            CASE 
                WHEN Age < 18 THEN 'Under 18'
                WHEN Age BETWEEN 18 AND 29 THEN '18-29'
                WHEN Age BETWEEN 30 AND 44 THEN '30-44'
                WHEN Age BETWEEN 45 AND 59 THEN '45-59'
                ELSE '60+'
            END AS avg_age,
            COUNT(*) AS customer_count,
            AVG(Age) AS avg_age_value
        FROM dbo.target_data   
        GROUP BY State, Sex,
            CASE 
                WHEN Age < 18 THEN 'Under 18'
                WHEN Age BETWEEN 18 AND 29 THEN '18-29'
                WHEN Age BETWEEN 30 AND 44 THEN '30-44'
                WHEN Age BETWEEN 45 AND 59 THEN '45-59'
                ELSE '60+'
            END
        ORDER BY State, Sex, avg_age;