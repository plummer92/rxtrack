import pandas as pd
import plotly.express as px
import streamlit as st

import App

_debug_event = getattr(App, "record_ui_debug_event", lambda *args, **kwargs: None)
_debug_panel = getattr(App, "render_ui_debugger", lambda *args, **kwargs: None)


st.set_page_config(page_title="Cartfill Optimizer", page_icon="🌙", layout="wide")
App.apply_global_styles()

render_sidebar = App.render_sidebar
load_orders = App.load_overnight_cartfill_orders
load_context = App.load_overnight_cartfill_context
get_cartfill_available_range = getattr(App, "get_cartfill_available_range", lambda: (None, None, 0))


CURRENT_WAVES = ["0600", "0900", "1400", "1700", "2000"]
DEFAULT_PROPOSED_WAVES = ["0600", "0900", "1530", "2000"]
TWO_WAVE_WAVES = ["0600", "1700"]
Q2H_WAVES = ["0600", "0800", "1000", "1200", "1400", "1600", "1800", "2000"]

# Weighted redistribution based on the agreed due-window redesign:
# 0600: 1000-1400
# 0900: 1400-2100
# 1530: 2100-0500
# 2000: 0500-1000
PROPOSED_SPLIT = {
    "0600": {"0600": 1.0},
    "0900": {"0900": 1.0},
    "1400": {"0900": 1 / 3, "1530": 2 / 3},
    "1700": {"1530": 1.0},
    "2000": {"0600": 1 / 6, "2000": 5 / 6},
}

PROPOSED_DOSE_WINDOWS = {
    "0600": "1000-1400",
    "0900": "1400-2100",
    "1530": "2100-0500",
    "2000": "0500-1000",
}

FRONTLOAD_BASIS = {
    "0600": "Moved from 0600, 0900, 1400, 1700",
    "1700": "Moved from 2000",
}

SCENARIOS = {
    "proposed": {
        "label": "Current Proposed",
        "waves": DEFAULT_PROPOSED_WAVES,
        "split": PROPOSED_SPLIT,
        "note": "Existing proposed model: 0600, 0900, 1530, and 2000 with weighted due-window redistribution.",
    },
    "two_wave_balanced": {
        "label": "0600 / 1700 Cartfills",
        "waves": TWO_WAVE_WAVES,
        "split": None,
        "method": "due_deadline",
        "note": "Batches orders into 0600 or 1700 based on dose due time, keeping a 2-hour delivery buffer.",
    },
    "two_wave_frontload": {
        "label": "0600 Heavy + 1700 Backup",
        "waves": TWO_WAVE_WAVES,
        "split": {
            "0600": {"0600": 1.0},
            "0900": {"0600": 1.0},
            "1400": {"0600": 1.0},
            "1700": {"0600": 1.0},
            "2000": {"1700": 1.0},
        },
        "method": "current_wave_split",
        "note": "Stress-tests the idea of moving the 0900, 1400, and 1700 work into 0600, leaving 1700 as the late cartfill.",
    },
    "q2h": {
        "label": "Cartfill Every 2 Hours",
        "waves": Q2H_WAVES,
        "split": None,
        "method": "due_deadline",
        "note": "Batches orders every 2 hours based on dose due time, keeping a 2-hour delivery buffer.",
    },
}


def wave_to_minutes(wave):
    return int(wave[:2]) * 60 + int(wave[2:])


def minutes_to_label(minutes):
    minutes = minutes % (24 * 60)
    return f"{minutes // 60:02d}{minutes % 60:02d}"


def due_window_map_for_waves(waves, lead_hours=2):
    if not waves:
        return {}
    sorted_waves = sorted(waves, key=wave_to_minutes)
    windows = {}
    for index, wave in enumerate(sorted_waves):
        next_wave = sorted_waves[(index + 1) % len(sorted_waves)]
        start = wave_to_minutes(wave) + (lead_hours * 60)
        end = wave_to_minutes(next_wave) + (lead_hours * 60)
        windows[wave] = f"{minutes_to_label(start)}-{minutes_to_label(end)}"
    return windows


def scenario_basis_map(scenario_key, scenario):
    if scenario_key == "proposed":
        return PROPOSED_DOSE_WINDOWS
    if scenario_key == "two_wave_frontload":
        return FRONTLOAD_BASIS
    if scenario.get("method") == "due_deadline":
        return due_window_map_for_waves(scenario["waves"], lead_hours=2)
    return {}


def nearest_wave_label(ts):
    if pd.isna(ts):
        return "Unknown"
    value = ts.hour + (ts.minute / 60)
    anchors = {"0600": 6, "0900": 9, "1400": 14, "1700": 17, "2000": 20}
    return min(anchors, key=lambda label: abs(value - anchors[label]))


def nearest_custom_wave_label(ts, waves):
    if pd.isna(ts):
        return "Unknown"
    value = ts.hour + (ts.minute / 60)
    anchors = {wave: int(wave[:2]) + int(wave[2:]) / 60 for wave in waves}
    return min(anchors, key=lambda label: abs(value - anchors[label]))


def next_custom_wave_label(ts, waves):
    if pd.isna(ts):
        return "Unknown"
    value = ts.hour + (ts.minute / 60)
    anchors = [(wave, int(wave[:2]) + int(wave[2:]) / 60) for wave in waves]
    for wave, hour_value in sorted(anchors, key=lambda item: item[1]):
        if value <= hour_value:
            return wave
    return waves[0]


def cartfill_for_due_deadline(due_ts, waves, lead_hours=2):
    if pd.isna(due_ts):
        return "Unknown"
    deadline = due_ts - pd.Timedelta(hours=lead_hours)
    deadline_value = deadline.hour + (deadline.minute / 60)
    anchors = [(wave, int(wave[:2]) + int(wave[2:]) / 60) for wave in waves]
    eligible = [item for item in anchors if item[1] <= deadline_value]
    if eligible:
        return max(eligible, key=lambda item: item[1])[0]
    return max(anchors, key=lambda item: item[1])[0]


def prepare_daily_current(df):
    if df.empty:
        return pd.DataFrame(columns=["event_date", "wave", "total_orders", "administered_orders", "not_administered_orders"])

    daily = (
        df.groupby(["event_date", "current_wave"], as_index=False)
        .agg(
            total_orders=("pk", "count"),
            administered_orders=("was_administered", "sum"),
        )
        .rename(columns={"current_wave": "wave"})
    )
    daily["not_administered_orders"] = daily["total_orders"] - daily["administered_orders"]
    return daily


def prepare_daily_split(current_daily, split_map):
    if current_daily.empty:
        return pd.DataFrame(columns=["event_date", "wave", "total_orders", "administered_orders", "not_administered_orders"])

    rows = []
    for row in current_daily.itertuples(index=False):
        wave_split = split_map.get(row.wave, {})
        for proposed_wave, weight in wave_split.items():
            rows.append(
                {
                    "event_date": row.event_date,
                    "wave": proposed_wave,
                    "total_orders": row.total_orders * weight,
                    "administered_orders": row.administered_orders * weight,
                    "not_administered_orders": row.not_administered_orders * weight,
                }
            )

    proposed = pd.DataFrame(rows)
    if proposed.empty:
        return proposed

    return (
        proposed.groupby(["event_date", "wave"], as_index=False)
        .sum(numeric_only=True)
    )


def prepare_daily_nearest_wave(df, waves):
    if df.empty:
        return pd.DataFrame(columns=["event_date", "wave", "total_orders", "administered_orders", "not_administered_orders"])
    work = df.copy()
    work["scenario_wave"] = work["ready_for_dispense_dt"].apply(lambda ts: next_custom_wave_label(ts, waves))
    daily = (
        work.groupby(["event_date", "scenario_wave"], as_index=False)
        .agg(
            total_orders=("pk", "count"),
            administered_orders=("was_administered", "sum"),
        )
        .rename(columns={"scenario_wave": "wave"})
    )
    daily["not_administered_orders"] = daily["total_orders"] - daily["administered_orders"]
    return daily


def prepare_daily_due_deadline(df, waves, lead_hours=2):
    if df.empty:
        return pd.DataFrame(columns=["event_date", "wave", "total_orders", "administered_orders", "not_administered_orders"])
    work = df.copy()
    if "dose_due_dt" not in work.columns:
        work["dose_due_dt"] = pd.NaT

    administered = work[work["dose_due_dt"].notna()].copy()
    missing_due = work[work["dose_due_dt"].isna()].copy()
    frames = []
    if not administered.empty:
        administered["scenario_wave"] = administered["dose_due_dt"].apply(
            lambda ts: cartfill_for_due_deadline(ts, waves, lead_hours=lead_hours)
        )
        frames.append(administered)
    if not missing_due.empty:
        missing_due["scenario_wave"] = missing_due["ready_for_dispense_dt"].apply(
            lambda ts: next_custom_wave_label(ts, waves)
        )
        frames.append(missing_due)
    if not frames:
        return pd.DataFrame(columns=["event_date", "wave", "total_orders", "administered_orders", "not_administered_orders"])

    work = pd.concat(frames, ignore_index=True)
    daily = (
        work.groupby(["event_date", "scenario_wave"], as_index=False)
        .agg(
            total_orders=("pk", "count"),
            administered_orders=("was_administered", "sum"),
        )
        .rename(columns={"scenario_wave": "wave"})
    )
    daily["not_administered_orders"] = daily["total_orders"] - daily["administered_orders"]
    return daily


def prepare_daily_two_wave_deadline(df, lead_hours=2):
    if df.empty:
        return pd.DataFrame(columns=["event_date", "wave", "total_orders", "administered_orders", "not_administered_orders"])
    work = df.copy()
    if "dose_due_dt" not in work.columns:
        work["dose_due_dt"] = pd.NaT

    administered = work[work["dose_due_dt"].notna()].copy()
    missing_due = work[work["dose_due_dt"].isna()].copy()
    frames = []
    if not administered.empty:
        administered["scenario_wave"] = administered["dose_due_dt"].apply(
            lambda ts: cartfill_for_due_deadline(ts, TWO_WAVE_WAVES, lead_hours=lead_hours)
        )
        frames.append(administered)
    if not missing_due.empty:
        current_to_two_wave = {
            "0600": "0600",
            "0900": "0600",
            "1400": "0600",
            "1700": "1700",
            "2000": "1700",
        }
        missing_due["scenario_wave"] = missing_due["current_wave"].map(current_to_two_wave).fillna("1700")
        frames.append(missing_due)
    if not frames:
        return pd.DataFrame(columns=["event_date", "wave", "total_orders", "administered_orders", "not_administered_orders"])

    work = pd.concat(frames, ignore_index=True)
    daily = (
        work.groupby(["event_date", "scenario_wave"], as_index=False)
        .agg(
            total_orders=("pk", "count"),
            administered_orders=("was_administered", "sum"),
        )
        .rename(columns={"scenario_wave": "wave"})
    )
    daily["not_administered_orders"] = daily["total_orders"] - daily["administered_orders"]
    return daily


def build_average_table(daily_df, dates, wave_order, label, basis_map=None):
    if not dates:
        return pd.DataFrame(
            columns=[
                "model",
                "wave",
                "dose_due_window",
                "avg_total_per_day",
                "avg_administered_per_day",
                "avg_not_administered_per_day",
                "waste_rate",
            ]
        )

    basis_map = basis_map or {}
    full_index = pd.MultiIndex.from_product([dates, wave_order], names=["event_date", "wave"])
    daily = (
        daily_df.set_index(["event_date", "wave"])
        .reindex(full_index, fill_value=0)
        .reset_index()
    )
    averages = (
        daily.groupby("wave", as_index=False)[["total_orders", "administered_orders", "not_administered_orders"]]
        .mean()
        .rename(
            columns={
                "total_orders": "avg_total_per_day",
                "administered_orders": "avg_administered_per_day",
                "not_administered_orders": "avg_not_administered_per_day",
            }
        )
    )
    averages.insert(0, "model", label)
    averages.insert(2, "dose_due_window", averages["wave"].map(basis_map).fillna(""))
    averages["waste_rate"] = averages.apply(
        lambda row: row["avg_not_administered_per_day"] / row["avg_total_per_day"] if row["avg_total_per_day"] else 0,
        axis=1,
    )
    return averages


def build_period_tables(current_daily, proposed_daily, focus_df, wave_order_current, wave_order_proposed, proposed_basis):
    dates = sorted(pd.to_datetime(focus_df["event_date"].dropna().unique()).tolist())
    weekday_dates = sorted(d for d in dates if pd.Timestamp(d).weekday() < 5)
    weekend_dates = sorted(d for d in dates if pd.Timestamp(d).weekday() >= 5)

    return {
        "overall": (
            build_average_table(current_daily, dates, wave_order_current, "Current"),
            build_average_table(proposed_daily, dates, wave_order_proposed, "Proposed", proposed_basis),
            len(dates),
        ),
        "weekday": (
            build_average_table(
                current_daily[current_daily["event_date"].isin(pd.to_datetime(weekday_dates))],
                weekday_dates,
                wave_order_current,
                "Current",
            ),
            build_average_table(
                proposed_daily[proposed_daily["event_date"].isin(pd.to_datetime(weekday_dates))],
                weekday_dates,
                wave_order_proposed,
                "Proposed",
                proposed_basis,
            ),
            len(weekday_dates),
        ),
        "weekend": (
            build_average_table(
                current_daily[current_daily["event_date"].isin(pd.to_datetime(weekend_dates))],
                weekend_dates,
                wave_order_current,
                "Current",
            ),
            build_average_table(
                proposed_daily[proposed_daily["event_date"].isin(pd.to_datetime(weekend_dates))],
                weekend_dates,
                wave_order_proposed,
                "Proposed",
                proposed_basis,
            ),
            len(weekend_dates),
        ),
    }


def style_average_table(df):
    if df.empty:
        return df
    styled = df.copy()
    for col in ["avg_total_per_day", "avg_administered_per_day", "avg_not_administered_per_day"]:
        styled[col] = styled[col].map(lambda x: f"{x:.1f}")
    styled["waste_rate"] = styled["waste_rate"].map(lambda x: f"{x * 100:.1f}%")
    return styled.rename(
        columns={
            "model": "Model",
            "wave": "Cartfill",
            "dose_due_window": "Doses Due / Basis",
            "avg_total_per_day": "Avg Total / Day",
            "avg_administered_per_day": "Avg Admined / Day",
            "avg_not_administered_per_day": "Avg Not Admined / Day",
            "waste_rate": "Likely Waste Rate",
        }
    )


start_date, end_date = render_sidebar()

if hasattr(App, "render_page_intro"):
    App.render_page_intro(
        "Cartfill Optimizer",
        "Track cleanroom cartfill volume, compare current versus proposed waves, and use actual administered versus not-administered orders to redesign capacity.",
        kicker="Operations",
    )
    _debug_event("Cartfill Optimizer", "shared_intro_loaded")
    _debug_panel("Cartfill Optimizer", intro_mode="shared")
else:
    st.header("🌙 Cartfill Optimizer")
    st.caption("Track IV cartfill volume and compare current versus proposed cartfill models.")
    _debug_event("Cartfill Optimizer", "fallback_header_used")
    _debug_panel("Cartfill Optimizer", intro_mode="fallback")

with st.spinner("Loading cartfill data..."):
    df_orders = load_orders(start_date, end_date)
    df_windows, df_staffing = load_context()

if df_orders.empty:
    available_min, available_max, available_rows = get_cartfill_available_range()
    if available_min and available_max:
        st.info(
            "No cartfill data matched the selected date range. "
            f"The uploaded cartfill table currently has {available_rows:,} rows from "
            f"{pd.to_datetime(available_min).date()} through {pd.to_datetime(available_max).date()}."
        )
    else:
        st.info("No cartfill data found. Upload `Cartfill Stats (All Areas)` from the sidebar to get started.")
    st.stop()

orders = df_orders.copy()
for col in ["ready_for_dispense_dt", "admin_given_dt", "prepared_dt", "event_date"]:
    if col in orders.columns:
        orders[col] = pd.to_datetime(orders[col], errors="coerce")

orders["pharmacy"] = orders["pharmacy"].fillna("Unknown").astype(str).str.strip()
orders["prep_or_dispense_user"] = orders["prep_or_dispense_user"].fillna("Unknown").astype(str).str.strip()
orders["order_medication"] = orders["order_medication"].fillna("Unknown").astype(str).str.strip()
orders["is_sjs_cleanroom"] = orders["is_sjs_cleanroom"].fillna(False)
orders["current_wave"] = orders["ready_for_dispense_dt"].apply(nearest_wave_label)
orders["was_administered"] = orders["admin_given_dt"].notna().astype(int)
orders["dose_due_dt"] = orders["admin_given_dt"]
orders["prep_lead_hours"] = pd.to_numeric(orders["prep_lead_hours"], errors="coerce")
orders["hold_hours"] = pd.to_numeric(orders["hold_hours"], errors="coerce")

pharmacy_options = sorted(orders["pharmacy"].dropna().unique().tolist())
default_pharmacy = ["SJS Cleanroom"] if "SJS Cleanroom" in pharmacy_options else pharmacy_options
selected_pharmacies = st.multiselect("Pharmacy", pharmacy_options, default=default_pharmacy)

filtered = orders.copy()
if selected_pharmacies:
    filtered = filtered[filtered["pharmacy"].isin(selected_pharmacies)]

if filtered.empty:
    st.warning("No overnight cartfill records match the current filters.")
    st.stop()

cleanroom = filtered[filtered["is_sjs_cleanroom"]].copy()
focus = cleanroom if not cleanroom.empty else filtered.copy()
focus = focus[focus["event_date"].notna()].copy()
focus["event_date"] = pd.to_datetime(focus["event_date"], errors="coerce").dt.normalize()

if focus.empty:
    st.warning("The selected records do not have usable cartfill dates.")
    st.stop()

current_daily = prepare_daily_current(focus)

scenario_key = st.selectbox(
    "What-if cartfill model",
    options=list(SCENARIOS.keys()),
    format_func=lambda key: SCENARIOS[key]["label"],
    index=1,
)
scenario = SCENARIOS[scenario_key]
if scenario.get("method") == "due_deadline":
    if scenario_key == "two_wave_balanced":
        proposed_daily = prepare_daily_two_wave_deadline(focus, lead_hours=2)
    else:
        proposed_daily = prepare_daily_due_deadline(focus, scenario["waves"], lead_hours=2)
    due_mode_rows = int(focus["dose_due_dt"].notna().sum())
else:
    proposed_daily = prepare_daily_split(current_daily, scenario["split"])
    due_mode_rows = len(focus)
proposed_basis = scenario_basis_map(scenario_key, scenario)
period_tables = build_period_tables(current_daily, proposed_daily, focus, CURRENT_WAVES, scenario["waves"], proposed_basis)

total_orders = len(focus)
total_days = len(sorted(pd.to_datetime(focus["event_date"].dropna().unique()).tolist()))
total_admined = int(focus["was_administered"].sum())
total_not_admined = int(total_orders - total_admined)
avg_per_day = total_orders / total_days if total_days else 0

m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("Cleanroom Orders", f"{total_orders:,}")
m2.metric("Avg Orders / Day", f"{avg_per_day:.1f}")
m3.metric("Administered", f"{total_admined:,}")
m4.metric("Not Administered", f"{total_not_admined:,}")
m5.metric("Likely Waste Rate", f"{(total_not_admined / total_orders * 100):.1f}%" if total_orders else "N/A")

st.caption(
    "Current cartfill volume uses the actual `Ready for Dispense` cartfill start time. "
    "Not-administered orders are rows with no `Admin Given Date & Time`. "
    f"Selected model: {scenario['note']}"
)

if scenario_key in {"two_wave_balanced", "two_wave_frontload"}:
    st.info(
        "Staffing assumption for this what-if: two techs start at 0600, the former 0800 tech shifts to "
        "1000-1830, and the late cartfill remains at 1700. The charts below show order volume movement; "
        "labor coverage is shown as an operating assumption, not a payroll calculation."
    )

if scenario.get("method") == "due_deadline":
    missing_due = len(focus) - due_mode_rows
    st.caption(
        f"Dose due time is approximated from `Admin Given Date & Time`; {due_mode_rows:,} administered rows "
        "use that due-time proxy."
    )
    if missing_due:
        st.info(
            f"{missing_due:,} rows had no Admin Given Date & Time. They stay in the model as likely waste and are "
            "assigned from the cartfill start time because no scheduled dose due time is available."
        )

st.divider()

st.subheader("Current vs Proposed Cartfill Volume")

period_label_map = {
    "overall": "Overall",
    "weekday": "Weekday",
    "weekend": "Weekend",
}
selected_period = st.segmented_control(
    "Average Type",
    options=list(period_label_map.keys()),
    default="overall",
    format_func=lambda x: period_label_map[x],
)

current_avg, proposed_avg, period_days = period_tables[selected_period]
comparison = pd.concat([current_avg, proposed_avg], ignore_index=True)
waste_summary = (
    comparison.groupby("model", as_index=False)
    .agg(
        avg_total_per_day=("avg_total_per_day", "sum"),
        avg_not_administered_per_day=("avg_not_administered_per_day", "sum"),
    )
)
waste_summary["waste_rate"] = waste_summary.apply(
    lambda row: row["avg_not_administered_per_day"] / row["avg_total_per_day"] if row["avg_total_per_day"] else 0,
    axis=1,
)

c1, c2 = st.columns([1.1, 0.9])

with c1:
    st.caption(f"{period_label_map[selected_period]} averages across {period_days} days")
    st.dataframe(
        style_average_table(comparison),
        width="stretch",
        hide_index=True,
    )

    scenario_totals = proposed_avg[["wave", "avg_total_per_day"]].copy()
    if not scenario_totals.empty:
        peak_wave = scenario_totals.sort_values("avg_total_per_day", ascending=False).iloc[0]
        st.metric(
            "Busiest Proposed Cartfill",
            f"{peak_wave['wave']}",
            f"{peak_wave['avg_total_per_day']:.1f} avg orders/day",
        )

with c2:
    fig_compare = px.bar(
        comparison,
        x="wave",
        y="avg_total_per_day",
        color="model",
        barmode="group",
        category_orders={"wave": CURRENT_WAVES + scenario["waves"]},
        labels={"wave": "Cartfill", "avg_total_per_day": "Avg Orders / Day", "model": "Model"},
        text_auto=".1f",
    )
    fig_compare.update_layout(height=360)
    st.plotly_chart(fig_compare, width="stretch")

st.subheader("Likely Waste Comparison")
waste_display = waste_summary.copy()
waste_display["avg_total_per_day"] = waste_display["avg_total_per_day"].map(lambda x: f"{x:.1f}")
waste_display["avg_not_administered_per_day"] = waste_display["avg_not_administered_per_day"].map(lambda x: f"{x:.1f}")
waste_display["waste_rate"] = waste_display["waste_rate"].map(lambda x: f"{x * 100:.1f}%")
waste_display = waste_display.rename(
    columns={
        "model": "Model",
        "avg_total_per_day": "Avg Total / Day",
        "avg_not_administered_per_day": "Avg Likely Waste / Day",
        "waste_rate": "Likely Waste Rate",
    }
)
st.dataframe(waste_display, width="stretch", hide_index=True)
if len(waste_summary) == 2:
    current_waste = waste_summary.loc[waste_summary["model"] == "Current", "avg_not_administered_per_day"].sum()
    proposed_waste = waste_summary.loc[waste_summary["model"] == "Proposed", "avg_not_administered_per_day"].sum()
    st.metric(
        "Likely Waste Difference",
        f"{proposed_waste - current_waste:+.1f} avg orders/day",
        help="Negative means the selected model has fewer likely waste rows per day; positive means more.",
    )
st.caption(
    "This uses missing `Admin Given Date & Time` as likely waste. The selected model changes which cartfill wave "
    "carries that waste; total likely waste only changes if a workflow change actually prevents doses from going unused."
)

detail_col1, detail_col2 = st.columns(2)

with detail_col1:
    fig_admin = px.bar(
        comparison,
        x="wave",
        y="avg_administered_per_day",
        color="model",
        barmode="group",
        category_orders={"wave": CURRENT_WAVES + scenario["waves"]},
        labels={"wave": "Cartfill", "avg_administered_per_day": "Avg Admined / Day", "model": "Model"},
        text_auto=".1f",
    )
    fig_admin.update_layout(height=360)
    st.plotly_chart(fig_admin, width="stretch")

with detail_col2:
    fig_not_admin = px.bar(
        comparison,
        x="wave",
        y="avg_not_administered_per_day",
        color="model",
        barmode="group",
        category_orders={"wave": CURRENT_WAVES + scenario["waves"]},
        labels={"wave": "Cartfill", "avg_not_administered_per_day": "Avg Not Admined / Day", "model": "Model"},
        text_auto=".1f",
    )
    fig_not_admin.update_layout(height=360)
    st.plotly_chart(fig_not_admin, width="stretch")

st.divider()

st.subheader("Current Cleanroom Cartfill Tracker")

wave_mix = (
    current_daily.groupby("wave", as_index=False)[["total_orders", "administered_orders", "not_administered_orders"]]
    .sum()
)
wave_mix["waste_rate"] = wave_mix["not_administered_orders"] / wave_mix["total_orders"]

tracker_col1, tracker_col2 = st.columns(2)

with tracker_col1:
    tracker_display = wave_mix.copy()
    tracker_display["waste_rate"] = tracker_display["waste_rate"].map(lambda x: f"{x * 100:.1f}%")
    tracker_display = tracker_display.rename(
        columns={
            "wave": "Current Cartfill",
            "total_orders": "Orders",
            "administered_orders": "Admined",
            "not_administered_orders": "Not Admined",
            "waste_rate": "Likely Waste Rate",
        }
    )
    st.dataframe(tracker_display, width="stretch", hide_index=True)

with tracker_col2:
    fig_tracker = px.bar(
        wave_mix,
        x="wave",
        y=["administered_orders", "not_administered_orders"],
        category_orders={"wave": CURRENT_WAVES},
        labels={"value": "Orders", "wave": "Current Cartfill", "variable": "Status"},
        barmode="stack",
    )
    fig_tracker.update_layout(height=360)
    st.plotly_chart(fig_tracker, width="stretch")

top_col1, top_col2 = st.columns(2)

with top_col1:
    st.subheader("Top Volume Medications")
    top_meds = (
        focus.groupby("order_medication", as_index=False)
        .agg(
            orders=("pk", "count"),
            administered=("was_administered", "sum"),
        )
        .sort_values("orders", ascending=False)
        .head(15)
    )
    top_meds["not_administered"] = top_meds["orders"] - top_meds["administered"]
    fig_meds = px.bar(
        top_meds.sort_values("orders"),
        x="orders",
        y="order_medication",
        orientation="h",
        labels={"orders": "Orders", "order_medication": ""},
        color="orders",
        color_continuous_scale="Tealgrn",
    )
    fig_meds.update_layout(coloraxis_showscale=False, height=420)
    st.plotly_chart(fig_meds, width="stretch")

with top_col2:
    st.subheader("Top Likely Waste Medications")
    waste_meds = (
        focus.groupby("order_medication", as_index=False)
        .agg(
            orders=("pk", "count"),
            not_administered=("was_administered", lambda s: int((1 - s).sum())),
        )
    )
    waste_meds = waste_meds[waste_meds["not_administered"] > 0].copy()
    waste_meds["waste_rate"] = waste_meds["not_administered"] / waste_meds["orders"]
    waste_meds = waste_meds.sort_values(["not_administered", "waste_rate"], ascending=[False, False]).head(15)
    fig_waste = px.bar(
        waste_meds.sort_values("not_administered"),
        x="not_administered",
        y="order_medication",
        orientation="h",
        labels={"not_administered": "Likely Waste Orders", "order_medication": ""},
        color="not_administered",
        color_continuous_scale="Reds",
    )
    fig_waste.update_layout(coloraxis_showscale=False, height=420)
    st.plotly_chart(fig_waste, width="stretch")

st.divider()

ctx_col1, ctx_col2 = st.columns(2)

with ctx_col1:
    st.subheader("Configured Cartfill Windows From Workbook")
    if df_windows.empty:
        st.info("No cartfill timing rows are stored yet from the workbook.")
    else:
        cleanroom_windows = df_windows[
            df_windows["pharmacy"].fillna("").astype(str).str.contains("Cleanroom", case=False, na=False)
        ].copy()
        if cleanroom_windows.empty:
            st.info("No cleanroom-specific timing rows were found in the workbook context.")
        else:
            st.dataframe(
                cleanroom_windows[["cartfill_name", "time_processed_raw", "doses_due", "pharmacy"]],
                width="stretch",
                hide_index=True,
            )

with ctx_col2:
    st.subheader("IV Staffing Snapshot")
    if df_staffing.empty:
        st.info("No staffing model rows are stored yet from the workbook.")
    else:
        staffing = df_staffing.copy()
        staffing["schedule_date"] = pd.to_datetime(staffing["schedule_date"], errors="coerce")
        if staffing["schedule_date"].notna().any():
            staffing = staffing[staffing["schedule_date"].dt.date.between(start_date, end_date)].copy()
        shift_text = staffing["shift_name"].fillna("").astype(str)
        iv_staff = staffing[
            shift_text.str.contains(r"\bIV\b|Cleanroom|Sterile", case=False, na=False, regex=True)
        ].copy()
        if iv_staff.empty:
            st.info("No IV staffing rows match the selected date range.")
        else:
            st.dataframe(
                iv_staff[["schedule_date", "day_name", "shift_name", "assigned_staff"]],
                width="stretch",
                hide_index=True,
            )
