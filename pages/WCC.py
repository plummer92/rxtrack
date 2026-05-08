import pandas as pd
import plotly.express as px
import streamlit as st

import App


def to_csv_bytes(df):
    return df.to_csv(index=False).encode("utf-8")


st.set_page_config(page_title="WCC", page_icon="🍼", layout="wide")

start_date, end_date = App.render_sidebar()

if hasattr(App, "render_page_intro"):
    App.render_page_intro(
        "WCC",
        "Track WCC compounding administration volume, barcode compliance, and component-level trends.",
        kicker="Specialty Operations",
    )
else:
    st.header("WCC")
    st.caption("Track WCC compounding administration volume, barcode compliance, and component-level trends.")

with st.spinner("Loading WCC compounding stats..."):
    df_wcc = App.load_wcc_compounding_stats(start_date, end_date)

if df_wcc.empty:
    st.info("No WCC compounding stats found for this date range. Upload `WCC Compounding Stats` from the sidebar to get started.")
    st.stop()

wcc = df_wcc.copy()
wcc["administration_dt"] = pd.to_datetime(wcc["administration_dt"], errors="coerce")
wcc["admin_date"] = wcc["administration_dt"].dt.date
wcc["admin_hour"] = wcc["administration_dt"].dt.hour
wcc["barcode_status"] = wcc["barcode_status"].fillna("Unknown").astype(str).str.strip().replace("", "Unknown")
wcc["component_name"] = wcc["component_name"].fillna("").astype(str).str.strip()
wcc["order_name"] = wcc["order_name"].fillna("").astype(str).str.strip()
wcc["is_compliant"] = wcc["barcode_status"].str.lower().eq("compliant")

w1, w2, w3, w4 = st.columns(4)
w1.metric("Administrations", f"{len(wcc):,}")
w2.metric("Unique Components", f"{wcc['component_name'].nunique():,}")
w3.metric("Barcode Compliance", f"{wcc['is_compliant'].mean() * 100:.1f}%")
w4.metric("Non-Compliant", f"{int((~wcc['is_compliant']).sum()):,}")

wf1, wf2, wf3 = st.columns(3)
status_options = sorted(wcc["barcode_status"].dropna().unique().tolist())
selected_wcc_statuses = wf1.multiselect("Barcode Status", status_options, default=status_options)
component_search = wf2.text_input("Component/order search")
selected_hours = wf3.slider("Administration hour", 0, 23, (0, 23))

wcc_view = wcc.copy()
if selected_wcc_statuses:
    wcc_view = wcc_view[wcc_view["barcode_status"].isin(selected_wcc_statuses)]
if component_search:
    search_mask = (
        wcc_view["component_name"].str.contains(component_search, case=False, na=False)
        | wcc_view["order_name"].str.contains(component_search, case=False, na=False)
    )
    wcc_view = wcc_view[search_mask]
wcc_view = wcc_view[wcc_view["admin_hour"].between(selected_hours[0], selected_hours[1])]

wc1, wc2 = st.columns(2)
with wc1:
    daily_wcc = (
        wcc_view.groupby("admin_date", as_index=False)
        .agg(
            administrations=("pk", "count"),
            compliant=("is_compliant", "sum"),
        )
    )
    if not daily_wcc.empty:
        daily_wcc["compliance_pct"] = daily_wcc["compliant"] / daily_wcc["administrations"] * 100
    st.markdown("##### Daily Volume")
    if daily_wcc.empty:
        st.info("No rows match the current filters.")
    else:
        st.plotly_chart(px.bar(daily_wcc, x="admin_date", y="administrations"), width="stretch")

with wc2:
    status_mix = wcc_view.groupby("barcode_status", as_index=False).size().rename(columns={"size": "rows"})
    st.markdown("##### Barcode Status Mix")
    if status_mix.empty:
        st.info("No rows match the current filters.")
    else:
        st.plotly_chart(px.pie(status_mix, names="barcode_status", values="rows"), width="stretch")

top_components = (
    wcc_view.groupby(["component_name", "order_name"], as_index=False)
    .agg(
        administrations=("pk", "count"),
        compliant=("is_compliant", "sum"),
        non_compliant=("is_compliant", lambda s: int((~s).sum())),
        first_admin=("administration_dt", "min"),
        last_admin=("administration_dt", "max"),
    )
)
if not top_components.empty:
    top_components["compliance_pct"] = top_components["compliant"] / top_components["administrations"] * 100
else:
    top_components["compliance_pct"] = pd.Series(dtype=float)

st.markdown("##### Highest-Volume Components")
component_cols = [
    "component_name",
    "order_name",
    "administrations",
    "compliant",
    "non_compliant",
    "compliance_pct",
    "first_admin",
    "last_admin",
]
if top_components.empty:
    st.info("No components match the current filters.")
else:
    st.dataframe(
        top_components.sort_values(["administrations", "non_compliant"], ascending=[False, False]).head(50)[component_cols],
        width="stretch",
        hide_index=True,
        column_config={
            "administrations": st.column_config.NumberColumn("Administrations", format="%d"),
            "compliant": st.column_config.NumberColumn("Compliant", format="%d"),
            "non_compliant": st.column_config.NumberColumn("Non-Compliant", format="%d"),
            "compliance_pct": st.column_config.NumberColumn("Compliance %", format="%.1f"),
            "first_admin": st.column_config.DatetimeColumn("First Admin", format="MM/DD/YYYY HH:mm"),
            "last_admin": st.column_config.DatetimeColumn("Last Admin", format="MM/DD/YYYY HH:mm"),
        },
    )

with st.expander("Raw Administration Rows"):
    raw_cols = ["administration_dt", "component_name", "component_id", "order_name", "barcode_status", "source_file"]
    st.dataframe(
        wcc_view.sort_values("administration_dt", ascending=False)[raw_cols],
        width="stretch",
        hide_index=True,
        column_config={
            "administration_dt": st.column_config.DatetimeColumn("Administration", format="MM/DD/YYYY HH:mm"),
        },
    )
    st.download_button(
        "Download WCC compounding stats CSV",
        data=to_csv_bytes(wcc_view[raw_cols]),
        file_name="wcc_compounding_stats.csv",
        mime="text/csv",
    )
