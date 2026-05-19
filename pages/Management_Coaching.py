import json
from datetime import date

import pandas as pd
import streamlit as st
from sqlalchemy import text

import App


st.set_page_config(page_title="Management Coaching", page_icon="📝", layout="wide")
App.apply_global_styles()

if hasattr(App, "render_sidebar_chrome"):
    App.render_sidebar_chrome()
else:
    App.render_sidebar()

App.require_management_access("Management Coaching")

engine = App.engine
App.init_db()

if hasattr(App, "render_page_intro"):
    App.render_page_intro(
        "Management Coaching",
        "Private coaching notes, follow-ups, and accountability planning for management review.",
        kicker="Management",
    )
else:
    st.header("Management Coaching")
    st.caption("Private coaching notes, follow-ups, and accountability planning.")


@st.cache_data(ttl=30)
def load_coaching_notes():
    with engine.connect() as conn:
        return pd.read_sql(
            text(
                """
                SELECT id, staff_name, topic, coaching_date, follow_up_date,
                       status, summary, next_steps, source_page, source_key, source_payload_json, created_at, updated_at
                FROM management_coaching_notes
                ORDER BY COALESCE(follow_up_date, coaching_date) DESC NULLS LAST, id DESC
                """
            ),
            conn,
        )


@st.cache_data(ttl=300)
def load_staff_options():
    try:
        with engine.connect() as conn:
            df = pd.read_sql(
                text(
                    """
                    SELECT DISTINCT staff_name
                    FROM staff_schedule
                    WHERE staff_name IS NOT NULL AND TRIM(staff_name) <> ''
                    ORDER BY staff_name
                    """
                ),
                conn,
            )
        return df["staff_name"].dropna().astype(str).tolist()
    except Exception:
        return []


def refresh():
    load_coaching_notes.clear()
    load_staff_options.clear()
    st.rerun()


def save_note(payload):
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO management_coaching_notes
                    (staff_name, topic, coaching_date, follow_up_date, status, summary, next_steps)
                VALUES
                    (:staff_name, :topic, :coaching_date, :follow_up_date, :status, :summary, :next_steps)
                """
            ),
            payload,
        )


def update_status(note_id, status):
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE management_coaching_notes
                SET status = :status, updated_at = NOW()
                WHERE id = :id
                """
            ),
            {"id": int(note_id), "status": status},
        )


notes = load_coaching_notes()
staff_options = load_staff_options()

open_notes = notes[notes["status"].fillna("Open").isin(["Open", "Follow Up"])] if not notes.empty else notes
past_due = pd.to_datetime(open_notes.get("follow_up_date"), errors="coerce") if not open_notes.empty else pd.Series(dtype="datetime64[ns]")
past_due_count = int((past_due.dt.date < date.today()).sum()) if not past_due.empty else 0

m1, m2, m3 = st.columns(3)
m1.metric("Open Items", f"{len(open_notes):,}" if not open_notes.empty else "0")
m2.metric("Past Due Follow-ups", f"{past_due_count:,}")
m3.metric("Total Notes", f"{len(notes):,}" if not notes.empty else "0")

st.divider()

entry_col, list_col = st.columns([0.9, 1.1])

with entry_col:
    st.subheader("New Coaching Note")
    with st.form("new_coaching_note", clear_on_submit=True):
        staff_name = st.selectbox(
            "Staff member",
            options=[""] + staff_options,
            index=0,
        )
        manual_name = st.text_input("Or enter name")
        topic = st.selectbox(
            "Topic",
            ["Attendance", "Workflow", "Accuracy", "Discrepancy", "Professionalism", "Follow-up", "Other"],
        )
        coaching_date = st.date_input("Coaching date", value=date.today())
        needs_follow_up = st.checkbox("Add follow-up date", value=True)
        follow_up_date = st.date_input("Follow-up date", value=date.today()) if needs_follow_up else None
        status = st.selectbox("Status", ["Open", "Follow Up", "Closed"], index=0)
        summary = st.text_area("Summary", height=130)
        next_steps = st.text_area("Next steps", height=100)
        submitted = st.form_submit_button("Save Note")

    if submitted:
        final_name = (manual_name or staff_name or "").strip()
        if not final_name:
            st.warning("Add a staff member before saving.")
        elif not summary.strip():
            st.warning("Add a summary before saving.")
        else:
            save_note(
                {
                    "staff_name": final_name,
                    "topic": topic,
                    "coaching_date": coaching_date,
                    "follow_up_date": follow_up_date,
                    "status": status,
                    "summary": summary.strip(),
                    "next_steps": next_steps.strip(),
                }
            )
            st.success("Coaching note saved.")
            refresh()

with list_col:
    st.subheader("Coaching Queue")
    if notes.empty:
        st.info("No coaching notes have been saved yet.")
    else:
        status_filter = st.multiselect(
            "Status",
            ["Open", "Follow Up", "Closed"],
            default=["Open", "Follow Up"],
        )
        view = notes.copy()
        if status_filter:
            view = view[view["status"].fillna("Open").isin(status_filter)].copy()

        search = st.text_input("Search notes")
        if search:
            needle = search.strip().lower()
            haystack = (
                view["staff_name"].fillna("")
                + " "
                + view["topic"].fillna("")
                + " "
                + view["summary"].fillna("")
                + " "
                + view["next_steps"].fillna("")
            ).str.lower()
            view = view[haystack.str.contains(needle, na=False)].copy()

        st.dataframe(
            view[["staff_name", "topic", "coaching_date", "follow_up_date", "status", "summary", "next_steps", "source_page"]],
            width="stretch",
            hide_index=True,
        )

        st.divider()
        note_ids = view["id"].tolist()
        if note_ids:
            selected_id = st.selectbox(
                "Update item",
                note_ids,
                format_func=lambda note_id: f"#{note_id} - {view.loc[view['id'].eq(note_id), 'staff_name'].iloc[0]}",
            )
            selected_note = view[view["id"].eq(selected_id)].iloc[0]
            st.markdown(f"**{selected_note['topic']}**")
            st.write(selected_note["summary"])
            if pd.notna(selected_note.get("next_steps")) and str(selected_note.get("next_steps")).strip():
                st.caption(f"Next steps: {selected_note['next_steps']}")
            payload_text = selected_note.get("source_payload_json")
            if pd.notna(payload_text) and str(payload_text).strip():
                try:
                    evidence = pd.DataFrame(json.loads(payload_text))
                except Exception:
                    evidence = pd.DataFrame()
                if not evidence.empty:
                    st.markdown("**Strong Pattern Evidence**")
                    chain_cols = [
                        "Verify Time", "Pyxis", "Med ID", "Medication", "Prior Refill Time",
                        "Prior Event", "Refill Entered", "Pull Qty", "Refill vs Pull",
                        "Later Verify Off", "Verify User", "Inventory Events Since",
                    ]
                    visible_cols = [col for col in chain_cols if col in evidence.columns]
                    st.dataframe(evidence[visible_cols], width="stretch", hide_index=True)

                    chart_cols = ["Refill Entered", "Pull Qty", "Later Verify Off"]
                    if all(col in evidence.columns for col in chart_cols):
                        chart_df = evidence.copy()
                        if "Verify Time" not in chart_df.columns:
                            chart_df["Verify Time"] = ""
                        if "Med ID" not in chart_df.columns:
                            chart_df["Med ID"] = ""
                        chart_df["Occurrence"] = (
                            chart_df["Verify Time"].astype(str)
                            + " | "
                            + chart_df["Med ID"].astype(str)
                        )
                        chart_df = chart_df[["Occurrence"] + chart_cols].set_index("Occurrence")
                        chart_df = chart_df.apply(pd.to_numeric, errors="coerce").fillna(0)
                        st.bar_chart(chart_df)

            c1, c2, c3 = st.columns(3)
            if c1.button("Mark Open", key=f"open_{selected_id}"):
                update_status(selected_id, "Open")
                refresh()
            if c2.button("Mark Follow Up", key=f"follow_{selected_id}"):
                update_status(selected_id, "Follow Up")
                refresh()
            if c3.button("Close", key=f"close_{selected_id}"):
                update_status(selected_id, "Closed")
                refresh()
