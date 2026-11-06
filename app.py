"""Streamlit verification desk for the local RMC lead staging database.

Reading and refreshing this app does not connect to Dynamics 365 or Dataverse.
"""

from __future__ import annotations

import io

import pandas as pd
import streamlit as st

from database import engine, init_db
from main import run_etl_pipeline
from schedule import next_slot, scheduled_run_is_due
from transformer import PHASE_LABELS

BRAND = "#004E2B"

st.set_page_config(
    page_title="MR Project",
    page_icon=":material/apartment:",
    layout="wide",
)

PHASE_ORDER = [948050000, 948050001, 948050002, 948050003]

COLUMN_ORDER = [
    "Project_Name",
    "Lead_Source_Name",
    "Buyer_Name",
    "Phase",
    "Construction_Project_Phase",
    "Notice_Type",
    "Cpv_Codes",
    "Total_Value",
    "Currency",
    "Published_On",
    "Tender_Closing_Date",
    "Tender_Award_Date",
    "Construction_Start_Date",
    "Construction_End_Date",
    "Address_1_City",
    "Address_1_State_Province",
    "Address_1_Postal_Code",
    "Address_1_Latitude",
    "Address_1_Longitude",
    "Notice_Url",
    "External_Project_Code",
    "Notes",
]
ALWAYS_VISIBLE = {
    "Project_Name",
    "Lead_Source_Name",
    "Phase",
    "Construction_Project_Phase",
    "Total_Value",
    "Currency",
    "Address_1_City",
    "Address_1_State_Province",
    "Address_1_Postal_Code",
    "Notice_Type",
    "Published_On",
    "External_Project_Code",
    "Notes",
}
SHARED_BY_SOURCE = {
    "Buyer_Name": {"EU_TED", "OpenTender"},
    "Cpv_Codes": {"EU_TED", "OpenTender"},
    "Tender_Closing_Date": {"EU_TED", "OpenTender"},
    "Tender_Award_Date": {"EU_TED", "OpenTender"},
    "Notice_Url": {"EU_TED", "OpenTender"},
}
EXCLUSIVE_TO_SOURCE = {
    "Construction_Start_Date": "Trafikverket",
    "Construction_End_Date": "Trafikverket",
    "Address_1_Latitude": "Trafikverket",
    "Address_1_Longitude": "Trafikverket",
}
FILTER_FIELDS = [
    ("Project_Name", "Project name", "text"),
    ("Buyer_Name", "Buyer", "text"),
    ("Address_1_City", "City", "text"),
    ("Address_1_State_Province", "Province", "text"),
    ("Cpv_Codes", "CPV", "text"),
    ("Notice_Type", "Notice type", "text"),
    ("Currency", "Currency", "text"),
    ("External_Project_Code", "External code", "text"),
    ("Notes", "Notes", "text"),
    ("Total_Value", "Total value", "number"),
    ("Published_On", "Published", "date"),
    ("Tender_Closing_Date", "Tender closing", "date"),
    ("Tender_Award_Date", "Award date", "date"),
    ("Construction_Start_Date", "Construction start", "date"),
    ("Construction_End_Date", "Construction end", "date"),
]
TEXT_OPERATORS = ["Contains", "Does not contain", "Equals", "Begins with", "Is empty"]
NUMBER_OPERATORS = ["Equals", "Greater than", "Less than", "Greater or equal", "Less or equal"]
DATE_OPERATORS = ["On", "Before", "After", "Is empty"]


def main() -> None:
    init_db()
    _inject_brand_css()
    _render_header()
    _run_scheduled_refresh()

    if st.button("Fetch latest data", type="primary"):
        summary = _refresh("Fetching Trafikverket, TED, and OpenTender…")
        st.session_state["last_run"] = summary
        load_leads.clear()

    last_run = st.session_state.get("last_run")
    if last_run:
        _render_run_summary(last_run)

    frame = load_leads()
    if frame.empty:
        st.info("The staging database is empty. Use Fetch Latest Data to load public notices.")
        return

    prepared = _prepare(frame)
    _render_kpis(prepared)
    filtered, selected_sources = _render_filters(prepared)
    _render_table(filtered, selected_sources)


def _inject_brand_css() -> None:
    st.markdown(
        f"""
        <style>
          html, body, [class*="css"] {{
            font-family: "Segoe UI", "Segoe UI Web", sans-serif;
          }}
          .stApp {{
            background: #FAF9F8;
          }}
          [data-testid="stHeader"], [data-testid="stToolbar"] {{
            display: none;
          }}
          .block-container {{
            padding-top: 0 !important;
            padding-left: 1.25rem;
            padding-right: 1.25rem;
            max-width: 100%;
          }}
          h1, h2, h3 {{
            color: #323130;
            font-weight: 600;
            letter-spacing: 0;
          }}
          .appbar {{
            background: {BRAND};
            color: #ffffff;
            height: 48px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin: 0 -1.25rem 8px;
            padding: 0 16px;
          }}
          .appbar-name {{
            margin: 0;
            font-size: 16px;
            font-weight: 600;
            color: #ffffff;
          }}
          .appbar-test {{
            margin: 0;
            border: 1px solid rgba(255,255,255,0.7);
            border-radius: 2px;
            padding: 2px 8px;
            font-size: 12px;
            letter-spacing: 0.04em;
            color: #ffffff;
          }}
          .workspace {{
            padding: 4px 0 0;
          }}
          .view-title {{
            margin: 8px 0 0;
            font-size: 20px;
            font-weight: 600;
            color: #323130;
          }}
          .view-sub {{
            margin: 2px 0 8px;
            color: #605E5C;
            font-size: 12px;
          }}
          div[data-testid="stMetric"] {{
            background: #ffffff;
            border: 1px solid #EDEBE9;
            border-top: 3px solid {BRAND};
            padding: 8px 12px 4px;
          }}
          div[data-testid="stMetricLabel"] p {{
            color: #605E5C;
            font-size: 12px;
          }}
          div[data-testid="stMetricValue"] {{
            color: #323130;
            font-size: 20px;
          }}
          .stButton > button {{
            background: {BRAND};
            color: #ffffff;
            border: 1px solid {BRAND};
            border-radius: 2px;
            height: 32px;
            font-weight: 600;
          }}
          .stButton > button:hover {{
            background: #00381F;
            color: #ffffff;
            border-color: #00381F;
          }}
          div[data-testid="stDataFrame"] {{
            background: #ffffff;
            border: 1px solid #EDEBE9;
          }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def _render_header() -> None:
    upcoming = next_slot().strftime("%d %b %Y, %H:%M")
    st.markdown(
        f"""
        <div class="appbar">
          <p class="appbar-name">MR Project</p>
          <p class="appbar-test">TEST</p>
        </div>
        <div class="workspace">
          <p class="view-title">Leads</p>
          <p class="view-sub">Real public notices. This is a test desk, not sample data. Next refresh {upcoming} Swedish time.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _run_scheduled_refresh() -> None:
    if st.session_state.get("schedule_checked"):
        return
    st.session_state["schedule_checked"] = True
    if not scheduled_run_is_due():
        return
    summary = _refresh("Daily refresh for 06:00 Swedish time…")
    st.session_state["last_run"] = summary
    load_leads.clear()


def _refresh(label: str) -> dict:
    try:
        with st.spinner(label):
            return run_etl_pipeline()
    except Exception as exc:
        return {
            "fetched": {},
            "upserted": {},
            "errors": [f"Refresh stopped: {exc.__class__.__name__}"],
            "total_upserted": 0,
        }


def _render_run_summary(summary: dict) -> None:
    counts = ", ".join(
        f"{source} {count}" for source, count in summary.get("upserted", {}).items()
    )
    st.success(f"Last refresh upserted {summary.get('total_upserted', 0)} leads ({counts}).")
    for error in summary.get("errors") or []:
        st.warning(error)


def _render_kpis(frame: pd.DataFrame) -> None:
    columns = st.columns(5)
    columns[0].metric("Leads", f"{len(frame):,}")
    columns[1].metric("Trafikverket", f"{(frame['Lead_Source_Name'] == 'Trafikverket').sum():,}")
    columns[2].metric("EU TED", f"{(frame['Lead_Source_Name'] == 'EU_TED').sum():,}")
    columns[3].metric("OpenTender", f"{(frame['Lead_Source_Name'] == 'OpenTender').sum():,}")
    tendering = (frame["Construction_Project_Phase"] == 948050001).sum()
    columns[4].metric("Open tenders", f"{int(tendering):,}")

    value_bits = []
    for currency, group in frame.dropna(subset=["Total_Value"]).groupby("Currency"):
        value_bits.append(f"{currency} {group['Total_Value'].sum():,.0f}")
    st.caption("Pipeline value by source currency: " + (" · ".join(value_bits) if value_bits else "no values published"))


def _render_filters(frame: pd.DataFrame):
    sources = sorted(frame["Lead_Source_Name"].dropna().unique().tolist())
    present_phases = [
        phase for phase in PHASE_ORDER if phase in set(frame["Construction_Project_Phase"].dropna())
    ]
    left, middle, right = st.columns([1.2, 1.4, 1.6])
    selected_sources = left.multiselect("Source", sources, default=sources)
    selected_phases = middle.multiselect(
        "Phase",
        present_phases,
        default=present_phases,
        format_func=lambda phase: f"{PHASE_LABELS.get(int(phase), 'Unknown')} ({int(phase)})",
    )
    query = right.text_input("Quick find")

    filtered = frame[
        frame["Lead_Source_Name"].isin(selected_sources)
        & frame["Construction_Project_Phase"].isin(selected_phases)
    ]
    filtered = _apply_view_filters(filtered, _render_condition_builder())
    if query.strip():
        needle = query.strip().lower()
        haystack = (
            filtered["Project_Name"].fillna("")
            + " "
            + filtered["Address_1_City"].fillna("")
            + " "
            + filtered["External_Project_Code"].fillna("")
            + " "
            + filtered.get("Buyer_Name", pd.Series("", index=filtered.index)).fillna("")
        ).str.lower()
        filtered = filtered[haystack.str.contains(needle, regex=False)]
    count_col, export_col = st.columns([4, 1])
    count_col.caption(f"{len(filtered):,} of {len(frame):,} leads match the filters.")
    export_col.download_button(
        "Export to Excel",
        data=_excel_bytes(filtered, selected_sources),
        file_name="mr-project-leads.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )
    return filtered, selected_sources


def _excel_bytes(frame: pd.DataFrame, selected_sources: list[str]) -> bytes:
    """Workbook of the rows and columns currently shown in the lead view."""
    display = frame.copy()
    if "Construction_Project_Phase" in display.columns:
        display.insert(
            3,
            "Phase",
            display["Construction_Project_Phase"].map(
                lambda phase: PHASE_LABELS.get(int(phase), "") if pd.notna(phase) else ""
            ),
        )
    columns = [column for column in _visible_columns(selected_sources) if column in display.columns]
    export = display[columns].copy()
    for column in export.columns:
        if pd.api.types.is_datetime64_any_dtype(export[column]):
            export[column] = export[column].dt.tz_localize(None) if export[column].dt.tz is not None else export[column]
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        export.to_excel(writer, index=False, sheet_name="Leads")
    return buffer.getvalue()


def _render_condition_builder() -> list[dict]:
    """Dynamics-style field, operator, and value conditions. All conditions apply together."""
    if "grid_filters" not in st.session_state:
        st.session_state["grid_filters"] = []

    with st.expander("Edit filters", expanded=bool(st.session_state["grid_filters"])):
        labels = [label for _field, label, _kind in FILTER_FIELDS]
        by_label = {label: (field, kind) for field, label, kind in FILTER_FIELDS}
        field_col, operator_col, value_col = st.columns([1.4, 1.2, 1.4])
        label = field_col.selectbox("Field", labels, key="filter_field")
        field, kind = by_label[label]
        operators = {"text": TEXT_OPERATORS, "number": NUMBER_OPERATORS, "date": DATE_OPERATORS}[kind]
        operator = operator_col.selectbox("Operator", operators, key="filter_operator")
        value = None
        if operator != "Is empty":
            if kind == "number":
                value = value_col.number_input("Value", key="filter_number", value=0.0, step=1000.0)
            elif kind == "date":
                value = value_col.date_input("Value", key="filter_date")
            else:
                value = value_col.text_input("Value", key="filter_text")
        if st.button("Add condition"):
            st.session_state["grid_filters"].append(
                {"field": field, "label": label, "kind": kind, "op": operator, "value": value}
            )
            st.rerun()

        remaining = []
        for index, condition in enumerate(st.session_state["grid_filters"]):
            shown_value = "" if condition["op"] == "Is empty" else f" {condition['value']}"
            line, remove = st.columns([6, 1])
            line.write(f"{condition['label']} {condition['op']}{shown_value}")
            if remove.button("Remove", key=f"remove_filter_{index}"):
                continue
            remaining.append(condition)
        if len(remaining) != len(st.session_state["grid_filters"]):
            st.session_state["grid_filters"] = remaining
            st.rerun()

    return list(st.session_state["grid_filters"])


def _apply_view_filters(frame: pd.DataFrame, conditions: list[dict]) -> pd.DataFrame:
    result = frame
    for condition in conditions:
        field = condition["field"]
        if field not in result.columns:
            continue
        series = result[field]
        operator = condition["op"]
        kind = condition["kind"]
        if operator == "Is empty":
            if kind == "text":
                mask = series.fillna("").astype(str).str.strip().eq("")
            else:
                mask = series.isna()
        elif kind == "text":
            text = series.fillna("").astype(str)
            needle = str(condition.get("value") or "")
            lowered = text.str.lower()
            query = needle.lower()
            if operator == "Contains":
                mask = lowered.str.contains(query, regex=False)
            elif operator == "Does not contain":
                mask = ~lowered.str.contains(query, regex=False)
            elif operator == "Equals":
                mask = lowered.eq(query)
            elif operator == "Begins with":
                mask = lowered.str.startswith(query)
            else:
                mask = pd.Series(True, index=result.index)
        elif kind == "number":
            numbers = pd.to_numeric(series, errors="coerce")
            amount = pd.to_numeric(condition.get("value"), errors="coerce")
            if pd.isna(amount):
                continue
            comparisons = {
                "Equals": numbers.eq(amount),
                "Greater than": numbers.gt(amount),
                "Less than": numbers.lt(amount),
                "Greater or equal": numbers.ge(amount),
                "Less or equal": numbers.le(amount),
            }
            mask = comparisons.get(operator, pd.Series(True, index=result.index))
        else:
            dates = pd.to_datetime(series, errors="coerce")
            day = pd.to_datetime(condition.get("value"), errors="coerce")
            if pd.isna(day):
                continue
            if operator == "On":
                mask = dates.dt.normalize().eq(day.normalize())
            elif operator == "Before":
                mask = dates.lt(day)
            elif operator == "After":
                mask = dates.gt(day)
            else:
                mask = pd.Series(True, index=result.index)
        result = result[mask.fillna(False)]
    return result


def _visible_columns(selected_sources: list[str]) -> list[str]:
    chosen = set(selected_sources)
    visible = set(ALWAYS_VISIBLE)
    for column, owners in SHARED_BY_SOURCE.items():
        if chosen & owners:
            visible.add(column)
    if len(chosen) == 1:
        only = next(iter(chosen))
        for column, owner in EXCLUSIVE_TO_SOURCE.items():
            if owner == only:
                visible.add(column)
    return [column for column in COLUMN_ORDER if column in visible]


def _render_table(frame: pd.DataFrame, selected_sources: list[str]) -> None:
    display = frame.copy()
    display.insert(
        3,
        "Phase",
        display["Construction_Project_Phase"].map(
            lambda phase: PHASE_LABELS.get(int(phase), "Unknown") if pd.notna(phase) else ""
        ),
    )
    columns = [column for column in _visible_columns(selected_sources) if column in display.columns]
    shown = display[columns]
    event = st.dataframe(
        shown,
        width="stretch",
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        column_config={
            "Construction_Project_Phase": st.column_config.NumberColumn("Phase code", format="%d"),
            "Total_Value": st.column_config.NumberColumn("Total value", format="%,.0f"),
            "Tender_Closing_Date": st.column_config.DatetimeColumn("Tender closing"),
            "Tender_Award_Date": st.column_config.DatetimeColumn("Award date"),
            "Construction_Start_Date": st.column_config.DatetimeColumn("Construction start"),
            "Construction_End_Date": st.column_config.DatetimeColumn("Construction end"),
            "Published_On": st.column_config.DatetimeColumn("Published"),
            "Notice_Url": st.column_config.LinkColumn("Notice"),
            "Buyer_Name": st.column_config.TextColumn("Buyer"),
            "Cpv_Codes": st.column_config.TextColumn("CPV"),
            "Notice_Type": st.column_config.TextColumn("Notice type"),
        },
    )
    selected = event.selection.rows if event is not None else []
    if selected:
        row = display.iloc[selected[0]]
        st.subheader(row["Project_Name"])
        if row.get("Buyer_Name"):
            st.write(row["Buyer_Name"])
        if row.get("Cpv_Codes"):
            st.caption(f"CPV {row['Cpv_Codes']}")
        st.write(row["Notes"] or "No description on this lead.")
        if row.get("Notice_Url"):
            st.markdown(f"[Open the notice]({row['Notice_Url']})")
        st.caption(row["External_Project_Code"])


@st.cache_data(ttl=60, show_spinner=False)
def load_leads() -> pd.DataFrame:
    init_db()
    return pd.read_sql_query("SELECT * FROM leads ORDER BY Project_Name", engine)


def _prepare(frame: pd.DataFrame) -> pd.DataFrame:
    prepared = frame.copy()
    for column in (
        "Tender_Closing_Date",
        "Tender_Award_Date",
        "Construction_Start_Date",
        "Construction_End_Date",
        "Published_On",
        "Created_On",
    ):
        if column in prepared.columns:
            prepared[column] = pd.to_datetime(prepared[column], errors="coerce")
    return prepared


if __name__ == "__main__":
    main()
