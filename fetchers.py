"""Raw collectors for the three public construction sources.

Trafikverket needs an API key. Streamlit Cloud secrets are checked first, then
the process environment (populated from ``.env`` when running locally).
TED and OpenTender are public and do not use that key.

Nothing in this module authenticates to Dynamics 365 or Dataverse.
"""

from __future__ import annotations

import os
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

TRAFIKVERKET_URL = "https://api.trafikinfo.trafikverket.se/v2/data.json"
TED_URL = "https://api.ted.europa.eu/v3/notices/search"
OPENTENDER_URL = "https://opentender.eu/api/se/search"

USER_AGENT = "HeidelbergSwedenRMC-Staging/1.0"
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
    """GET municipal construction notices from the OpenTender Sweden search API."""
    response = _request(
        "GET",
        OPENTENDER_URL,
        params={"Sector": "Construction"},
        headers={
            "Accept": "application/json",
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
