"""Raw collectors for public construction notices.

Sweden reads Trafikverket, EU TED, and OpenTender. Trafikverket needs an API
key. Streamlit Cloud secrets are checked first, then the process environment
(populated from ``.env`` when running locally). TED and OpenTender are public
and do not use that key.

The United Kingdom, Belgium, and the Netherlands use keyless public JSON only.
Nothing in this module authenticates to Dynamics 365, Dataverse, or a paid
lead service.
"""

from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

TRAFIKVERKET_URL = "https://api.trafikinfo.trafikverket.se/v2/data.json"
TED_URL = "https://api.ted.europa.eu/v3/notices/search"
OPENTENDER_URL = "https://api.opentender.eu/api/se/tender/search"
OPENTENDER_PAGE_SIZE = 80

USER_AGENT = "MR-Project-Staging/1.0"
REQUEST_TIMEOUT = 90

# Swedish works notices published since 1 September 2026 whose procedure value
# clears the approximate EUR 5.4 million works screen when the currency is SEK
# (5.4e6 * 11.5). transformer.py applies the same screen again by currency.
TED_QUERY = (
    "buyer-country=SWE AND "
    "(classification-cpv=45200000 OR classification-cpv=44114000) AND "
    "publication-date>=20260901 AND estimated-value-proc>=62100000"
)
TED_FIELDS = [
    "publication-number",
    "publication-date",
    "notice-title",
    "notice-type",
    "buyer-name",
    "buyer-city",
    "buyer-post-code",
    "buyer-country-sub",
    "classification-cpv",
    "estimated-value-proc",
    "estimated-value-cur-proc",
    "estimated-value-lot",
    "estimated-value-cur-lot",
    "deadline-receipt-tender-date-lot",
    "contract-conclusion-date",
    "place-of-performance-city-proc",
    "place-of-performance-post-code-proc",
    "place-of-performance-subdiv-proc",
]
TED_PAGE_SIZE = 100
TED_MAX_PAGES = 3


class FetchError(RuntimeError):
    """A public source could not be read. The message never includes secrets."""


def get_trafikverket_api_key() -> str:
    """Read the Trafikverket key from Streamlit secrets, then the environment."""
    key = _read_streamlit_secret("TRAFIKVERKET_API_KEY")
    if not key:
        key = os.environ.get("TRAFIKVERKET_API_KEY")
    if key is None or not str(key).strip():
        raise FetchError(
            "TRAFIKVERKET_API_KEY is not set. Add it to Streamlit secrets or the environment."
        )
    return str(key).strip()


def fetch_trafikverket() -> dict:
    """POST a Situation query for current Swedish roadworks."""
    api_key = get_trafikverket_api_key()
    payload = _trafikverket_request_xml(api_key)
    response = _request(
        "POST",
        TRAFIKVERKET_URL,
        data=payload.encode("utf-8"),
        headers={
            "Content-Type": "text/xml",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    body = _json_or_raise(response, "Trafikverket")
    error_message = _trafikverket_error(body)
    if error_message:
        raise FetchError(f"Trafikverket rejected the query: {error_message}")
    return body


def fetch_ted() -> dict:
    """POST a TED Search API query for Swedish works and concrete notices."""
    notices: list[dict] = []
    for page in range(1, TED_MAX_PAGES + 1):
        response = _request(
            "POST",
            TED_URL,
            json={
                "query": TED_QUERY,
                "fields": TED_FIELDS,
                "limit": TED_PAGE_SIZE,
                "page": page,
                "scope": "ALL",
                "paginationMode": "PAGE_NUMBER",
                "onlyLatestVersions": True,
            },
            headers={
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
            },
        )
        body = _json_or_raise(response, "EU TED")
        page_notices = body.get("notices") or []
        if not isinstance(page_notices, list):
            raise FetchError("EU TED returned an unexpected notices payload.")
        notices.extend(page_notices)
        if len(page_notices) < TED_PAGE_SIZE:
            break
    return {"notices": notices}


def fetch_opentender() -> dict | list:
    """POST a construction-works search to the OpenTender Sweden API.

    The public site calls ``api.opentender.eu``, not ``opentender.eu/api/se/search``.
    Results are the newest works notices whose text matches construction.
    """
    response = _request(
        "POST",
        OPENTENDER_URL,
        json={
            "filters": [
                {"field": "freetext", "value": "construction", "type": "text"},
                {"field": "supplyType", "value": ["WORKS"], "type": "term"},
            ],
            "totalHitsWithoutFilters": True,
            "size": OPENTENDER_PAGE_SIZE,
            "sort": {"field": "tender_publication_date", "order": "DESC"},
            "lang": "en",
        },
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Origin": "https://opentender.eu",
            "User-Agent": USER_AGENT,
        },
    )
    return _json_or_raise(response, "OpenTender")


def _read_streamlit_secret(name: str) -> str | None:
    try:
        import streamlit as st

        value = st.secrets[name]
    except Exception:
        return None
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _trafikverket_request_xml(api_key: str) -> str:
    safe_key = (
        api_key.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
    return f"""<REQUEST>
  <LOGIN authenticationkey="{safe_key}" />
  <QUERY objecttype="Situation" namespace="road.trafficinfo" schemaversion="1.6" limit="80" orderby="ModifiedTime desc">
    <FILTER>
      <EQ name="Deviation.MessageType" value="Vägarbete" />
    </FILTER>
    <INCLUDE>Id</INCLUDE>
    <INCLUDE>Deleted</INCLUDE>
    <INCLUDE>PublicationTime</INCLUDE>
    <INCLUDE>ModifiedTime</INCLUDE>
    <INCLUDE>Deviation.Id</INCLUDE>
    <INCLUDE>Deviation.Header</INCLUDE>
    <INCLUDE>Deviation.Message</INCLUDE>
    <INCLUDE>Deviation.LocationDescriptor</INCLUDE>
    <INCLUDE>Deviation.StartTime</INCLUDE>
    <INCLUDE>Deviation.EndTime</INCLUDE>
    <INCLUDE>Deviation.MessageType</INCLUDE>
    <INCLUDE>Deviation.MessageTypeValue</INCLUDE>
    <INCLUDE>Deviation.MessageCode</INCLUDE>
    <INCLUDE>Deviation.RoadNumber</INCLUDE>
    <INCLUDE>Deviation.CountyNo</INCLUDE>
    <INCLUDE>Deviation.Geometry.Point.WGS84</INCLUDE>
    <INCLUDE>Deviation.Geometry.WGS84</INCLUDE>
  </QUERY>
</REQUEST>"""


def _trafikverket_error(body: dict) -> str | None:
    results = body.get("RESPONSE", {}).get("RESULT", [])
    if not isinstance(results, list):
        return None
    for result in results:
        error = result.get("ERROR") if isinstance(result, dict) else None
        if isinstance(error, dict) and error.get("MESSAGE"):
            return str(error["MESSAGE"])[:300]
    return None


def _json_or_raise(response: requests.Response, source: str) -> dict | list:
    if response.status_code >= 400:
        raise FetchError(f"{source} returned HTTP {response.status_code}.")
    content_type = response.headers.get("Content-Type", "")
    if "json" not in content_type.lower() and not response.text.lstrip().startswith(("{", "[")):
        raise FetchError(
            f"{source} did not return JSON. Automated access to that host may be blocked."
        )
    try:
        return response.json()
    except ValueError as exc:
        raise FetchError(f"{source} returned a body that is not valid JSON.") from exc


def _request(method: str, url: str, **kwargs) -> requests.Response:
    kwargs.setdefault("timeout", REQUEST_TIMEOUT)
    last_error: Exception | None = None
    for _attempt in range(2):
        try:
            return requests.request(method, url, **kwargs)
        except (requests.Timeout, requests.ConnectionError) as exc:
            last_error = exc
    raise FetchError(f"{url} could not be reached ({last_error.__class__.__name__}).")


# Same publication floor as the Swedish TED query (1 September 2026). The UK
# APIs cannot filter by CPV, so each refresh walks newest-first and stops
# after a page budget instead of reading every notice in the window.
NOTICE_WINDOW_START = date(2026, 9, 1)
UK_FIND_A_TENDER_URL = "https://www.find-tender.service.gov.uk/api/1.0/ocdsReleasePackages"
UK_CONTRACTS_FINDER_URL = (
    "https://www.contractsfinder.service.gov.uk/Published/Notices/OCDS/Search"
)
UK_OCDS_PAGE_LIMIT = 100
UK_FIND_A_TENDER_PAGE_BUDGET = 36
UK_CONTRACTS_FINDER_PAGE_BUDGET = 36
TENDERNED_URL = "https://www.tenderned.nl/papi/tenderned-rs-tns/v2/publicaties"
TENDERNED_PAGE_SIZE = 100
TENDERNED_MAX_PAGES = 10
TED_MARKET_MAX_PAGES = 12
JSON_HEADERS = {"Accept": "application/json", "User-Agent": USER_AGENT}


def fetch_uk_find_a_tender() -> dict:
    """GET Find a Tender OCDS releases. A combined stages value returns no rows."""
    releases, notes = _fetch_uk_ocds(
        "UK Find a Tender",
        UK_FIND_A_TENDER_URL,
        "updatedFrom",
        "updatedTo",
        ("tender", "award", "planning"),
        UK_FIND_A_TENDER_PAGE_BUDGET,
    )
    return {"releases": releases, "_notes": notes}


def fetch_uk_contracts_finder() -> dict:
    """GET Contracts Finder OCDS releases. No API key."""
    releases, notes = _fetch_uk_ocds(
        "UK Contracts Finder",
        UK_CONTRACTS_FINDER_URL,
        "publishedFrom",
        "publishedTo",
        ("tender,award,planning",),
        UK_CONTRACTS_FINDER_PAGE_BUDGET,
    )
    return {"releases": releases, "_notes": notes}


def fetch_be_ted() -> dict:
    """POST the TED Search API for Belgian works and concrete notices."""
    return _fetch_ted_market("BEL", "EU TED Belgium")


def fetch_nl_ted() -> dict:
    """POST the TED Search API for Dutch works and concrete notices."""
    return _fetch_ted_market("NLD", "EU TED Netherlands")


def fetch_nl_tenderned() -> dict:
    """GET TenderNed's public JSON publications. The XML notice service needs a login."""
    notes: list[str] = []
    rows = _tenderned_search(
        {"publicatieDatumVanaf": NOTICE_WINDOW_START.isoformat(), "typeOpdracht": "W"},
        notes,
        "works",
    )
    concrete = _tenderned_search(
        {"publicatieDatumVanaf": NOTICE_WINDOW_START.isoformat(), "cpvCodes": "44114000-7"},
        notes,
        "concrete",
    )
    by_id: dict[str, dict] = {}
    for row in rows + concrete:
        if not isinstance(row, dict) or not row.get("publicatieId"):
            continue
        key = str(row["publicatieId"])
        current = by_id.get(key)
        if current is None:
            by_id[key] = row
            continue
        if row.get("_matchedConcrete"):
            current["_matchedConcrete"] = True
    publications = list(by_id.values())
    failed = _attach_tenderned_details(publications)
    if failed:
        notes.append(
            "NL_TenderNed: "
            f"{failed} publication details did not return JSON. "
            "CPV and region were left empty on those rows."
        )
    return {"publicaties": publications, "_notes": notes}


def _fetch_ted_market(buyer_country: str, source: str) -> dict:
    """Works notices coded under CPV 45, plus concrete supplies (CPV 44114)."""
    query = (
        f"buyer-country={buyer_country} AND publication-date>=20260901 AND "
        "((contract-nature=works AND classification-cpv=45*) OR classification-cpv=44114*) "
        "SORT BY publication-number DESC"
    )
    notices: list[dict] = []
    total = None
    for page in range(1, TED_MARKET_MAX_PAGES + 1):
        response = _request(
            "POST",
            TED_URL,
            json={
                "query": query,
                "fields": list(TED_FIELDS),
                "limit": TED_PAGE_SIZE,
                "page": page,
                "scope": "ALL",
                "paginationMode": "PAGE_NUMBER",
                "onlyLatestVersions": True,
            },
            headers=JSON_HEADERS,
        )
        body = _json_or_raise(response, source)
        if not isinstance(body, dict):
            raise FetchError(f"{source} returned an unexpected payload.")
        page_notices = body.get("notices") or []
        if not isinstance(page_notices, list):
            raise FetchError(f"{source} returned an unexpected notices payload.")
        notices.extend(item for item in page_notices if isinstance(item, dict))
        if isinstance(body.get("totalNoticeCount"), int):
            total = body["totalNoticeCount"]
        if len(page_notices) < TED_PAGE_SIZE:
            break
    notes: list[str] = []
    if total is not None and total > len(notices):
        notes.append(
            f"{source}: TED reported {total} notices. "
            f"This run kept the newest {len(notices)}."
        )
    return {"notices": notices, "_notes": notes}


def _fetch_uk_ocds(
    source: str,
    url: str,
    start_key: str,
    end_key: str,
    stages: tuple[str, ...],
    page_budget: int,
) -> tuple[list[dict], list[str]]:
    releases: list[dict] = []
    seen: set[str] = set()
    notes: list[str] = []
    budget = page_budget
    pages_read = 0
    earliest = ""
    stopped_early = False
    windows = _notice_windows()
    for window_index, (start, end) in enumerate(windows):
        for stage_index, stage in enumerate(stages):
            params = {
                "limit": UK_OCDS_PAGE_LIMIT,
                start_key: _ocds_stamp(start),
                end_key: _ocds_stamp(end),
                "stages": stage,
            }
            batch, used, more, batch_earliest, walk_notes = _walk_ocds(
                source,
                url,
                params,
                budget,
                have_any=bool(releases),
            )
            notes.extend(walk_notes)
            budget -= used
            pages_read += used
            if batch_earliest and (not earliest or batch_earliest < earliest):
                earliest = batch_earliest
            for release in batch:
                if not isinstance(release, dict):
                    continue
                identity = str(release.get("id") or "")
                if identity and identity in seen:
                    continue
                if identity:
                    seen.add(identity)
                releases.append(release)
            more_work = more or window_index < len(windows) - 1 or stage_index < len(stages) - 1
            if more or (budget <= 0 and more_work):
                stopped_early = True
                break
        if stopped_early:
            break
    if stopped_early:
        reached = earliest or "the newest page"
        notes.append(
            f"{source}: the public search has no CPV filter. "
            f"Read {pages_read} pages from the newest notices back to {reached}. "
            "Older notices in the 1 September 2026 window were not read."
        )
    return releases, notes


def _walk_ocds(source: str, url: str, params: dict, budget: int, have_any: bool):
    collected: list[dict] = []
    used = 0
    more = False
    earliest = ""
    notes_holder: list[str] = []
    next_url: str | None = url
    request_params: dict | None = params
    while next_url and used < budget:
        try:
            response = _get_public_json(next_url, request_params)
            body = _json_or_raise(response, source)
        except FetchError as exc:
            if not have_any and used == 0 and not collected:
                raise
            notes_holder.append(str(exc))
            more = True
            break
        if not isinstance(body, dict):
            raise FetchError(f"{source} returned an unexpected payload.")
        batch = body.get("releases") or []
        if not isinstance(batch, list):
            raise FetchError(f"{source} returned an unexpected releases payload.")
        used += 1
        collected.extend(batch)
        for release in batch:
            if isinstance(release, dict) and release.get("date"):
                day = str(release["date"])[:10]
                if day and (not earliest or day < earliest):
                    earliest = day
        link = body.get("links") or {}
        next_url = link.get("next") if isinstance(link, dict) else None
        request_params = None
        if not batch or not next_url:
            next_url = None
            break
        time.sleep(0.4)
    if next_url:
        more = True
    return collected, used, more, earliest, notes_holder


def _get_public_json(url: str, params: dict | None) -> requests.Response:
    """GET JSON, honoring a short pause when a public API asks us to slow down."""
    response = _request("GET", url, params=params, headers=JSON_HEADERS)
    for _attempt in range(3):
        if response.status_code != 429:
            return response
        try:
            delay = int(response.headers.get("Retry-After") or 8)
        except ValueError:
            delay = 8
        time.sleep(min(max(delay, 2), 40))
        response = _request("GET", url, params=params, headers=JSON_HEADERS)
    return response


def _notice_windows(days: int = 7) -> list[tuple[date, date]]:
    start = NOTICE_WINDOW_START
    end = date.today() + timedelta(days=1)
    if end <= start:
        end = start + timedelta(days=1)
    windows: list[tuple[date, date]] = []
    cursor = start
    while cursor < end:
        nxt = min(cursor + timedelta(days=days), end)
        windows.append((cursor, nxt))
        cursor = nxt
    windows.reverse()
    return windows


def _ocds_stamp(day: date) -> str:
    return f"{day.isoformat()}T00:00:00"


def _tenderned_search(params: dict, notes: list[str], label: str) -> list[dict]:
    rows: list[dict] = []
    stopped_early = False
    for page in range(TENDERNED_MAX_PAGES):
        response = _get_public_json(
            TENDERNED_URL,
            {"page": page, "size": TENDERNED_PAGE_SIZE, **params},
        )
        body = _json_or_raise(response, "TenderNed")
        if not isinstance(body, dict):
            raise FetchError("TenderNed returned an unexpected payload.")
        content = body.get("content") or []
        if not isinstance(content, list):
            raise FetchError("TenderNed returned an unexpected publications payload.")
        for item in content:
            if isinstance(item, dict):
                row = dict(item)
                if label == "concrete":
                    row["_matchedConcrete"] = True
                rows.append(row)
        if body.get("last") is True or len(content) < TENDERNED_PAGE_SIZE:
            break
        if page == TENDERNED_MAX_PAGES - 1:
            stopped_early = True
        time.sleep(0.1)
    else:
        stopped_early = True
    if stopped_early:
        notes.append(
            "NL_TenderNed: the public search was still paging after "
            f"{TENDERNED_MAX_PAGES} pages of {label} notices. Older rows were not read."
        )
    return rows


def _attach_tenderned_details(rows: list[dict]) -> int:
    """Fill CPV and NUTS from the public detail call. List rows omit both."""
    failed = 0

    def load(row: dict):
        publication_id = row.get("publicatieId")
        try:
            response = _get_public_json(f"{TENDERNED_URL}/{publication_id}", None)
            detail = _json_or_raise(response, "TenderNed")
        except FetchError:
            return row, None
        return row, detail if isinstance(detail, dict) else None

    if not rows:
        return 0
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(load, row) for row in rows]
        for future in as_completed(futures):
            row, detail = future.result()
            if not detail:
                failed += 1
                continue
            if detail.get("cpvCodes"):
                row["cpvCodes"] = detail["cpvCodes"]
            if detail.get("nutsCodes"):
                row["nutsCodes"] = detail["nutsCodes"]
            if detail.get("formType"):
                row["formType"] = detail["formType"]
            if "isGegund" in detail:
                row["isGegund"] = detail["isGegund"]
            if isinstance(detail.get("aankondigingCode"), dict):
                row["aankondigingCode"] = detail["aankondigingCode"]
            if isinstance(detail.get("typeOpdrachtCode"), dict):
                row["typeOpdrachtCode"] = detail["typeOpdrachtCode"]
    return failed
