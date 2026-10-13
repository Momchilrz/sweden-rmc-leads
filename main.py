"""ETL orchestration for the local Sweden RMC lead staging database."""

from __future__ import annotations

import logging

from database import upsert_leads
from fetchers import FetchError, fetch_opentender, fetch_ted, fetch_trafikverket
from transformer import transform_opentender, transform_ted, transform_trafikverket

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("rmc_etl")

SOURCES = (
    ("Trafikverket", fetch_trafikverket, transform_trafikverket),
    ("EU_TED", fetch_ted, transform_ted),
    ("OpenTender", fetch_opentender, transform_opentender),
)


def run_etl_pipeline() -> dict:
    """Fetch, sanitize, and upsert every source. One failed source does not stop the others."""
    summary = {
        "fetched": {},
        "upserted": {},
        "errors": [],
        "total_upserted": 0,
    }
    for source_name, fetcher, transformer in SOURCES:
        try:
            logger.info("Fetching %s", source_name)
            payload = fetcher()
            leads = transformer(payload)
            written = upsert_leads(leads)
            summary["fetched"][source_name] = len(leads)
            summary["upserted"][source_name] = written
            summary["total_upserted"] += written
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
    return summary


if __name__ == "__main__":
    result = run_etl_pipeline()
    print("Staging complete")
    for source, count in result["upserted"].items():
        print(f"  {source}: {count}")
    print(f"  total upserted this run: {result['total_upserted']}")
    if result["errors"]:
        print("Source errors:")
        for error in result["errors"]:
            print(f"  - {error}")
