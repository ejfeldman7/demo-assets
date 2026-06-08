-- Test queries for sample_metric_view
-- These queries validate the correctness of the metric view after deployment

-- Test 1: Basic functionality - ensure the view returns data
-- Expected: Should return at least 1 row
SELECT COUNT(*) as row_count
FROM `{{ catalog }}`.`{{ schema }}`.`sample_metric_view`;

-- Test 2: Verify pct_ok is within valid range (0-1)  
-- Expected: Should return 0 rows (no violations)  
SELECT SUM(CASE WHEN pct_ok < 0 OR pct_ok > 1 THEN 1 ELSE 0 END) as invalid_pct_ok_count
FROM (
  SELECT MEASURE(pct_ok) as pct_ok
  FROM `{{ catalog }}`.`{{ schema }}`.`sample_metric_view`
) t;

-- Test 3: Verify pct_failing is within valid range (0-1)
-- Expected: Should return 0 rows (no violations)
SELECT SUM(CASE WHEN pct_failing < 0 OR pct_failing > 1 THEN 1 ELSE 0 END) as invalid_pct_failing_count
FROM (
  SELECT MEASURE(pct_failing) as pct_failing
  FROM `{{ catalog }}`.`{{ schema }}`.`sample_metric_view`
) t;

-- Test 4: Verify percentages sum appropriately
-- Expected: For each turbine/location, pct_ok + pct_failing should be <= 1.01 (allowing for rounding)
SELECT SUM(CASE WHEN (pct_ok + pct_failing) > 1.01 THEN 1 ELSE 0 END) as percentage_sum_violations
FROM (
  SELECT 
    MEASURE(pct_ok) as pct_ok,
    MEASURE(pct_failing) as pct_failing
  FROM `{{ catalog }}`.`{{ schema }}`.`sample_metric_view`
) t;

-- Test 5: Data completeness - ensure we have data for expected turbines
-- Expected: Should return at least 5 distinct turbine_ids (adjust based on test data)
SELECT COUNT(DISTINCT turbine_id) as unique_turbines
FROM `{{ catalog }}`.`{{ schema }}`.`sample_metric_view`;
