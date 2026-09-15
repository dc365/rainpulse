-- Read-only inventory; dates are not evidence of clear-sky conditions.
SELECT radar_id,
       count(*) AS registered_scans,
       count(DISTINCT (volume_start_time AT TIME ZONE 'Asia/Shanghai')::date) AS registered_days,
       min(volume_start_time) AS earliest_utc,
       max(volume_start_time) AS latest_utc
FROM radar_scans
WHERE radar_id IN ('z9591', 'z9593', 'z9598', 'z9599')
GROUP BY radar_id
ORDER BY radar_id;
