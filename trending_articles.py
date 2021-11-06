#!/home/neilpquinn-wmf/.conda/envs/trending_articles/bin/python3
# coding: utf-8

import json
import os
from pathlib import Path
import subprocess

import pandas as pd
import requests
from requests_oauthlib import OAuth1
import wmfdata as wmf

from secrets import oauth_config

# A CSV holding past trending articles, for rate-limiting frequent entries and as a log
ARCHIVE_FILE = "/home/neilpquinn-wmf/2021-KaiOS-app-homepage-content-suggestions/trending_articles.csv"
QUERY_FILE = "/home/neilpquinn-wmf/2021-KaiOS-app-homepage-content-suggestions/trending_articles_for_country_day.sql"

# Mapping of trending article lists to recommendation pages
# Every list goes on its own country's page for production plus others for testing
LISTS = {
    "IN": ["IN"],
    # Nigeria list
    "NG": ["NG", "KE", "PT"],
    # Pakistan list
    "PK": ["PK", "DE"],
    # Tanzania list
    "TZ": ["TZ", "US", "PR"],
    # Uganda list
    "UG": ["UG", "CA"]
}

BAD_RECOMMENDATIONS = [
    "-",
    ".xxx",
    "Brazzers",
    "Main_Page",
    "News",
    "Pornography",
    "Sex",
    "XHamster",
    "XVideos",
    "XXX",
    "XXX_(film_series)",
    "XXX:_Return_of_Xander_Cage",
    "XXXTentacion",
    "XXXX"
]

USER_AGENT = "Bot for https://phabricator.wikimedia.org/T271312"

REST_API_URL = "https://en.wikipedia.org/api/rest_v1/"
REST_API_SESSION = requests.Session()
REST_API_SESSION.headers.update({"User-Agent": USER_AGENT})

ACTION_API_URL = "https://www.mediawiki.org/w/api.php"
ACTION_API_SESSION = requests.Session()
ACTION_API_SESSION.auth = OAuth1(
    oauth_config["consumer_token"],
    oauth_config["consumer_secret"],
    oauth_config["access_token"],
    oauth_config["access_secret"]
)
ACTION_API_SESSION.headers.update({"User-Agent": USER_AGENT})
ACTION_API_SESSION.params.update({
    "format": "json",
    "formatversion": 2
})
CSRF_TOKEN  = ACTION_API_SESSION.get(ACTION_API_URL, params = {
    "action": "query",
    "meta": "tokens", 
    "type": "csrf"
}).json()["query"]["tokens"]["csrftoken"]

def sql_tuple(i):
    """
    Making an SQL 'tuple', for use in an IN clause, is hard. Doing it manually using 
    `", ".join` requires a lot of messing around with quote marks and escaping. Using the
    string representation of a Python tuple *almost* works, but fails when there's just
    one element, because SQL doesn't accept the trailing comma that Python uses.
    What we really want is the string representation of a Python list, but using parentheses
    instead of brackets. This function turns an iterable into just that.
    """
    # Transform other iterables into lists, raising errors for non-iterables
    if type(i) != list:
        i = [x for x in i]
    
    # Don't return empty SQL tuples, since they cause syntax errors 
    if len(i) == 0:
        return None

    list_repr = repr(i)

    return "(" + list_repr[1:-1] + ")"

def get_trending_articles(country, year, month, day, recently_trending):
    query = Path(QUERY_FILE).read_text()

    if recently_trending:
        not_recently_trending_clause = f"AND canonical_title NOT IN {recently_trending}"
    else:
        not_recently_trending_clause = ""

    formatted_query = query.format(
        country=country,
        year=year,
        month=month,
        day=day,
        bad_recommendations=sql_tuple(BAD_RECOMMENDATIONS),
        not_recently_trending_clause=not_recently_trending_clause
    )

    results = wmf.spark.run(formatted_query, session_type="yarn-large")
    results["date"] = pd.to_datetime(results["date"])
    
    return results
    
def post_list(page_title, list_date, page_content):
    r = ACTION_API_SESSION.post(ACTION_API_URL, data={
        "action": "edit",
        "nocreate": 1,
        "summary": f"Update with trending articles from {list_date.strftime('%Y-%m-%d')}",
        "text": page_content,
        "title": page_title,
        "token": CSRF_TOKEN
    })

def update_lists(lists, archive, year, month, day):
    date = pd.Timestamp(year, month, day)
    
    for country in lists:
        recent_start = date - pd.DateOffset(days=7)

        if archive is None:
            recently_trending = None
        else:
            recently_trending = archive.query(
                "country == @country"
                " & date >= @recent_start"
                " & date < @date"
                " & rank <= 5"
            )["article"].pipe(sql_tuple)

        results = get_trending_articles(country, year, month, day, recently_trending)
        records = results.to_dict("records")
        for r in records:
            # We need to make sure the title is percent-encoded
            encoded_title = requests.utils.quote(r["article"], safe='')
            summary_data = REST_API_SESSION.get(REST_API_URL + "page/summary/" + encoded_title).json()

            # This title uses spaces rather than underscores and, if the initial title was a redirect, will be the destination
            r["title"] = summary_data["title"]

            # extract_html will be an empty string if there is no extract
            if summary_data["extract_html"]:
                r["description"] = summary_data["extract_html"]

            try:
                r["image_url"] = summary_data["thumbnail"]["source"]
            # Some articles have no thumbnail
            except KeyError:
                pass

        def client_format(d):
            d_2 = {"title": d["title"]}
            if d.get("description"):
                d_2["description"] = d["description"]
            if d.get("image_url"):
                d_2["imageUrl"] = d["image_url"]
            return d_2

        page_content = json.dumps([client_format(r) for r in records])

        for page in lists[country]:
            page_title = "Wikipedia_for_KaiOS/engagement1/trending/en/" + page.lower()
            post_list(page_title, date, page_content)

        results = pd.DataFrame.from_records(records)

        try:
            archive = archive.append(results, ignore_index=True)
        except AttributeError:
            archive = results

    return archive

if __name__ == "__main__":
    NOW = pd.Timestamp.now()
    YESTERDAY = NOW - pd.DateOffset(days=1)

    # Helps navigation of logs when running this as a cron job. For some reason, output from 
    # the print function doesn't appear in the logs, so calling echo as a workaround.
    subprocess.call([
        "echo", 
        f"###### trending_articles.py at {NOW.strftime('%Y-%m-%d %H:%M:%S')} ######"
    ])

    # If this is being run as analytics-product, run `kerberos-run-command` to ensure there is a 
    # Kerberos ticket, since PySpark will not use the keytab automatically
    if os.getenv('LOGNAME') == "analytics-product":
        subprocess.call([
            "/usr/local/bin/kerberos-run-command", 
            "analytics-product", 
            "echo", 
            "'Running kerberos-run-command to ensure analytics-product has a Kerberos ticket...'"
        ])
    
    try:
        archive = pd.read_csv(ARCHIVE_FILE, parse_dates=["date"])
    except FileNotFoundError:
        archive = None

    archive = update_lists(LISTS, archive, YESTERDAY.year, YESTERDAY.month, YESTERDAY.day)

    (
        archive
        .drop_duplicates(subset=["date", "country", "rank"])
        .sort_values(["date", "country", "rank"])
        .to_csv(ARCHIVE_FILE, index=False)
    )

    # Hard exit; otherwise, a PySpark thread blocks exit, even with sys.exit()
    os._exit(os.EX_OK)