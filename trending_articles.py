#!/usr/bin/env python
# coding: utf-8

import json
from pathlib import Path

import mwapi
import pandas as pd
import requests
from requests_oauthlib import OAuth1
import wmfdata as wmf
from wmfdata.utils import (
    pd_display_all, 
    print_err
)

from secrets import oauth_config


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


YEAR = 2021
MONTH = 3
DAY = 19
DATE = pd.Timestamp(YEAR, MONTH, DAY)

# Mapping of trending article lists to recommendation pages
# Every list goes on its own country's page for production plus others for testing
COUNTRIES = {
    # Nigeria list
    "NG": ["NG", "KE", "PT"],
    # Pakistan list
    "PK": ["PK", "IN", "DE"],
    # Tanzania list
    "TZ": ["TZ", "US"],
    # Uganda list
    "UG": ["UG", "CA"]
}

BAD_RECOMMENDATIONS = sql_tuple([
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
])

# Custom user agent for API requests
USER_AGENT = "Bot for https://phabricator.wikimedia.org/T271312"
session = requests.Session()
session.headers.update({"User-Agent": USER_AGENT})

action_api_session = mwapi.Session("https://www.mediawiki.org", user_agent = USER_AGENT)

auth = OAuth1(
    oauth_config["consumer_token"],
    oauth_config["consumer_secret"],
    oauth_config["access_token"],
    oauth_config["access_secret"]
)

def get_token():
    r = action_api_session.get(
        action="query", 
        meta="tokens", 
        type="csrf", 
        auth=auth
    )

    return r["query"]["tokens"]["csrftoken"]

def action_api_get(*args, **kwargs):
    return action_api_session.get(
        *args,
        format = "json",
        formatversion = 2,
        auth = auth,
        **kwargs
    )

def action_api_post(*args, **kwargs):
    return action_api_session.post(
        *args,
        format = "json",
        formatversion = 2,
        auth = auth,
        token = get_token(),
        **kwargs
    )


try:
    trending_articles = pd.read_csv("trending_articles.csv", parse_dates=["date"])
except FileNotFoundError:
    trending_articles = None

query = Path("trending_articles_for_country_day.sql").read_text()

for country in COUNTRIES:
    recent_start = DATE - pd.DateOffset(days=7)
    
    if trending_articles is None:
        recently_trending = None
    else:
        recently_trending = trending_articles.query(
            "country == @country"
            " & date >= @recent_start"
            " & date < @DATE"
            " & rank <= 5"
        )["article"].pipe(sql_tuple)
     
    if recently_trending:
        not_recently_trending_clause = f"AND canonical_title NOT IN {recently_trending}"
    else:
        not_recently_trending_clause = ""

    formatted_query = query.format(
      country=country,
      year=YEAR,
      month=MONTH,
      day=DAY,
      bad_recommendations=BAD_RECOMMENDATIONS,
      not_recently_trending_clause=not_recently_trending_clause
    )

    results = wmf.spark.run(formatted_query, session_type="yarn-large")
    results["date"] = pd.to_datetime(results["date"])
    
    records = results.to_dict("records")
    for r in records:
        summary_data = session.get("https://en.wikipedia.org/api/rest_v1/page/summary/" + r["article"]).json()
    
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
    
    for page in COUNTRIES[country]:
        page_title = "Wikipedia_for_KaiOS/engagement1/trending/en/" + page.lower()
        action_api_post(
            action="edit",
            nocreate=1,
            summary=f"Update with trending articles from {DATE.strftime('%Y-%m-%d')}",
            text=page_content,
            title=page_title
        )
    
    results = pd.DataFrame.from_records(records)

    try:
        trending_articles = trending_articles.append(results, ignore_index=True)
    except AttributeError:
        trending_articles = results

trending_articles.sort_values(["date", "country", "rank"]).to_csv("trending_articles.csv", index=False)
