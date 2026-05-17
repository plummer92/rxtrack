import streamlit as st
import pandas as pd
import urllib.parse
from datetime import date, datetime, timedelta
from sqlalchemy import text
import App

_debug_event = getattr(App, "record_ui_debug_event", lambda *args, **kwargs: None)
_debug_panel = getattr(App, "render_ui_debugger", lambda *args, **kwargs: None)

st.set_page_config(page_title="Daily Command", page_icon="🎯", layout="wide")
App.apply_global_styles()
if hasattr(App, "render_sidebar_chrome"):
    App.render_sidebar_chrome()
else:
    App.render_sidebar()

App.require_management_access("Daily Command")

engine = App.engine

if hasattr(App, "render_page_intro"):
    App.render_page_intro(
        "Daily Command",
        f"Today is {date.today().strftime('%A, %B %d %Y')}. Run the day from one shared command center instead of the legacy page shell.",
        kicker="Operations",
    )
    _debug_event("Daily Command", "shared_intro_loaded")
    _debug_panel("Daily Command", intro_mode="shared")
else:
    st.header("🎯 Daily Command")
    st.caption(f"Today is {date.today().strftime('%A, %B %d %Y')}.")
    _debug_event("Daily Command", "fallback_header_used")
    _debug_panel("Daily Command", intro_mode="fallback")

# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

PRIORITY_ORDER = {"High": 0, "Medium": 1, "Low": 2}
PRIORITY_COLOR = {"High": "🔴", "Medium": "🟡", "Low": "🟢"}
DOW_NAMES      = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

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

@st.cache_data(ttl=60)
def load_recurring():
    with engine.connect() as conn:
        return pd.read_sql(text("SELECT * FROM recurring_tasks ORDER BY id"), conn)

def refresh():
    load_tasks.clear()
    load_followups.clear()
    load_recurring.clear()
    st.rerun()

def outlook_link(task_name: str, task_date: date, notes: str = "", duration_min: int = 30) -> str:
    """Return an Outlook Web deep-link URL to pre-fill a calendar event."""
    start = datetime.combine(task_date, datetime.min.time()).replace(hour=9, minute=0, second=0)
    end   = start + timedelta(minutes=duration_min)
    params = {
        "subject":  task_name,
        "startdt":  start.strftime("%Y-%m-%dT%H:%M:%S"),
        "enddt":    end.strftime("%Y-%m-%dT%H:%M:%S"),
        "body":     notes or "",
    }
    return "https://outlook.office.com/calendar/deeplink/compose?" + urllib.parse.urlencode(params)

def _should_fire_today(recurrence: str, days_of_week: str) -> bool:
    """Return True if a recurring task should fire today."""
    dow = date.today().weekday()          # 0=Mon … 6=Sun
    today_name = DOW_NAMES[dow]
    if recurrence == "Daily":
        return True
    if recurrence == "Weekdays":
        return dow < 5
    if recurrence in ("Weekly", "Custom") and days_of_week:
        selected = [d.strip() for d in days_of_week.split(",")]
        return today_name in selected
    return False

# ─────────────────────────────────────────────────────────────────────────────
# AUTO-SEED RECURRING TASKS
# ─────────────────────────────────────────────────────────────────────────────

def seed_recurring_today():
    """Insert today's daily_ops rows from active recurring_tasks (once per day)."""
    try:
        with engine.begin() as conn:
            rows = conn.execute(text(
                "SELECT id, task, category, priority, recurrence, days_of_week, notes "
                "FROM recurring_tasks WHERE active = TRUE"
            )).fetchall()

            for r in rows:
                if not _should_fire_today(r.recurrence, r.days_of_week):
                    continue
                exists = conn.execute(text(
                    "SELECT 1 FROM daily_ops WHERE recurring_task_id = :rid AND due_date = :d"
                ), {"rid": r.id, "d": str(date.today())}).fetchone()
                if not exists:
                    conn.execute(text("""
                        INSERT INTO daily_ops
                            (task, category, priority, status, due_date, notes, created_at, recurring_task_id)
                        VALUES (:task, :cat, :pri, 'Not Started', :due, :notes, NOW(), :rid)
                    """), {
                        "task": r.task, "cat": r.category, "pri": r.priority,
                        "due": str(date.today()), "notes": r.notes, "rid": r.id,
                    })
    except Exception as e:
        st.warning(f"Recurring task seeding error: {e}")

seed_recurring_today()

# ─────────────────────────────────────────────────────────────────────────────
# LOAD DATA
# ─────────────────────────────────────────────────────────────────────────────

today         = date.today()
tasks_all     = load_tasks()
followups_all = load_followups()
recurring_all = load_recurring()

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
        pri_icon     = PRIORITY_COLOR.get(row["priority"], "⚪")
        is_recurring = pd.notna(row.get("recurring_task_id")) and row.get("recurring_task_id") is not None
        overdue_flag = " 🚨 OVERDUE" if pd.notnull(row["due_date"]) and row["due_date"] < today else ""
        recur_badge  = " 🔁" if is_recurring else ""

        label = f"{pri_icon} **{row['task']}**{recur_badge}{overdue_flag}"
        if row.get("category"):
            label += f"  `{row['category']}`"
        if row.get("notes"):
            label += f"  — _{row['notes']}_"

        col_lbl, col_prog, col_cal, col_done, col_del = st.columns([5, 2, 1, 1, 1])
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

        # 📅 Outlook deep-link
        cal_url = outlook_link(
            row["task"],
            row["due_date"] if pd.notnull(row["due_date"]) else today,
            notes=str(row.get("notes") or ""),
        )
        col_cal.link_button("📅", cal_url, help="Add to Outlook Calendar")

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

        col_lbl, col_cal, col_done, col_del = st.columns([6, 1, 1, 1])
        col_lbl.markdown(label)

        fu_cal_url = outlook_link(
            f"Follow-Up: {row['item']}",
            row["follow_up_date"] if pd.notnull(row["follow_up_date"]) else today,
            notes=str(row.get("action_taken") or ""),
        )
        col_cal.link_button("📅", fu_cal_url, help="Add to Outlook Calendar")

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
        new_item    = c1.text_input("Person / Issue *", placeholder="Who or what needs follow-up?")
        new_fu_date = c2.date_input("Follow-Up Date", value=today)

        d1, d2 = st.columns(2)
        new_action   = d1.text_input("Action Taken", placeholder="What have you done so far?")
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
# SECTION 4 — RECURRING TASKS MANAGER
# ─────────────────────────────────────────────────────────────────────────────

with st.expander("🔁 Recurring Tasks", expanded=False):
    st.caption(
        "Recurring tasks auto-appear in Today's Tasks every morning. "
        "🔁 badge marks auto-generated rows."
    )

    if not recurring_all.empty:
        for _, r in recurring_all.iterrows():
            is_active = bool(r["active"])
            status_icon = "🟢" if is_active else "⚫"
            days_label  = f" ({r['days_of_week']})" if r.get("days_of_week") else ""
            pri_icon    = PRIORITY_COLOR.get(r["priority"], "⚪")

            rc1, rc2, rc3, rc4 = st.columns([5, 2, 1, 1])
            rc1.markdown(
                f"{status_icon} {pri_icon} **{r['task']}** "
                f"— `{r['recurrence']}`{days_label}"
                + (f"  `{r['category']}`" if r.get("category") else "")
            )
            rc2.caption(r.get("notes") or "")

            toggle_label = "Pause" if is_active else "Resume"
            if rc3.button(toggle_label, key=f"rtog_{r['id']}"):
                _run("UPDATE recurring_tasks SET active = NOT active WHERE id=:id", {"id": int(r["id"])})
                refresh()

            if rc4.button("🗑️", key=f"rdel_{r['id']}", help="Delete recurring template"):
                _run("DELETE FROM recurring_tasks WHERE id=:id", {"id": int(r["id"])})
                refresh()
    else:
        st.info("No recurring tasks yet. Add one below.")

    st.divider()
    st.markdown("##### Add Recurring Task")

    with st.form("add_recurring_form", clear_on_submit=True):
        r1, r2, r3 = st.columns([3, 1, 1])
        rt_task     = r1.text_input("Task *", placeholder="e.g. Check overnight discrepancies")
        rt_priority = r2.selectbox("Priority", ["High", "Medium", "Low"], index=1)
        rt_recur    = r3.selectbox("Repeats", ["Daily", "Weekdays", "Weekly", "Custom"])

        r4, r5 = st.columns(2)
        rt_cat   = r4.text_input("Category", placeholder="e.g. Staffing, Compliance")
        rt_notes = r5.text_input("Notes", placeholder="Optional")

        rt_days = None
        if rt_recur in ("Weekly", "Custom"):
            rt_days = st.multiselect(
                "Which days?",
                DOW_NAMES,
                default=["Mon"] if rt_recur == "Weekly" else [],
                key="rt_days_sel",
            )

        if st.form_submit_button("Save Recurring Task", type="primary"):
            if rt_task.strip():
                days_str = ",".join(rt_days) if rt_days else None
                _run(
                    """INSERT INTO recurring_tasks
                           (task, category, priority, recurrence, days_of_week, active, notes, created_at)
                       VALUES (:task, :cat, :pri, :rec, :days, TRUE, :notes, NOW())""",
                    {"task": rt_task.strip(), "cat": rt_cat.strip() or None,
                     "pri": rt_priority, "rec": rt_recur,
                     "days": days_str, "notes": rt_notes.strip() or None}
                )
                st.toast("🔁 Recurring task saved!", icon="✅")
                refresh()
            else:
                st.warning("Task description is required.")

st.divider()

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 5 — FULL BACKLOG
# ─────────────────────────────────────────────────────────────────────────────

with st.expander("📋 Full Task Backlog", expanded=False):
    if tasks_all.empty:
        st.info("No tasks yet.")
    else:
        f1, f2, f3 = st.columns(3)
        filt_status    = f1.multiselect("Status",   ["Not Started","In Progress","Done"],
                                         default=["Not Started","In Progress"], key="bl_status")
        filt_priority  = f2.multiselect("Priority", ["High","Medium","Low"],
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
        bl = bl.sort_values(["due_date", "_pri_ord"]).drop(columns="_pri_ord")

        st.dataframe(
            bl[["id", "priority", "status", "task", "category", "due_date", "notes"]],
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

        push_id = st.number_input("Push task ID to tomorrow", min_value=1, step=1, key="push_id")
        if st.button("📅 Push to tomorrow", key="push_btn"):
            _run("UPDATE daily_ops SET due_date=:d WHERE id=:id",
                 {"d": today + timedelta(days=1), "id": int(push_id)})
            refresh()

with st.expander("📋 Full Follow-Up Log", expanded=False):
    if followups_all.empty:
        st.info("No follow-ups yet.")
    else:
        filt_fu = st.multiselect("Status", ["Pending", "Done"], default=["Pending"], key="fu_filt")
        fu_view = followups_all[followups_all["status"].isin(filt_fu)] if filt_fu else followups_all
        fu_view = fu_view.sort_values("follow_up_date")

        st.dataframe(
            fu_view[["id", "status", "item", "action_taken", "follow_up_date", "notes"]],
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

