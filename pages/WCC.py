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

with st.spinner("Loading WCC data..."):
    df_wcc = App.load_wcc_compounding_stats(start_date, end_date)
    load_cartfill = getattr(App, "load_wcc_cartfill_stats", None)
    df_cartfill = load_cartfill(start_date, end_date) if callable(load_cartfill) else pd.DataFrame()

if df_wcc.empty and df_cartfill.empty:
    st.info("No WCC data found for this date range. Upload `WCC Compounding Stats` or `WCC Cartfill Stats` from the sidebar to get started.")
    st.stop()

if not df_cartfill.empty:
    cartfill = df_cartfill.copy()
    cartfill["ready_for_dispense_dt"] = pd.to_datetime(cartfill["ready_for_dispense_dt"], errors="coerce")
    cartfill["prepared_dt"] = pd.to_datetime(cartfill["prepared_dt"], errors="coerce")
    cartfill["ready_date"] = cartfill["ready_for_dispense_dt"].dt.date
    cartfill["ready_hour"] = cartfill["ready_for_dispense_dt"].dt.hour
    cartfill["ready_minute"] = cartfill["ready_for_dispense_dt"].dt.hour * 60 + cartfill["ready_for_dispense_dt"].dt.minute
    cartfill["order_medication"] = cartfill["order_medication"].fillna("").astype(str).str.strip()
    cartfill["prep_or_dispense_user"] = cartfill["prep_or_dispense_user"].fillna("Unassigned").astype(str).str.strip().replace("", "Unassigned")
    cartfill["location"] = cartfill["location"].fillna("").astype(str).str.strip()
    cartfill["pharmacy"] = cartfill["pharmacy"].fillna("").astype(str).str.strip()
    if "cartfill_area" not in cartfill.columns:
        cartfill["cartfill_area"] = "Needs Review"
    cartfill["cartfill_area"] = cartfill["cartfill_area"].fillna("Needs Review").astype(str).str.strip().replace("", "Needs Review")
    cartfill["is_1230_window"] = cartfill["ready_minute"].between(12 * 60, 13 * 60)
    cartfill["is_prepared"] = cartfill["prepared_dt"].notna()

    st.subheader("WCC 12:30 Cartfill")
    cf_view = cartfill.copy()
    cf0, cf1, cf2, cf3 = st.columns(4)
    area_options = sorted(cf_view["cartfill_area"].dropna().unique().tolist())
    default_areas = [area for area in ["WCC"] if area in area_options] or area_options
    selected_areas = cf0.multiselect("Cartfill area", area_options, default=default_areas)
    pharmacy_options = sorted(cf_view["pharmacy"].dropna().unique().tolist())
    selected_pharmacies = cf1.multiselect("Cartfill pharmacy", pharmacy_options, default=pharmacy_options)
    cartfill_search = cf2.text_input("Cartfill med/search")
    only_1230 = cf3.toggle("Only 12:30 fill window", value=True)
    if selected_areas:
        cf_view = cf_view[cf_view["cartfill_area"].isin(selected_areas)]
    if selected_pharmacies:
        cf_view = cf_view[cf_view["pharmacy"].isin(selected_pharmacies)]
    if only_1230:
        cf_view = cf_view[cf_view["is_1230_window"]]
    if cartfill_search:
        cf_mask = (
            cf_view["order_medication"].str.contains(cartfill_search, case=False, na=False)
            | cf_view["location"].str.contains(cartfill_search, case=False, na=False)
            | cf_view["pharmacy"].str.contains(cartfill_search, case=False, na=False)
        )
        cf_view = cf_view[cf_mask]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Cartfill Items", f"{len(cf_view):,}")
    c2.metric("Prepared", f"{int(cf_view['is_prepared'].sum()):,}" if not cf_view.empty else "0")
    c3.metric("Unprepared", f"{int((~cf_view['is_prepared']).sum()):,}" if not cf_view.empty else "0")
    c4.metric("Unique Meds", f"{cf_view['order_medication'].nunique():,}" if not cf_view.empty else "0")

    cart_chart_1, cart_chart_2 = st.columns(2)
    with cart_chart_1:
        ready_by_day = cf_view.groupby("ready_date", as_index=False).size().rename(columns={"size": "items"})
        st.markdown("##### Cartfill Items by Day")
        if ready_by_day.empty:
            st.info("No cartfill rows match the current filters.")
        else:
            st.plotly_chart(px.bar(ready_by_day, x="ready_date", y="items"), width="stretch")
    with cart_chart_2:
        by_pharmacy = cf_view.groupby("pharmacy", as_index=False).size().rename(columns={"size": "items"})
        st.markdown("##### Cartfill Items by Pharmacy")
        if by_pharmacy.empty:
            st.info("No cartfill rows match the current filters.")
        else:
            st.plotly_chart(px.bar(by_pharmacy.sort_values("items"), x="items", y="pharmacy", orientation="h"), width="stretch")

    med_summary = (
        cf_view.groupby(["order_medication", "cartfill_area", "pharmacy"], as_index=False)
        .agg(
            items=("pk", "count"),
            prepared=("is_prepared", "sum"),
            first_ready=("ready_for_dispense_dt", "min"),
            last_ready=("ready_for_dispense_dt", "max"),
        )
    )
    if not med_summary.empty:
        med_summary["unprepared"] = med_summary["items"] - med_summary["prepared"]
    st.markdown("##### 12:30 Cartfill Item Summary")
    summary_cols = ["order_medication", "cartfill_area", "pharmacy", "items", "prepared", "unprepared", "first_ready", "last_ready"]
    if med_summary.empty:
        st.info("No cartfill items match the current filters.")
    else:
        st.dataframe(
            med_summary.sort_values(["items", "unprepared"], ascending=[False, False])[summary_cols],
            width="stretch",
            hide_index=True,
            column_config={
                "items": st.column_config.NumberColumn("Items", format="%d"),
                "prepared": st.column_config.NumberColumn("Prepared", format="%d"),
                "unprepared": st.column_config.NumberColumn("Unprepared", format="%d"),
                "first_ready": st.column_config.DatetimeColumn("First Ready", format="MM/DD/YYYY HH:mm"),
                "last_ready": st.column_config.DatetimeColumn("Last Ready", format="MM/DD/YYYY HH:mm"),
            },
        )

    with st.expander("Raw 12:30 Cartfill Rows"):
        cart_cols = [
            "ready_for_dispense_dt", "admin_given_dt", "prepared_dt", "prep_or_dispense_user",
            "order_medication", "med_id", "cartfill_area", "location", "pharmacy", "source_file",
        ]
        cart_cols = [col for col in cart_cols if col in cf_view.columns]
        st.dataframe(
            cf_view.sort_values("ready_for_dispense_dt", ascending=False)[cart_cols],
            width="stretch",
            hide_index=True,
            column_config={
                "ready_for_dispense_dt": st.column_config.DatetimeColumn("Ready", format="MM/DD/YYYY HH:mm"),
                "admin_given_dt": st.column_config.DatetimeColumn("Admin Given", format="MM/DD/YYYY HH:mm"),
                "prepared_dt": st.column_config.DatetimeColumn("Prepared", format="MM/DD/YYYY HH:mm"),
            },
        )
        st.download_button(
            "Download WCC cartfill CSV",
            data=to_csv_bytes(cf_view[cart_cols]),
            file_name="wcc_1230_cartfill.csv",
            mime="text/csv",
        )

if df_wcc.empty:
    st.info("No WCC compounding administration stats found for this date range.")
    st.stop()

wcc = df_wcc.copy()
wcc["administration_dt"] = pd.to_datetime(wcc["administration_dt"], errors="coerce")
wcc["admin_date"] = wcc["administration_dt"].dt.date
wcc["admin_hour"] = wcc["administration_dt"].dt.hour
wcc["barcode_status"] = wcc["barcode_status"].fillna("Unknown").astype(str).str.strip().replace("", "Unknown")
wcc["component_name"] = wcc["component_name"].fillna("").astype(str).str.strip()
wcc["order_name"] = wcc["order_name"].fillna("").astype(str).str.strip()
wcc["is_compliant"] = wcc["barcode_status"].str.lower().eq("compliant")

st.subheader("WCC Compounding Administration Stats")
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
