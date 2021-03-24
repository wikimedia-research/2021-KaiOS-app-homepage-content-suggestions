-- Written for Spark 2.4.4
WITH views_by_page_title AS (
    SELECT
        page_id,
        page_title,
        SUM(view_count) AS views,
        SUM(IF(access_method = 'desktop', view_count, 0)) AS computer_views
    FROM wmf.pageview_hourly
    WHERE
        agent_type = 'user'
        AND project = 'en.wikipedia'
        AND namespace_id = 0
        AND country_code = '{country}'
        AND year = {year}
        AND month = {month}
        AND day = {day}
    GROUP BY
        page_id,
        page_title
    ORDER BY
        page_id ASC,
        views ASC
), views_by_canonical_title AS (
    SELECT
        -- Because of the ORDER BY in the previous CTE, the last title will be the one with the most views
        LAST(page_title) AS canonical_title,
        SUM(views) AS views,
        SUM(computer_views) / SUM(views) AS views_computer_proportion
    FROM views_by_page_title
    GROUP BY page_id
), filtered_views_by_canonical_title AS (
    SELECT *
    FROM views_by_canonical_title
    WHERE
        canonical_title NOT IN {bad_recommendations}
        AND views_computer_proportion BETWEEN 0.1 AND 0.9
        {not_recently_trending_clause}
), trending AS (
    SELECT
         -- If there are ties, ROW_NUMBER will break them so we can always get the same number of articles
        ROW_NUMBER() OVER (
            ORDER BY views DESC
        ) AS rank,
        canonical_title AS article,
        views,
        views_computer_proportion
    FROM filtered_views_by_canonical_title
)
SELECT
    DATE(CONCAT_WS("-", {year}, LPAD(CAST({month} AS STRING), 2, '0'), LPAD(CAST({day} AS STRING), 2, '0'))) AS date,
    '{country}' AS country,
    *
FROM trending
WHERE
    rank <= 6