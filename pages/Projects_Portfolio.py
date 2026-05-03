from datetime import date

import pandas as pd
import plotly.express as px
import streamlit as st
from sqlalchemy import text

import App


st.set_page_config(page_title="Projects Portfolio", page_icon="📁", layout="wide")

if hasattr(App, "render_sidebar_chrome"):
    App.render_sidebar_chrome()
else:
    App.render_sidebar()

if hasattr(App, "render_page_intro"):
    App.render_page_intro(
        "Projects Portfolio",
        "A running record of pharmacy operations projects, process changes, and the impact they created.",
        kicker="Professional Portfolio",
    )
else:
    st.header("Projects Portfolio")
    st.caption("A running record of pharmacy operations projects, process changes, and impact.")


engine = App.engine
load_data = App.load_data

UNLOAD_PROJECT_START = date(2025, 12, 15)
UNLOAD_PROJECT_END = date(2026, 1, 5)
UNLOAD_PROJECT_USERS = ["Isaac Vizral", "Jaycie Cole", "Lauren Voudrie"]


PROJECTS = [
    {
        "name": "Unload Window Optimization",
        "status": "Completed",
        "timeframe": "Dec 15, 2025 to Jan 5, 2026",
        "area": "Inventory Rotation / Carousel Stocking",
        "problem": (
            "The unload process was waiting 45 days before medications rotated back through the pharmacy workflow. "
            "That allowed usable inventory to sit in Pyxis longer while the carousel could appear short."
        ),
        "action": (
            "Changed the unload window from 45 days to 28 days so inventory rotates sooner, keeping more usable "
            "stock in the pharmacy carousel and improving visibility before new orders are placed."
        ),
        "impact": (
            "Less unnecessary ordering pressure because medication that would have been sitting in Pyxis is brought "
            "back into carousel availability sooner. The pharmacy can use on-hand inventory before buying more."
        ),
        "proof_points": [
            "Compare carousel on-hand quantity before and after the 28-day change.",
            "Track medications reordered while usable stock existed in Pyxis.",
            "Monitor unload volume and carousel restock volume by week.",
            "Estimate avoided orders or avoided dollars once cost data is available.",
        ],
    },
    {
        "name": "RxTrack Operations Dashboard",
        "status": "In production",
        "timeframe": "Ongoing",
        "area": "Operations Intelligence",
        "problem": "Daily Pyxis, carousel, pharmacy workflow, schedule, and attendance data lived in separate reports.",
        "action": "Built a Streamlit dashboard that combines those sources into operational views and drilldowns.",
        "impact": "Faster visibility into workload, discrepancies, returns, pends, staffing patterns, and inventory quality.",
        "proof_points": [
            "Number of workflows now visible in one app.",
            "Time saved from manual report review.",
            "Examples of issues found through dashboard drilldowns.",
        ],
    },
    {
        "name": "Carousel Drop Tracker",
        "status": "In production",
        "timeframe": "Ongoing",
        "area": "Carousel / Pyxis Loop Closure",
        "problem": "Carousel pull demand and Pyxis refill completion were hard to compare by drop window.",
        "action": "Built drop-level reconciliation around pull quantity, loaded quantity, coverage, and device drilldowns.",
        "impact": "Makes missed refills, partial fills, and refill timing visible by drop and by device.",
        "proof_points": [
            "Coverage percentage by drop.",
            "Devices with pull demand but no refill activity.",
            "Repeated problem devices or windows.",
        ],
    },
    {
        "name": "Return Reconciliation",
        "status": "In production",
        "timeframe": "Ongoing",
        "area": "Medication Returns",
        "problem": "Returned medication movement was difficult to follow from Pyxis activity back to carousel handling.",
        "action": "Built views to compare return/removal activity and supporting transaction detail.",
        "impact": "Improves accountability and makes return-process gaps easier to review.",
        "proof_points": [
            "Matched vs unmatched return activity.",
            "High-volume medications or devices in the return path.",
            "Follow-up actions from reconciliation review.",
        ],
    },
]


def project_dataframe(projects):
    return pd.DataFrame(
        [
            {
                "Project": p["name"],
                "Status": p["status"],
                "Area": p["area"],
                "Timeframe": p["timeframe"],
            }
            for p in projects
        ]
    )


def classify_return_workflow(row):
    text_blob = " ".join([str(row.get("event_type", "")), str(row.get("priority", ""))]).lower()
    if "inventory" in text_blob:
        return "Inventory Move"
    if "instant" in text_blob and "return" in text_blob:
        return "Instant Return"
    if "instant" in text_blob and "restock" in text_blob:
        return "Instant Restock"
    if "restock" in text_blob:
        return "Restock"
    if "return" in text_blob:
        return "Return"
    return "Other"


def remove_dummy_meds(df):
    if df.empty or "med_desc" not in df.columns:
        return df
    return df[~df["med_desc"].astype(str).str.contains("cassette", case=False, na=False)]


def matches_project_user(user_name, project_users):
    raw = str(user_name or "").strip().lower()
    if not raw:
        return False
    compact = raw.replace(",", " ")
    tokens = {part for part in compact.split() if part}
    normalized = App.normalize_name(raw)

    for project_user in project_users:
        first, *rest = project_user.lower().split()
        last = rest[-1] if rest else ""
        full = f"{first} {last}".strip()
        comma_name = f"{last}, {first}".strip(", ")
        if raw in {full, comma_name} or full in compact:
            return True
        if first in tokens and (not last or last in tokens):
            return True
        if normalized == first:
            return True
    return False


@st.cache_data(ttl=300)
def load_med_costs():
    try:
        with engine.connect() as conn:
            costs = pd.read_sql(
                text("SELECT med_id, cost_per_unit FROM med_costs"),
                conn,
            )
        costs["med_id"] = costs["med_id"].astype(str).str.strip()
        costs["cost_per_unit"] = pd.to_numeric(costs["cost_per_unit"], errors="coerce").fillna(0)
        return costs
    except Exception:
        return pd.DataFrame(columns=["med_id", "cost_per_unit"])


def build_unload_window_impact(
    start_date,
    end_date,
    exclude_dummy=True,
    savings_capture_pct=100,
    project_users=None,
):
    df_events, _, df_pharm, _, _ = load_data(start_date, end_date)
    for df in [df_events, df_pharm]:
        if not df.empty and "dt" in df.columns:
            df["dt"] = pd.to_datetime(df["dt"], errors="coerce")
            df["date"] = df["dt"].dt.date

    pyxis_unload = pd.DataFrame()
    if not df_events.empty and "event_type" in df_events.columns:
        pyxis_all = df_events[
            df_events["event_type"].astype(str).str.contains("empty|unload|return bin|destock", case=False, na=False)
            & ~df_events["event_type"].astype(str).str.contains("cancel", case=False, na=False)
        ].copy()
        pyxis_unload = pyxis_all[
            ~pyxis_all["event_type"].astype(str).str.contains("eject", case=False, na=False)
        ].copy()
        if "device" in pyxis_unload.columns:
            pyxis_unload = pyxis_unload[
                ~pyxis_unload["device"].astype(str).str.contains("cass|patient", case=False, na=False)
            ]

    pharm_return = pd.DataFrame()
    if not df_pharm.empty:
        pharm_df = df_pharm.copy()
        event_col = "event_type" if "event_type" in pharm_df.columns else "priority"
        pharm_all = pharm_df[
            pharm_df[event_col].astype(str).str.contains("return|restock|instant|inventory", case=False, na=False)
        ].copy()
        if not pharm_all.empty:
            pharm_all["workflow_type"] = pharm_all.apply(classify_return_workflow, axis=1)
            pharm_return = pharm_all[
                pharm_all["workflow_type"].isin({"Return", "Instant Return", "Instant Restock"})
            ].copy()

    if exclude_dummy:
        pyxis_unload = remove_dummy_meds(pyxis_unload)
        pharm_return = remove_dummy_meds(pharm_return)

    if project_users:
        if not pyxis_unload.empty and "user_name" in pyxis_unload.columns:
            pyxis_unload = pyxis_unload[
                pyxis_unload["user_name"].apply(lambda value: matches_project_user(value, project_users))
            ]
        if not pharm_return.empty and "user_name" in pharm_return.columns:
            pharm_return = pharm_return[
                pharm_return["user_name"].apply(lambda value: matches_project_user(value, project_users))
            ]

    costs = load_med_costs()
    project_source = pyxis_unload.copy()
    if not project_source.empty:
        project_source["med_id"] = project_source["med_id"].astype(str).str.strip()
        project_source["qty"] = pd.to_numeric(project_source["qty"], errors="coerce").fillna(0)
        returned = (
            project_source.groupby(["med_id", "med_desc"], dropna=False)
            .agg(
                returned_qty=("qty", "sum"),
                unload_rows=("qty", "size"),
                users=("user_name", lambda values: ", ".join(sorted({str(v) for v in values if pd.notna(v)}))),
                devices=("device", lambda values: ", ".join(sorted({str(v) for v in values if pd.notna(v)}))),
            )
            .reset_index()
        )
    else:
        returned = pd.DataFrame(columns=["med_id", "med_desc", "returned_qty", "unload_rows", "users", "devices"])

    if not pharm_return.empty:
        pharm_return["med_id"] = pharm_return["med_id"].astype(str).str.strip()
        pharm_return["qty"] = pd.to_numeric(pharm_return["qty"], errors="coerce").fillna(0)
        carousel_returned = (
            pharm_return.groupby(["med_id", "med_desc"], dropna=False)["qty"]
            .sum()
            .reset_index(name="carousel_return_qty")
        )
    else:
        carousel_returned = pd.DataFrame(columns=["med_id", "med_desc", "carousel_return_qty"])

    returned = returned.merge(costs, on="med_id", how="left")
    returned = returned.merge(
        carousel_returned.drop(columns=["med_desc"], errors="ignore"),
        on="med_id",
        how="left",
    )
    returned["cost_per_unit"] = returned["cost_per_unit"].fillna(0)
    returned["carousel_return_qty"] = returned["carousel_return_qty"].fillna(0)
    returned["returned_value"] = returned["returned_qty"] * returned["cost_per_unit"]
    returned = returned.sort_values("returned_value", ascending=False)

    days = max((pd.to_datetime(end_date).date() - pd.to_datetime(start_date).date()).days + 1, 1)
    capture_rate = savings_capture_pct / 100
    returned_units = float(returned["returned_qty"].sum()) if not returned.empty else 0
    carousel_units = float(returned["carousel_return_qty"].sum()) if not returned.empty else 0
    returned_value = float(returned["returned_value"].sum()) if not returned.empty else 0
    annualized_units = returned_units / days * 365
    annualized_value = returned_value / days * 365 * capture_rate
    pyxis_units = float(pyxis_unload["qty"].sum()) if not pyxis_unload.empty and "qty" in pyxis_unload.columns else 0

    returned["projected_12mo_value"] = returned["returned_value"] / days * 365 * capture_rate
    return {
        "detail": returned,
        "days": days,
        "returned_units": returned_units,
        "carousel_units": carousel_units,
        "returned_value": returned_value,
        "annualized_units": annualized_units,
        "annualized_value": annualized_value,
        "pyxis_units": pyxis_units,
    }


def render_project_card(project):
    with st.container(border=True):
        top = st.columns([2.2, 1, 1])
        top[0].subheader(project["name"])
        top[1].metric("Status", project["status"])
        top[2].metric("Area", project["area"])

        st.caption(project["timeframe"])
        st.markdown("**Problem**")
        st.write(project["problem"])
        st.markdown("**What I Changed**")
        st.write(project["action"])
        st.markdown("**Operational Impact**")
        st.write(project["impact"])

        with st.expander("Proof points to attach", expanded=False):
            for item in project["proof_points"]:
                st.markdown(f"- {item}")


summary = project_dataframe(PROJECTS)

c1, c2, c3, c4 = st.columns(4)
c1.metric("Projects Tracked", len(PROJECTS))
c2.metric("Completed", sum(1 for p in PROJECTS if p["status"] == "Completed"))
c3.metric("In Production", sum(1 for p in PROJECTS if p["status"] == "In production"))
c4.metric("Last Updated", date.today().strftime("%b %d, %Y"))

st.divider()

st.subheader("Portfolio Summary")
st.dataframe(summary, use_container_width=True, hide_index=True)

st.divider()

st.subheader("Featured Project")
render_project_card(PROJECTS[0])

with st.expander("Unload Window Impact Calculator", expanded=True):
    st.caption(
        "Project mode values the Pyxis unload transactions completed by Isaac Vizral, Jaycie Cole, and Lauren Voudrie from Dec 15, 2025 through Jan 5, 2026."
    )

    use_project_scope = st.checkbox(
        "Use unload-window project scope",
        value=True,
        help="Dec 15, 2025 to Jan 5, 2026; Isaac Vizral, Jaycie Cole, and Lauren Voudrie.",
    )

    f1, f2, f3 = st.columns([1, 1, 1])
    default_start = UNLOAD_PROJECT_START if use_project_scope else date.today() - pd.Timedelta(days=89)
    default_end = UNLOAD_PROJECT_END if use_project_scope else date.today()
    impact_start = f1.date_input("Impact start date", value=default_start, disabled=use_project_scope)
    impact_end = f2.date_input("Impact end date", value=default_end, disabled=use_project_scope)
    capture_pct = f3.slider(
        "Savings capture assumption",
        min_value=0,
        max_value=100,
        value=100,
        step=5,
        help="Use 100% when every returned unit is assumed to offset a future purchase. Lower it if only some returned stock prevents ordering.",
    )
    exclude_dummy = st.checkbox("Exclude dummy/cassette medications", value=True)
    project_users = UNLOAD_PROJECT_USERS if use_project_scope else None
    if use_project_scope:
        st.caption("Included project users: " + ", ".join(UNLOAD_PROJECT_USERS))

    if impact_start > impact_end:
        st.warning("Start date must be before end date.")
    else:
        impact = build_unload_window_impact(impact_start, impact_end, exclude_dummy, capture_pct, project_users)
        detail = impact["detail"]

        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("Project Unload Units", f"{impact['returned_units']:,.0f}")
        m2.metric("Carousel Return Units", f"{impact['carousel_units']:,.0f}")
        m3.metric("Returned Inventory Value", f"${impact['returned_value']:,.2f}")
        m4.metric("Projected 12-Mo Units", f"{impact['annualized_units']:,.0f}")
        m5.metric("Projected 12-Mo Value", f"${impact['annualized_value']:,.2f}")

        st.caption(
            f"Projection is annualized from {impact['days']} selected day(s) at a {capture_pct}% capture assumption. "
            "Treat this as avoided purchasing opportunity from rotating 28-to-45-day Pyxis inventory back into usable stock, not booked savings, until purchasing data confirms the offset."
        )

        if detail.empty:
            st.info("No qualifying project unload rows found for this date range and user scope.")
        else:
            table = detail.rename(
                columns={
                    "med_id": "Med ID",
                    "med_desc": "Medication",
                    "returned_qty": "Project Unload Qty",
                    "carousel_return_qty": "Carousel Return Qty",
                    "unload_rows": "Unload Rows",
                    "users": "Users",
                    "devices": "Devices",
                    "cost_per_unit": "Unit Cost",
                    "returned_value": "Returned Value",
                    "projected_12mo_value": "Projected 12-Mo Value",
                }
            )
            st.dataframe(
                table,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Project Unload Qty": st.column_config.NumberColumn(format="%.0f"),
                    "Carousel Return Qty": st.column_config.NumberColumn(format="%.0f"),
                    "Unload Rows": st.column_config.NumberColumn(format="%.0f"),
                    "Unit Cost": st.column_config.NumberColumn(format="$%.2f"),
                    "Returned Value": st.column_config.NumberColumn(format="$%.2f"),
                    "Projected 12-Mo Value": st.column_config.NumberColumn(format="$%.2f"),
                },
            )

            top = detail.head(15).copy()
            top["label"] = top["med_desc"].fillna(top["med_id"]).astype(str).str.slice(0, 42)
            fig = px.bar(
                top.sort_values("returned_value"),
                x="returned_value",
                y="label",
                orientation="h",
                text="returned_qty",
                labels={"returned_value": "Returned inventory value", "label": ""},
                color="returned_value",
                color_continuous_scale="Teal",
            )
            fig.update_layout(height=420, coloraxis_showscale=False, margin=dict(l=10, r=20, t=20, b=10))
            fig.update_traces(texttemplate="%{text:.0f} units", textposition="outside")
            st.plotly_chart(fig, use_container_width=True)

st.divider()

st.subheader("Other Projects So Far")
for project in PROJECTS[1:]:
    render_project_card(project)

st.divider()

with st.expander("Impact statement for resumes, interviews, or leadership updates", expanded=True):
    st.markdown(
        """
Changed the pharmacy unload process from a 45-day window to a 28-day window to rotate inventory sooner,
keep more usable stock available in the carousel, and reduce unnecessary drug ordering when medication
was already available in Pyxis inventory. Built RxTrack dashboards to make these operational changes
visible, measurable, and easier to sustain.
        """.strip()
    )
