from datetime import date

import pandas as pd
import streamlit as st

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


PROJECTS = [
    {
        "name": "Unload Window Optimization",
        "status": "Completed",
        "timeframe": "Recent process change",
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
