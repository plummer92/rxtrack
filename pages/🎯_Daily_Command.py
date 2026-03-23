import streamlit as st
import pandas as pd
from datetime import date, datetime
from sqlalchemy import text
from App import engine

st.set_page_config(page_title="Daily Command", page_icon="🎯", layout="wide")

st.header("🎯 Daily Command")
st.caption(f"Today is **{date.today().strftime('%A, %B %d %Y')}**")

# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

PRIORITY_ORDER = {"High": 0, "Medium": 1, "Low": 2}
PRIORITY_COLOR = {"High": "🔴", "Medium": "🟡", "Low": "🟢"}

def _run(sql, params=None):
    with engine.begin() as conn:
        conn.execute(text(sql), params or {})

@st.cache_data(ttl=30)
def load_tasks():
    with engine.connect() as conn:
        return pd.read_sql(text("SELECT * FROM daily_ops ORDER BY due_date, id"), conn)

@st.cache_data(ttl=30)
def load_followups():
    with engine.connect() as conn:
        return pd.read_sql(text("SELECT * FROM follow_ups ORDER BY follow_up_date, id"), conn)

def refresh():
    load_tasks.clear()
    load_followups.clear()
    st.rerun()

# ─────────────────────────────────────────────────────────────────────────────
# LOAD DATA
# ─────────────────────────────────────────────────────────────────────────────

today = date.today()
tasks_all   = load_tasks()
followups_all = load_followups()

# Coerce date columns
if not tasks_all.empty:
    tasks_all["due_date"] = pd.to_datetime(tasks_all["due_date"], errors="coerce").dt.date
if not followups_all.empty:
    followups_all["follow_up_date"] = pd.to_datetime(followups_all["follow_up_date"], errors="coerce").dt.date

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 1 — TODAY'S TASKS
# ─────────────────────────────────────────────────────────────────────────────

st.subheader("🔴 Today's Tasks")

today_tasks = tasks_all[
    (tasks_all["due_date"] == today) &
    (tasks_all["status"] != "Done")
].copy() if not tasks_all.empty else pd.DataFrame()

overdue = tasks_all[
    tasks_all["due_date"].notna() &
    (tasks_all["due_date"] < today) &
    (tasks_all["status"] != "Done")
].copy() if not tasks_all.empty else pd.DataFrame()

if not overdue.empty:
    st.warning(f"⚠️ **{len(overdue)} overdue task(s)** carried from previous days.")

if today_tasks.empty and overdue.empty:
    st.info("Nothing due today. Add tasks below or check the full backlog.")
else:
    display_tasks = pd.concat([today_tasks, overdue], ignore_index=True).drop_duplicates("id")
    display_tasks["_pri_ord"] = display_tasks["priority"].map(PRIORITY_ORDER).fillna(1)
    display_tasks = display_tasks.sort_values("_pri_ord").drop(columns="_pri_ord")

    for _, row in display_tasks.iterrows():
        pri_icon = PRIORITY_COLOR.get(row["priority"], "⚪")
        overdue_flag = " 🚨 OVERDUE" if pd.notnull(row["due_date"]) and row["due_date"] < today else ""
        label = f"{pri_icon} **{row['task']}**{overdue_flag}"
        if row.get("category"):
            label += f"  `{row['category']}`"
        if row.get("notes"):
            label += f"  — _{row['notes']}_"

        col_lbl, col_prog, col_done, col_del = st.columns([5, 2, 1, 1])
        col_lbl.markdown(label)

        new_status = col_prog.selectbox(
            "Status",
            ["Not Started", "In Progress", "Done"],
            index=["Not Started", "In Progress", "Done"].index(row["status"])
                  if row["status"] in ["Not Started", "In Progress", "Done"] else 0,
            key=f"status_{row['id']}",
            label_visibility="collapsed",
        )
        if new_status != row["status"]:
            _run("UPDATE daily_ops SET status=:s WHERE id=:id", {"s": new_status, "id": int(row["id"])})
            refresh()

        if col_done.button("✅", key=f"done_{row['id']}", help="Mark done"):
            _run("UPDATE daily_ops SET status='Done' WHERE id=:id", {"id": int(row["id"])})
            refresh()

        if col_del.button("🗑️", key=f"del_{row['id']}", help="Delete task"):
            _run("DELETE FROM daily_ops WHERE id=:id", {"id": int(row["id"])})
            refresh()

st.divider()

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 2 — FOLLOW-UPS DUE
# ─────────────────────────────────────────────────────────────────────────────

st.subheader("🟡 Follow-Ups Due")

due_followups = followups_all[
    followups_all["follow_up_date"].notna() &
    (followups_all["follow_up_date"] <= today) &
    (followups_all["status"] == "Pending")
].copy() if not followups_all.empty else pd.DataFrame()

if due_followups.empty:
    st.info("No follow-ups due today.")
else:
    for _, row in due_followups.iterrows():
        overdue_fu = " 🚨" if row["follow_up_date"] < today else ""
        label = f"🟡 **{row['item']}**{overdue_fu}"
        if row.get("action_taken"):
            label += f"  — last action: _{row['action_taken']}_"
        if row.get("notes"):
            label += f"  · _{row['notes']}_"

        col_lbl, col_done, col_del = st.columns([6, 1, 1])
        col_lbl.markdown(label)

        if col_done.button("✅", key=f"fu_done_{row['id']}", help="Mark done"):
            _run("UPDATE follow_ups SET status='Done' WHERE id=:id", {"id": int(row["id"])})
            refresh()

        if col_del.button("🗑️", key=f"fu_del_{row['id']}", help="Delete"):
            _run("DELETE FROM follow_ups WHERE id=:id", {"id": int(row["id"])})
            refresh()

st.divider()

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 3 — QUICK ADD
# ─────────────────────────────────────────────────────────────────────────────

st.subheader("➕ Quick Add")

add_tab1, add_tab2 = st.tabs(["➕ New Task", "➕ New Follow-Up"])

with add_tab1:
    with st.form("add_task_form", clear_on_submit=True):
        a1, a2, a3 = st.columns([3, 1, 1])
        new_task     = a1.text_input("Task *", placeholder="What needs to get done?")
        new_priority = a2.selectbox("Priority", ["High", "Medium", "Low"], index=1)
        new_due      = a3.date_input("Due Date", value=today)

        b1, b2 = st.columns(2)
        new_cat   = b1.text_input("Category", placeholder="e.g. Staffing, Inventory, Compliance")
        new_notes = b2.text_input("Notes", placeholder="Optional context")

        if st.form_submit_button("Add Task", type="primary"):
            if new_task.strip():
                _run(
                    """INSERT INTO daily_ops (task, category, priority, status, due_date, notes, created_at)
                       VALUES (:task, :cat, :pri, 'Not Started', :due, :notes, NOW())""",
                    {"task": new_task.strip(), "cat": new_cat.strip() or None,
                     "pri": new_priority, "due": new_due,
                     "notes": new_notes.strip() or None}
                )
                st.toast("✅ Task added!", icon="📋")
                refresh()
            else:
                st.warning("Task description is required.")

with add_tab2:
    with st.form("add_followup_form", clear_on_submit=True):
        c1, c2 = st.columns([3, 1])
        new_item   = c1.text_input("Person / Issue *", placeholder="Who or what needs follow-up?")
        new_fu_date = c2.date_input("Follow-Up Date", value=today)

        d1, d2 = st.columns(2)
        new_action = d1.text_input("Action Taken", placeholder="What have you done so far?")
        new_fu_notes = d2.text_input("Notes", placeholder="Optional")

        if st.form_submit_button("Add Follow-Up", type="primary"):
            if new_item.strip():
                _run(
                    """INSERT INTO follow_ups (item, action_taken, follow_up_date, status, notes, created_at)
                       VALUES (:item, :action, :date, 'Pending', :notes, NOW())""",
                    {"item": new_item.strip(), "action": new_action.strip() or None,
                     "date": new_fu_date, "notes": new_fu_notes.strip() or None}
                )
                st.toast("✅ Follow-up added!", icon="🟡")
                refresh()
            else:
                st.warning("Item description is required.")

st.divider()

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 4 — FULL BACKLOG
# ─────────────────────────────────────────────────────────────────────────────

with st.expander("📋 Full Task Backlog", expanded=False):
    if tasks_all.empty:
        st.info("No tasks yet.")
    else:
        f1, f2, f3 = st.columns(3)
        filt_status   = f1.multiselect("Status",   ["Not Started","In Progress","Done"],
                                        default=["Not Started","In Progress"], key="bl_status")
        filt_priority = f2.multiselect("Priority", ["High","Medium","Low"],
                                        default=["High","Medium","Low"], key="bl_pri")
        show_all_dates = f3.checkbox("Include all dates", value=True, key="bl_dates")

        bl = tasks_all.copy()
        if filt_status:
            bl = bl[bl["status"].isin(filt_status)]
        if filt_priority:
            bl = bl[bl["priority"].isin(filt_priority)]
        if not show_all_dates:
            bl = bl[bl["due_date"] <= today]

        bl["_pri_ord"] = bl["priority"].map(PRIORITY_ORDER).fillna(1)
        bl = bl.sort_values(["due_date","_pri_ord"]).drop(columns="_pri_ord")

        st.dataframe(
            bl[["id","priority","status","task","category","due_date","notes"]],
            use_container_width=True,
            hide_index=True,
            column_config={
                "id":       st.column_config.NumberColumn("ID",       format="%d"),
                "priority": st.column_config.TextColumn("Priority"),
                "status":   st.column_config.TextColumn("Status"),
                "task":     st.column_config.TextColumn("Task"),
                "category": st.column_config.TextColumn("Category"),
                "due_date": st.column_config.DateColumn("Due",        format="MM/DD/YYYY"),
                "notes":    st.column_config.TextColumn("Notes"),
            }
        )

        # Inline push-to-tomorrow
        push_id = st.number_input("Push task ID to tomorrow", min_value=1, step=1, key="push_id")
        if st.button("📅 Push to tomorrow", key="push_btn"):
            from datetime import timedelta
            _run("UPDATE daily_ops SET due_date=:d WHERE id=:id",
                 {"d": today + timedelta(days=1), "id": int(push_id)})
            refresh()

with st.expander("📋 Full Follow-Up Log", expanded=False):
    if followups_all.empty:
        st.info("No follow-ups yet.")
    else:
        filt_fu = st.multiselect("Status", ["Pending","Done"], default=["Pending"], key="fu_filt")
        fu_view = followups_all[followups_all["status"].isin(filt_fu)] if filt_fu else followups_all
        fu_view = fu_view.sort_values("follow_up_date")

        st.dataframe(
            fu_view[["id","status","item","action_taken","follow_up_date","notes"]],
            use_container_width=True,
            hide_index=True,
            column_config={
                "id":             st.column_config.NumberColumn("ID",            format="%d"),
                "status":         st.column_config.TextColumn("Status"),
                "item":           st.column_config.TextColumn("Person / Issue"),
                "action_taken":   st.column_config.TextColumn("Action Taken"),
                "follow_up_date": st.column_config.DateColumn("Follow-Up Date", format="MM/DD/YYYY"),
                "notes":          st.column_config.TextColumn("Notes"),
            }
        )
