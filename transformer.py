"""Strict sanitizers and source-to-staging transforms.

Every value is trimmed and length-limited before it is allowed near SQLite.
Dynamics 365 option-set integers used for Construction_Project_Phase:

- 948050000 Planning / identified
- 948050001 Tendering / bids open
- 948050002 Awarded or construction underway
- 948050003 Completed or closed
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime

from dateutil import parser as date_parser

PHASE_PLANNING = 948050000
PHASE_TENDERING = 948050001
PHASE_AWARDED = 948050002
PHASE_COMPLETED = 948050003
ALLOWED_PHASES = {PHASE_PLANNING, PHASE_TENDERING, PHASE_AWARDED, PHASE_COMPLETED}

PHASE_LABELS = {
    PHASE_PLANNING: "Planning",
    PHASE_TENDERING: "Tendering",
    PHASE_AWARDED: "Awarded / Construction",
    PHASE_COMPLETED: "Completed",
}

# Used only to decide whether a notice clears the public-works screen of
# about EUR 5.4 million. Stored amounts keep the source currency.
EUR_WORKS_THRESHOLD = 5_400_000
SEK_PER_EUR = 11.5

FIELD_LIMITS = {
    "Project_Name": 162,
    "External_Project_Code": 100,
    "Lead_Source_Name": 50,
    "Country": 50,
    "Currency": 10,
    "Notes": 2000,
    "Address_1_Postal_Code": 20,
    "Address_1_City": 40,
    "Address_1_State_Province": 100,
    "Address_1_Country": 50,
    "Buyer_Name": 200,
    "Cpv_Codes": 200,
    "Notice_Type": 80,
    "Notice_Url": 300,
}

SWEDISH_COUNTIES = {
    1: "Stockholm",
    3: "Uppsala",
    4: "Södermanland",
    5: "Östergötland",
    6: "Jönköping",
    7: "Kronoberg",
    8: "Kalmar",
    9: "Gotland",
    10: "Blekinge",
    12: "Skåne",
    13: "Halland",
    14: "Västra Götaland",
    17: "Värmland",
    18: "Örebro",
    19: "Västmanland",
    20: "Dalarna",
    21: "Gävleborg",
    22: "Västernorrland",
    23: "Jämtland",
    24: "Västerbotten",
    25: "Norrbotten",
}


def sanitize_text(text, max_length: int) -> str:
    """Trim text, turn missing values into an empty string, and hard-truncate."""
    if text is None:
        value = ""
    else:
        value = str(text)
    value = value.replace("\x00", " ")
    value = re.sub(r"\s+", " ", value).strip()
    limit = max(int(max_length), 0)
    return value[:limit]


def map_phase_to_optionset(raw_phase_string) -> int:
    """Map a source status string onto one of the four CRM option-set integers."""
    text = sanitize_text(raw_phase_string, 300).lower()
    if not text:
        return PHASE_PLANNING

    if _contains_any(
        text,
        (
            "completed",
            "complete",
            "closed",
            "ended",
            "finished",
            "cancelled",
            "canceled",
            "avslut",
            "upphör",
            "upphor",
        ),
    ):
        return PHASE_COMPLETED
    if _contains_any(
        text,
        (
            "award",
            "awarded",
            "tilldel",
            "contract",
            "can-",
            "can_",
            "construction",
            "vägarbete",
            "vagarbete",
            "roadwork",
            "maintenanceworks",
            "pågår",
            "pagar",
        ),
    ):
        return PHASE_AWARDED
    if _contains_any(
        text,
        (
            "tender",
            "bidding",
            "competition",
            "submission",
            "anbud",
            "cn-",
            "cn_",
            "open",
        ),
    ):
        return PHASE_TENDERING
    if _contains_any(text, ("plan", "prior", "pin-", "pin_", "forecast", "identified")):
        return PHASE_PLANNING
    return PHASE_PLANNING


def parse_date(date_str):
    """Convert a date-like value to a naive datetime, or None.

    The published wall-clock time is kept. A date-only TED value such as
    ``2026-06-26+02:00`` stays 26 June rather than shifting to the previous
    UTC evening.
    """
    if date_str is None:
        return None
    if isinstance(date_str, list):
        for item in date_str:
            parsed = parse_date(item)
            if parsed is not None:
                return parsed
        return None
    if isinstance(date_str, datetime):
        parsed = date_str
    else:
        text = str(date_str).strip()
        if not text or text.lower() in {"none", "null", "nat"}:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        # TED publishes calendar dates as 2026-06-26+02:00. fromisoformat can
        # read the offset as a clock time, so split the date and the offset.
        offset_match = re.fullmatch(r"(\d{4}-\d{2}-\d{2})([+-]\d{2}:\d{2})", text)
        if offset_match:
            text = f"{offset_match.group(1)}T00:00:00{offset_match.group(2)}"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            try:
                parsed = date_parser.parse(text)
            except (ValueError, TypeError, OverflowError):
                return None
    if parsed.tzinfo is not None:
        parsed = parsed.replace(tzinfo=None)
    return parsed


def parse_float(val):
    """Convert a numeric-like value to float, or None."""
    if val is None or isinstance(val, bool):
        return None
    if isinstance(val, list):
        numbers = [parse_float(item) for item in val]
        numbers = [number for number in numbers if number is not None]
        return max(numbers) if numbers else None
    if isinstance(val, (int, float)):
        return float(val)
    text = str(val).strip()
    if not text or text.lower() in {"none", "null", "nan"}:
        return None
    text = text.replace(" ", "").replace("\u00a0", "")
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        text = text.replace(",", ".")
    if text.count(".") > 1:
        text = text.replace(".", "")
    try:
        return float(text)
    except ValueError:
        return None


def generate_external_code(prefix, raw_id) -> str:
    """Build a source-prefixed external id that fits the 100-character column."""
    prefix_clean = re.sub(r"[^A-Za-z0-9]", "", sanitize_text(prefix, 20)).upper() or "SRC"
    raw = "" if raw_id is None else str(raw_id).strip()
    ident = re.sub(r"[^A-Za-z0-9_\-]", "", raw)
    if not ident:
        seed = raw or uuid.uuid4().hex
        ident = uuid.uuid5(uuid.NAMESPACE_URL, seed).hex[:16]
    code = f"{prefix_clean}-{ident}"
    return code[:100]


def transform_trafikverket(payload) -> list[dict]:
    """Turn a Trafikverket Situation response into staging lead dicts."""
    situations = []
    results = (payload or {}).get("RESPONSE", {}).get("RESULT", [])
    if isinstance(results, list):
        for result in results:
            if isinstance(result, dict):
                situations.extend(_as_list(result.get("Situation")))

    leads = []
    now = datetime.utcnow()
    for situation in situations:
        if not isinstance(situation, dict) or situation.get("Deleted") is True:
            continue
        for deviation in _as_list(situation.get("Deviation")):
            if not isinstance(deviation, dict):
                continue
            message_type = sanitize_text(deviation.get("MessageType"), 80).lower()
            message_code = sanitize_text(deviation.get("MessageCode"), 80).lower()
            if "vägarbete" not in message_type and "vagarbete" not in message_type:
                if "vägarbete" not in message_code and "roadwork" not in message_code:
                    continue
            start = parse_date(deviation.get("StartTime"))
            end = parse_date(deviation.get("EndTime"))
            if end is not None and end < now:
                phase_text = "completed"
            elif start is not None and start > now:
                phase_text = "planned"
            else:
                phase_text = deviation.get("MessageType") or "Vägarbete"

            header = sanitize_text(deviation.get("Header"), 120)
            road = sanitize_text(deviation.get("RoadNumber"), 40)
            location = sanitize_text(deviation.get("LocationDescriptor"), 500)
            project_name = header or location or "Roadworks"
            if road and road.lower() not in project_name.lower():
                project_name = f"{project_name} ({road})"

            latitude, longitude = _parse_wgs84(
                ((deviation.get("Geometry") or {}).get("Point") or {}).get("WGS84")
                or (deviation.get("Geometry") or {}).get("WGS84")
            )
            county_names = _county_names(deviation.get("CountyNo"))
            raw_id = deviation.get("Id") or situation.get("Id")
            message = sanitize_text(deviation.get("Message"), 800)
            notes = " | ".join(part for part in (location, message) if part)
            leads.append(
                _finalize_lead(
                    {
                        "Project_Name": project_name,
                        "External_Project_Code": generate_external_code("TRV", raw_id),
                        "Lead_Source_Name": "Trafikverket",
                        "Country": "Sweden",
                        "Construction_Project_Phase": map_phase_to_optionset(phase_text),
                        "Total_Value": None,
                        "Currency": "SEK",
                        "Notes": notes,
                        "Tender_Closing_Date": None,
                        "Tender_Award_Date": None,
                        "Construction_Start_Date": start,
                        "Construction_End_Date": end,
                        "Address_1_Postal_Code": None,
                        "Address_1_City": None,
                        "Address_1_State_Province": ", ".join(county_names),
                        "Address_1_Country": "Sweden",
                        "Address_1_Latitude": latitude,
                        "Address_1_Longitude": longitude,
                        "Buyer_Name": None,
                        "Cpv_Codes": None,
                        "Notice_Type": sanitize_text(deviation.get("MessageType"), 80),
                        "Published_On": parse_date(situation.get("PublicationTime")),
                        "Notice_Url": None,
                    }
                )
            )
    return _dedupe_codes(leads)


def transform_ted(payload) -> list[dict]:
    """Turn TED notices into staging leads above the EUR 5.4 million screen."""
    notices = (payload or {}).get("notices") if isinstance(payload, dict) else None
    if not isinstance(notices, list):
        return []

    leads = []
    for notice in notices:
        if not isinstance(notice, dict):
            continue
        value, currency = _ted_value(notice)
        if not _meets_works_threshold(value, currency):
            continue
        publication_number = sanitize_text(notice.get("publication-number"), 40)
        title = _ted_title(notice)
        buyer = _localized(notice.get("buyer-name"))
        cpv = ", ".join(
            sanitize_text(code, 12)
            for code in _as_list(notice.get("classification-cpv"))
            if sanitize_text(code, 12)
        )
        cpv_codes = []
        seen_cpv = set()
        for code in cpv.split(", "):
            if code and code not in seen_cpv:
                seen_cpv.add(code)
                cpv_codes.append(code)
        city = _localized(
            notice.get("place-of-performance-city-proc") or notice.get("buyer-city")
        )
        postal = _localized(
            notice.get("place-of-performance-post-code-proc")
            or notice.get("buyer-post-code")
        )
        region = _localized(
            notice.get("place-of-performance-subdiv-proc")
            or notice.get("buyer-country-sub")
        )
        notice_type = sanitize_text(notice.get("notice-type"), 80)
        notice_url = (
            f"https://ted.europa.eu/sv/notice/-/detail/{publication_number}"
            if publication_number
            else ""
        )
        leads.append(
            _finalize_lead(
                {
                    "Project_Name": title or f"TED notice {publication_number}",
                    "External_Project_Code": generate_external_code(
                        "TED", publication_number
                    ),
                    "Lead_Source_Name": "EU_TED",
                    "Country": "Sweden",
                    "Construction_Project_Phase": map_phase_to_optionset(notice_type),
                    "Total_Value": value,
                    "Currency": currency or "SEK",
                    "Notes": None,
                    "Buyer_Name": buyer,
                    "Cpv_Codes": ", ".join(cpv_codes),
                    "Notice_Type": notice_type,
                    "Published_On": parse_date(notice.get("publication-date")),
                    "Notice_Url": notice_url,
                    "Tender_Closing_Date": parse_date(
                        notice.get("deadline-receipt-tender-date-lot")
                    ),
                    "Tender_Award_Date": parse_date(notice.get("contract-conclusion-date")),
                    "Construction_Start_Date": None,
                    "Construction_End_Date": None,
                    "Address_1_Postal_Code": postal,
                    "Address_1_City": city,
                    "Address_1_State_Province": region,
                    "Address_1_Country": "Sweden",
                    "Address_1_Latitude": None,
                    "Address_1_Longitude": None,
                }
            )
        )
    return _dedupe_codes(leads)


def transform_opentender(payload) -> list[dict]:
    """Turn an OpenTender Sweden search payload into staging leads."""
    leads = []
    for record in _iter_opentender_records(payload):
        if not isinstance(record, dict) or not _is_construction_record(record):
            continue
        amount, currency = _opentender_amount(record)
        title = _localized(
            record.get("title")
            or record.get("titleEnglish")
            or record.get("name")
            or record.get("Project_Name")
        )
        raw_id = (
            record.get("id")
            or record.get("persistentId")
            or record.get("tenderId")
            or record.get("noticeId")
            or record.get("ocid")
        )
        buyer = _first_opentender_buyer(record)
        buyer_address = buyer.get("address") if isinstance(buyer.get("address"), dict) else {}
        address = record.get("address") if isinstance(record.get("address"), dict) else {}
        city = (
            record.get("city")
            or address.get("city")
            or buyer_address.get("city")
            or record.get("municipality")
        )
        postal = (
            record.get("postalCode")
            or record.get("postcode")
            or address.get("postcode")
            or address.get("postalCode")
            or buyer_address.get("postcode")
        )
        region = _first_text(
            record.get("region")
            or record.get("nuts")
            or address.get("nuts")
            or buyer_address.get("nuts")
            or buyer_address.get("nutsRobust2021")
        )
        buyer_name = sanitize_text(buyer.get("name") or record.get("buyerName"), 120)
        cpv_names = []
        for cpv in _as_list(record.get("cpvs"))[:4]:
            if isinstance(cpv, dict) and cpv.get("code"):
                cpv_names.append(sanitize_text(cpv.get("code"), 12))
            elif cpv:
                cpv_names.append(sanitize_text(cpv, 12))
        publications = record.get("publications") or []
        notice_url = ""
        if publications and isinstance(publications[0], dict):
            notice_url = (
                publications[0].get("humanReadableUrl")
                or publications[0].get("machineReadableUrl")
                or ""
            )
        notice_type = sanitize_text(
            record.get("procedureType")
            or (publications[0].get("formType") if publications and isinstance(publications[0], dict) else ""),
            80,
        )
        leads.append(
            _finalize_lead(
                {
                    "Project_Name": title or "Municipal construction notice",
                    "External_Project_Code": generate_external_code("OT", raw_id),
                    "Lead_Source_Name": "OpenTender",
                    "Country": "Sweden",
                    "Construction_Project_Phase": map_phase_to_optionset(
                        _opentender_status(record)
                    ),
                    "Total_Value": parse_float(amount),
                    "Currency": sanitize_text(currency, 10).upper() or "SEK",
                    "Notes": sanitize_text(record.get("description") or record.get("summary"), 2000),
                    "Buyer_Name": buyer_name,
                    "Cpv_Codes": ", ".join(cpv_names),
                    "Notice_Type": notice_type,
                    "Published_On": parse_date(record.get("tender_publication_date")),
                    "Notice_Url": notice_url,
                    "Tender_Closing_Date": parse_date(_opentender_lot_date(record, (
                        "bidDeadline",
                        "deadline",
                        "tenderDeadline",
                    ))),
                    "Tender_Award_Date": parse_date(
                        record.get("awardDecisionDate")
                        or record.get("awardDate")
                        or _opentender_lot_date(record, ("awardDecisionDate", "awardDate"))
                    ),
                    "Construction_Start_Date": parse_date(
                        record.get("startDate") or record.get("constructionStart")
                    ),
                    "Construction_End_Date": parse_date(
                        record.get("endDate") or record.get("constructionEnd")
                    ),
                    "Address_1_Postal_Code": postal,
                    "Address_1_City": city,
                    "Address_1_State_Province": region,
                    "Address_1_Country": "Sweden",
                    "Address_1_Latitude": parse_float(record.get("latitude") or address.get("lat")),
                    "Address_1_Longitude": parse_float(
                        record.get("longitude") or address.get("lon")
                    ),
                }
            )
        )
    return _dedupe_codes(leads)


def _finalize_lead(row: dict) -> dict:
    lead = {
        "Project_Name": sanitize_text(row.get("Project_Name"), FIELD_LIMITS["Project_Name"])
        or "Untitled project",
        "External_Project_Code": sanitize_text(
            row.get("External_Project_Code"), FIELD_LIMITS["External_Project_Code"]
        ),
        "Lead_Source_Name": sanitize_text(
            row.get("Lead_Source_Name"), FIELD_LIMITS["Lead_Source_Name"]
        ),
        "Country": sanitize_text(row.get("Country") or "Sweden", FIELD_LIMITS["Country"])
        or "Sweden",
        "Construction_Project_Phase": _phase_or_default(row.get("Construction_Project_Phase")),
        "Total_Value": parse_float(row.get("Total_Value")),
        "Currency": (
            sanitize_text(row.get("Currency") or "SEK", FIELD_LIMITS["Currency"]).upper()
            or "SEK"
        ),
        "Notes": sanitize_text(row.get("Notes"), FIELD_LIMITS["Notes"]) or None,
        "Tender_Closing_Date": parse_date(row.get("Tender_Closing_Date")),
        "Tender_Award_Date": parse_date(row.get("Tender_Award_Date")),
        "Construction_Start_Date": parse_date(row.get("Construction_Start_Date")),
        "Construction_End_Date": parse_date(row.get("Construction_End_Date")),
        "Address_1_Postal_Code": _empty_as_none(
            sanitize_text(row.get("Address_1_Postal_Code"), FIELD_LIMITS["Address_1_Postal_Code"])
        ),
        "Address_1_City": _empty_as_none(
            sanitize_text(row.get("Address_1_City"), FIELD_LIMITS["Address_1_City"])
        ),
        "Address_1_State_Province": _empty_as_none(
            sanitize_text(
                row.get("Address_1_State_Province"),
                FIELD_LIMITS["Address_1_State_Province"],
            )
        ),
        "Address_1_Country": sanitize_text(
            row.get("Address_1_Country") or "Sweden", FIELD_LIMITS["Address_1_Country"]
        )
        or "Sweden",
        "Address_1_Latitude": parse_float(row.get("Address_1_Latitude")),
        "Address_1_Longitude": parse_float(row.get("Address_1_Longitude")),
        "Buyer_Name": _empty_as_none(sanitize_text(row.get("Buyer_Name"), FIELD_LIMITS["Buyer_Name"])),
        "Cpv_Codes": _empty_as_none(sanitize_text(row.get("Cpv_Codes"), FIELD_LIMITS["Cpv_Codes"])),
        "Notice_Type": _empty_as_none(
            sanitize_text(row.get("Notice_Type"), FIELD_LIMITS["Notice_Type"])
        ),
        "Published_On": parse_date(row.get("Published_On")),
        "Notice_Url": _empty_as_none(sanitize_text(row.get("Notice_Url"), FIELD_LIMITS["Notice_Url"])),
    }
    if not lead["External_Project_Code"]:
        lead["External_Project_Code"] = generate_external_code(
            lead["Lead_Source_Name"], lead["Project_Name"]
        )
    return lead


def _dedupe_codes(rows: list[dict]) -> list[dict]:
    seen: dict[str, int] = {}
    for row in rows:
        code = row["External_Project_Code"]
        seen[code] = seen.get(code, 0) + 1
        if seen[code] == 1:
            continue
        suffix = f"-{seen[code]}"
        row["External_Project_Code"] = code[: 100 - len(suffix)] + suffix
    return rows


def _phase_or_default(value) -> int:
    try:
        phase = int(value)
    except (TypeError, ValueError):
        return PHASE_PLANNING
    return phase if phase in ALLOWED_PHASES else PHASE_PLANNING


def _meets_works_threshold(value, currency: str) -> bool:
    amount = parse_float(value)
    if amount is None:
        return False
    code = sanitize_text(currency, 10).upper() or "SEK"
    if code == "EUR":
        return amount >= EUR_WORKS_THRESHOLD
    if code == "SEK":
        return amount >= EUR_WORKS_THRESHOLD * SEK_PER_EUR
    return amount >= EUR_WORKS_THRESHOLD


def _ted_value(notice: dict) -> tuple[float | None, str]:
    currency = sanitize_text(
        _first(notice.get("estimated-value-cur-proc") or notice.get("estimated-value-cur-lot")),
        10,
    ).upper()
    value = parse_float(notice.get("estimated-value-proc"))
    if value is None:
        value = parse_float(notice.get("estimated-value-lot"))
    return value, currency or "SEK"


def _ted_title(notice: dict) -> str:
    text = sanitize_text(_localized(notice.get("notice-title")), 500)
    # TED separates country, CPV label, and the project name with en dashes.
    # ASCII hyphens stay intact so "åk 4 - 9" is not treated as a separator.
    parts = [part.strip() for part in re.split(r"\s+[–—]\s+", text) if part.strip()]
    if len(parts) >= 2:
        text = parts[-1]
    return sanitize_text(text, 162)


def _localized(value) -> str:
    if isinstance(value, dict):
        for lang in ("swe", "eng", "en"):
            picked = value.get(lang)
            if picked:
                return sanitize_text(_first(picked), 500)
        if value:
            return sanitize_text(_first(next(iter(value.values()))), 500)
        return ""
    if isinstance(value, list):
        names = [sanitize_text(_localized(item), 120) for item in value]
        names = [name for name in names if name]
        return ", ".join(names[:3])
    return sanitize_text(value, 500)


def _first(value):
    if isinstance(value, list):
        for item in value:
            if item not in (None, ""):
                return item
        return None
    return value


def _as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)


def _parse_wgs84(value) -> tuple[float | None, float | None]:
    if not value:
        return None, None
    match = re.search(r"(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)", str(value))
    if not match:
        return None, None
    longitude = float(match.group(1))
    latitude = float(match.group(2))
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return None, None
    return latitude, longitude


def _county_names(county_numbers) -> list[str]:
    names = []
    for number in _as_list(county_numbers):
        try:
            code = int(number)
        except (TypeError, ValueError):
            continue
        name = SWEDISH_COUNTIES.get(code)
        if name and name not in names:
            names.append(name)
    return names


def _iter_opentender_records(payload):
    if isinstance(payload, list):
        yield from payload
        return
    if not isinstance(payload, dict):
        return
    data = payload.get("data")
    if isinstance(data, dict):
        nested_hits = data.get("hits")
        if isinstance(nested_hits, dict) and isinstance(nested_hits.get("hits"), list):
            yield from nested_hits["hits"]
            return
    hits = payload.get("hits")
    if isinstance(hits, dict) and isinstance(hits.get("hits"), list):
        for hit in hits["hits"]:
            if isinstance(hit, dict) and isinstance(hit.get("_source"), dict):
                yield hit["_source"]
            else:
                yield hit
        return
    for key in ("data", "results", "tenders", "items", "notices", "search"):
        bucket = payload.get(key)
        if isinstance(bucket, list):
            yield from bucket
            return
        if isinstance(bucket, dict):
            for inner in ("data", "results", "hits", "tenders", "items"):
                inner_bucket = bucket.get(inner)
                if isinstance(inner_bucket, list):
                    yield from inner_bucket
                    return


def _is_construction_record(record: dict) -> bool:
    """Keep the notice unless it explicitly belongs to another sector.

    The OpenTender request already asks for Sector=Construction. Swedish titles
    often omit that word, so a missing sector field is kept.
    """
    sector = sanitize_text(
        record.get("sector") or record.get("Sector") or record.get("mainSector"),
        80,
    ).lower()
    if not sector:
        return True
    return any(token in sector for token in ("construction", "bygg", "anlägg", "anlagg"))


def _first_opentender_buyer(record: dict) -> dict:
    buyers = record.get("buyers")
    if isinstance(buyers, list):
        for buyer in buyers:
            if isinstance(buyer, dict):
                return buyer
    buyer = record.get("buyer")
    return buyer if isinstance(buyer, dict) else {}


def _opentender_status(record: dict) -> str:
    publications = record.get("publications") or []
    form = ""
    if publications and isinstance(publications[0], dict):
        form = str(publications[0].get("formType") or "")
    if "AWARD" in form.upper():
        return "awarded"
    procedure = str(record.get("procedureType") or record.get("status") or "")
    if procedure:
        return f"tender {procedure}"
    return form or "planned"


def _opentender_lot_date(record: dict, keys: tuple[str, ...]):
    for key in keys:
        if record.get(key):
            return record.get(key)
    for lot in _as_list(record.get("lots")):
        if not isinstance(lot, dict):
            continue
        for key in keys:
            if lot.get(key):
                return lot.get(key)
    return None


def _first_text(value) -> str:
    if isinstance(value, list):
        for item in value:
            text = _first_text(item)
            if text:
                return text
        return ""
    return sanitize_text(value, 100)


def _opentender_amount(record: dict) -> tuple[object, object]:
    for key in ("estimatedPrice", "finalPrice", "value", "totalValue", "amount"):
        raw = record.get(key)
        if isinstance(raw, dict):
            amount = raw.get("netAmount", raw.get("amount", raw.get("value")))
            currency = raw.get("currency") or record.get("currency")
            if amount is not None:
                return amount, currency
        elif raw is not None and not isinstance(raw, (list, dict)):
            return raw, record.get("currency")
    for lot in _as_list(record.get("lots")):
        if not isinstance(lot, dict):
            continue
        for key in ("estimatedPrice", "finalPrice"):
            raw = lot.get(key)
            if isinstance(raw, dict) and raw.get("netAmount") is not None:
                return raw.get("netAmount"), raw.get("currency") or record.get("currency")
    return None, record.get("currency")


def _empty_as_none(value: str):
    return value or None
