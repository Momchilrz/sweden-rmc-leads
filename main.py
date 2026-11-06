"""ETL orchestration for the local RMC lead staging database."""

from __future__ import annotations

import argparse
import logging

from database import record_etl_run, upsert_leads
from fetchers import (
    FetchError,
    fetch_be_ted,
    fetch_nl_ted,
    fetch_nl_tenderned,
    fetch_opentender,
    fetch_ted,
    fetch_trafikverket,
    fetch_uk_contracts_finder,
    fetch_uk_find_a_tender,
)
from schedule import scheduled_run_is_due
from transformer import (
    transform_be_ted,
    transform_nl_ted,
    transform_nl_tenderned,
    transform_opentender,
    transform_ted,
    transform_trafikverket,
    transform_uk_contracts_finder,
    transform_uk_find_a_tender,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("rmc_etl")

SOURCES = (
    ("Trafikverket", fetch_trafikverket, transform_trafikverket),
    ("EU_TED", fetch_ted, transform_ted),
    ("OpenTender", fetch_opentender, transform_opentender),
)

COUNTRY_ORDER = (
    "Sweden",
    "United Kingdom",
    "Belgium",
    "Netherlands",
)

SOURCE_LABELS = {
    "Sweden": (
        ("Trafikverket", "Trafikverket"),
        ("EU_TED", "EU TED"),
        ("OpenTender", "OpenTender"),
    ),
    "United Kingdom": (
        ("UK_Find_a_Tender", "Find a Tender"),
        ("UK_Contracts_Finder", "Contracts Finder"),
    ),
    "Belgium": (("BE_TED", "EU TED"),),
    "Netherlands": (
        ("NL_TED", "EU TED"),
        ("NL_TenderNed", "TenderNed"),
    ),
}

FETCH_SPINNERS = {
    "Sweden": "Fetching Trafikverket, TED, and OpenTender…",
    "United Kingdom": "Fetching Find a Tender and Contracts Finder…",
    "Belgium": "Fetching EU TED Belgium…",
    "Netherlands": "Fetching EU TED Netherlands and TenderNed…",
}

# Sources that were checked and are not callable as keyless JSON.
COUNTRY_GAPS = {
    "United Kingdom": (
        "UK_Planning was not fetched. planning.data.gov.uk lists planning-application, but the live JSON search returned no application records.",
        "Public Contracts Scotland was not fetched. The documented notices API did not return JSON because the TLS certificate could not be verified.",
        "Sell2Wales was not fetched. The documented notices call returned HTML, not JSON.",
        "Northern Ireland was not fetched. eTendersNI has no documented public JSON API.",
    ),
    "Belgium": (
        "BE_Public_Procurement was not fetched. The BOSA e-Procurement API requires a client credential, and the public site did not return a keyless JSON feed.",
    ),
}

COUNTRY_PIPELINES = {
    "Sweden": SOURCES,
    "United Kingdom": (
        ("UK_Find_a_Tender", fetch_uk_find_a_tender, transform_uk_find_a_tender),
        ("UK_Contracts_Finder", fetch_uk_contracts_finder, transform_uk_contracts_finder),
    ),
    "Belgium": (("BE_TED", fetch_be_ted, transform_be_ted),),
    "Netherlands": (
        ("NL_TED", fetch_nl_ted, transform_nl_ted),
        ("NL_TenderNed", fetch_nl_tenderned, transform_nl_tenderned),
    ),
}


def run_etl_pipeline(country: str = "Sweden") -> dict:
    """Fetch one country. Sweden still runs Trafikverket, TED, then OpenTender."""
    summary = _run_country(country)
    if country == "Sweden":
        record_etl_run(summary)
    return summary


def run_scheduled_pipelines() -> dict:
    """Refresh every country. One country failing does not stop the others.

    The Swedish morning slot is recorded from the Sweden run only, as before.
    """
    merged = _empty_summary("*")
    merged["by_country"] = {}
    for country in COUNTRY_ORDER:
        try:
            summary = run_etl_pipeline(country)
        except Exception as exc:
            failed = _empty_summary(country)
            failed["errors"].append(f"{country}: {exc.__class__.__name__}")
            merged["by_country"][country] = failed
            merged["errors"].append(failed["errors"][0])
            continue
        merged["by_country"][country] = summary
        merged["fetched"].update(summary.get("fetched") or {})
        merged["upserted"].update(summary.get("upserted") or {})
        merged["errors"].extend(summary.get("errors") or [])
        merged["notes"].extend(summary.get("notes") or [])
        merged["total_upserted"] += summary.get("total_upserted") or 0
    return merged


def _run_country(country: str) -> dict:
    if country not in COUNTRY_PIPELINES:
        raise ValueError(f"Unknown country: {country}")
    summary = _empty_summary(country)
    for source_name, fetcher, transformer in COUNTRY_PIPELINES[country]:
        try:
            logger.info("Fetching %s", source_name)
            payload = fetcher()
            notes = []
            if isinstance(payload, dict) and isinstance(payload.get("_notes"), list):
                notes = [str(note) for note in payload.get("_notes") if note]
            leads = transformer(payload)
            written = upsert_leads(leads)
            summary["fetched"][source_name] = len(leads)
            summary["upserted"][source_name] = written
            summary["total_upserted"] += written
            summary["notes"].extend(notes)
            logger.info("%s staged %s leads", source_name, written)
        except FetchError as exc:
            message = f"{source_name}: {exc}"
            summary["errors"].append(message)
            summary["fetched"][source_name] = 0
            summary["upserted"][source_name] = 0
            logger.error(message)
        except Exception as exc:
            message = f"{source_name}: {exc.__class__.__name__}: {exc}"
            summary["errors"].append(message)
            summary["fetched"][source_name] = 0
            summary["upserted"][source_name] = 0
            logger.exception(message)
    summary["notes"].extend(COUNTRY_GAPS.get(country, ()))
    return summary


def _empty_summary(country: str) -> dict:
    return {
        "country": country,
        "fetched": {},
        "upserted": {},
        "errors": [],
        "notes": [],
        "total_upserted": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh the local RMC staging database.")
    parser.add_argument(
        "--scheduled",
        action="store_true",
        help="Pull every country when the 06:00 Europe/Stockholm slot is still open.",
    )
    parser.add_argument(
        "--country",
        choices=COUNTRY_ORDER,
        default="Sweden",
        help="Country to refresh when this is not the scheduled run.",
    )
    args = parser.parse_args()
    if args.scheduled and not scheduled_run_is_due():
        print("Swedish morning slot is already covered.")
        return 0
    if args.scheduled:
        result = run_scheduled_pipelines()
        print("Staging complete")
        for country, summary in result.get("by_country", {}).items():
            print(country)
            for source, count in summary.get("upserted", {}).items():
                print(f"  {source}: {count}")
            for note in summary.get("notes") or []:
                print(f"  - {note}")
            for error in summary.get("errors") or []:
                print(f"  - {error}")
        print(f"  total upserted this run: {result['total_upserted']}")
        return 0
    result = run_etl_pipeline(args.country)
    print(f"Staging complete for {args.country}")
    for source, count in result["upserted"].items():
        print(f"  {source}: {count}")
    print(f"  total upserted this run: {result['total_upserted']}")
    for note in result.get("notes") or []:
        print(f"  - {note}")
    if result["errors"]:
        print("Source errors:")
        for error in result["errors"]:
            print(f"  - {error}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
