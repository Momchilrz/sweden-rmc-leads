"""Streamlit verification desk for the local RMC lead staging database.

Reading and refreshing this app does not connect to Dynamics 365 or Dataverse.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st
from database import engine, init_db
from main import run_etl_pipeline
from transformer import PHASE_LABELS

st.set_page_config(
    page_title="Sweden RMC Lead Staging",
    page_icon="🏗️",
    layout="wide",
)

PHASE_ORDER = [948050000, 948050001, 948050002, 948050003]


def main() -> None:
    init_db()
    st.title("Sweden RMC lead staging")
    st.caption(
        "Public Swedish construction projects, sanitized into the future CRM field "
        "layout and held in a local SQLite file for business verification. "
        "This desk does not authenticate to Dynamics 365."
    )

    if st.button("🔄 Fetch Latest Data", type="primary"):
        with st.spinner("Fetching Trafikverket, TED, and OpenTender…"):
            summary = run_etl_pipeline()
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
    filtered = _render_filters(prepared)
    _render_table(filtered)


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


def _render_filters(frame: pd.DataFrame) -> pd.DataFrame:
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
    query = right.text_input("Search name, city, or external code")

    filtered = frame[
        frame["Lead_Source_Name"].isin(selected_sources)
        & frame["Construction_Project_Phase"].isin(selected_phases)
    ]
    if query.strip():
        needle = query.strip().lower()
        haystack = (
            filtered["Project_Name"].fillna("")
            + " "
            + filtered["Address_1_City"].fillna("")
            + " "
            + filtered["External_Project_Code"].fillna("")
        ).str.lower()
        filtered = filtered[haystack.str.contains(needle, regex=False)]
    st.caption(f"{len(filtered):,} of {len(frame):,} leads match the filters.")
    return filtered


def _render_table(frame: pd.DataFrame) -> None:
    display = frame.copy()
    display.insert(
        3,
        "Phase",
        display["Construction_Project_Phase"].map(
            lambda phase: PHASE_LABELS.get(int(phase), "Unknown") if pd.notna(phase) else ""
        ),
    )
    columns = [
        "Project_Name",
        "Lead_Source_Name",
        "Phase",
        "Construction_Project_Phase",
        "Total_Value",
        "Currency",
        "Address_1_City",
        "Address_1_State_Province",
        "Address_1_Postal_Code",
        "Tender_Closing_Date",
        "Tender_Award_Date",
        "Construction_Start_Date",
        "Construction_End_Date",
        "External_Project_Code",
        "Notes",
    ]
    shown = display[columns]
    event = st.dataframe(
        shown,
        use_container_width=True,
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
        },
    )
    selected = event.selection.rows if event is not None else []
    if selected:
        row = shown.iloc[selected[0]]
        st.subheader(row["Project_Name"])
        st.write(row["Notes"] or "No notes on this lead.")
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
        "Created_On",
    ):
        if column in prepared.columns:
            prepared[column] = pd.to_datetime(prepared[column], errors="coerce")
    return prepared


if __name__ == "__main__":
    main()
