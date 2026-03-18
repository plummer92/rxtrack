import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import io
from datetime import date, timedelta
from sqlalchemy import text
from App import engine

st.set_page_config(page_title="Carousel Drop Tracker", page_icon="📋", layout="wide")

st.header("📋 Carousel Drop Tracker")
st.caption("Track quantity loaded per Pyxis device at each scheduled carousel drop. Select a date to see that day's drops.")

# ═══════════════════════════════════════════════════════════════════════════════
# SCHEDULE DATA
# ═══════════════════════════════════════════════════════════════════════════════

AREA_COLOR = {
    "Stack":     "#22c55e",
    "Adult ICU": "#f97316",
    "ED":        "#facc15",
    "WCC":       "#ec4899",
    "OR":        "#818cf8",
    "PHP":       "#c084fc",
    "Montvale":  "#fda4af",
    "Other":     "#94a3b8",
}

# Device → hospital area
DEVICE_AREA = {
    **{d: "Stack" for d in [
        "SJS3E","SJS3N","SJS4E","SJS4N","SJS4S","SJS4W",
        "SJS5EN","SJS5ES","SJS5WN","SJS6EN","SJS6ES","SJS6WN",
        "SJS7EN","SJS7ES","SJS7WN","SJS8EN","SJS8ES","SJS8WN",
        "SJS9EN","SJS9ES","SJS9WN","SJS11E","SJS11N","SJS11W","SJSCCU",
    ]},
    **{d: "Adult ICU" for d in [
        "SJSCVICU-NE","SJSCVICU-NW","SJSCVICU-SE","SJSCVICU-SW",
        "SJSICA-N","SJSICA-S","SJSICU4C","SJSICD","SJSICE",
    ]},
    **{d: "ED" for d in [
        "SJSTRAUMA1","SJSTRAUMA2","SJSTRAUMA3","SJSEDSO","SJSEDTRIAG",
        "SJSEMS","SJSER","SJSBRONCH","SJSPEDISED","SJSRADM",
    ]},
    **{d: "WCC" for d in [
        "SJSCSECT1","SJSCSECT2","SJSNICUC","SJSNICUN","SJSNICUS",
        "SJSPICU","SJSBCC","SJSANTE","SJSBCAB","SJSBCTRIAG","SJSNICU",
        "SJSPEDN","SJSPEDS","SJSPIMC","SJSPEDOP","SJSPEDPREA",
    ]},
    **{d: "OR" for d in [
        "SJSCSOR","SJSCSOR-2","SJSCSOR-3","SJSCSOR-4","SJSCSOR-5",
        "SJSCSOR-6","SJSCSOR-7","SJSCSOR-8",
        "SJSOR","SJSOR2","SJSOR-1","SJSOR-2","SJSOR-3","SJSOR-4",
        "SJSOR-5","SJSOR-6","SJSOR-7","SJSOR-8","SJSOR-9","SJSOR-10",
        "SJSOR-11","SJSOR-12","SJSOR-13","SJSOR-14","SJSOR-15","SJSOR-16",
        "SJSPACU","SJSPACU-21","SJSPACU21MAIN","SJSPACUNOR",
        "SJSPREAN","SJSPREAN2","SJSRWC",
    ]},
    **{d: "PHP" for d in [
        "SJSCARDVS","SJSCARDVS2","SJSCARDVS3","SJSCATH4-ANES",
        "SJSCATHL1","SJSCATHL3","SJSCATHL4","SJSCATHL5","SJSCATHL6",
        "SJSCATHL7","SJSCATHL8","SJSCATHL9","SJSCATHL10","SJSCATHL11","SJSCATHL12",
        "SJSDAHP","SJSDIAL",
        "SJSGI1","SJSGI2","SJSGI3","SJSGI4","SJSGIMAIN",
        "SJSNUCMED","SJSOSC1","SJSOSC2","SJSOSC3","SJSOSC4","SJSOSC5",
        "SJSOSCOR","SJSOSCPACU","SJSPRECATH",
    ]},
    **{d: "Montvale" for d in ["SJSSM1","SJSSM2","SJSSMOR1","SJSSMOR2"]},
}

# Stockout-print configuration
STOCKOUTS_PRINT_AUTO = [
    "SJS7EN","SJS7ES","SJS7WN",                                    # Stack
    "SJSCVICU-NE","SJSCVICU-NW","SJSCVICU-SE","SJSCVICU-SW",       # Adult ICU
    "SJSICA-N","SJSICA-S","SJSICU4C",
    "SJSTRAUMA1","SJSTRAUMA2","SJSTRAUMA3",                         # ED
    "SJSEDSO","SJSEDTRIAG","SJSEMS","SJSER",
    "SJSCSECT1","SJSCSECT2","SJSNICUC","SJSNICUN","SJSNICUS",       # WCC
    "SJSPICU","SJSBCC",
    "SJSOR","SJSOR2",                                               # OR
]
STOCKOUTS_PRINT_AUTO_SET = set(STOCKOUTS_PRINT_AUTO)

# ── M-F Drop Schedule ──────────────────────────────────────────────────────────
_MF_0400_FULL = [
    "SJSCVICU-NE","SJSCVICU-NW","SJSCVICU-SE","SJSCVICU-SW",
    "SJSICA-N","SJSICA-S","SJSICU4C",
    "SJS11E","SJS11N","SJS11W","SJS9EN","SJS9ES","SJS9WN",
    "SJS8EN","SJS8ES","SJS8WN","SJS7EN","SJS7ES","SJS7WN",
    "SJS6EN","SJS6ES","SJS6WN","SJS5EN","SJS5ES","SJS5WN",
    "SJS3E","SJS3N","SJS4E","SJS4N","SJS4S",
]

_MF_0700_FULL = [
    "SJSBRONCH","SJSEDSO","SJSEDTRIAG","SJSEMS","SJSER",
    "SJSPEDISED","SJSRADM","SJSTRAUMA1","SJSTRAUMA2","SJSTRAUMA3",
    "SJSANTE","SJSBCAB","SJSBCC","SJSBCTRIAG","SJSCSECT1","SJSCSECT2",
    "SJSNICU","SJSNICUC","SJSNICUN","SJSNICUS",
    "SJSPEDN","SJSPEDOP","SJSPEDS","SJSPICU","SJSPIMC",
]
_MF_0700_WED = ["SJSSM1","SJSSM2","SJSSMOR1","SJSSMOR2"]

_MF_1235_FULL = [
    "SJSCVICU-NE","SJSCVICU-NW","SJSCVICU-SE","SJSCVICU-SW",
    "SJSICA-N","SJSICA-S","SJSICU4C",
    "SJSEDSO","SJSER",
    "SJSPEDN","SJSPEDS","SJSPICU","SJSPIMC",
    "SJS7EN","SJS7ES","SJS7WN",
]
_MF_1235_STOCK = [
    "SJS11E","SJS11N","SJS11W","SJS3E","SJS3N","SJSCCU",
    "SJS4E","SJS4N","SJS4W",
    "SJS5EN","SJS5ES","SJS5WN","SJS6EN","SJS6ES","SJS6WN",
    "SJS8EN","SJS8ES","SJS8WN","SJS9EN","SJS9ES","SJS9WN",
    "SJSANTE","SJSBCAB","SJSBCC","SJSBCTRIAG",
    "SJSNICU","SJSNICUC","SJSNICUN","SJSNICUS","SJSPEDOP",
    "SJSPEDPREA","SJSICE",
]

_MF_1430_FULL = [
    "SJSCSOR","SJSCSOR-2","SJSCSOR-3","SJSCSOR-4","SJSCSOR-5",
    "SJSCSOR-6","SJSCSOR-7","SJSCSOR-8",
    "SJSOR","SJSOR2","SJSOR-1","SJSOR-2","SJSOR-3","SJSOR-4",
    "SJSOR-5","SJSOR-6","SJSOR-7","SJSOR-8","SJSOR-9","SJSOR-10",
    "SJSOR-11","SJSOR-12","SJSOR-13","SJSOR-14","SJSOR-15","SJSOR-16",
    "SJSPACU","SJSPACU-21","SJSPACU21MAIN","SJSPACUNOR",
    "SJSPREAN","SJSPREAN2","SJSPEDPREA","SJSRWC","SJSICE",
    "SJSCARDVS","SJSCARDVS2","SJSCARDVS3","SJSCATH4-ANES",
    "SJSCATHL1","SJSCATHL3","SJSCATHL4","SJSCATHL5","SJSCATHL6",
    "SJSCATHL7","SJSCATHL8","SJSCATHL9","SJSCATHL10","SJSCATHL11","SJSCATHL12",
    "SJSDAHP","SJSDIAL",
    "SJSGI1","SJSGI2","SJSGI3","SJSGI4","SJSGIMAIN",
    "SJSNUCMED","SJSOSC1","SJSOSC2","SJSOSC3","SJSOSC4","SJSOSC5",
    "SJSOSCOR","SJSOSCPACU","SJSPRECATH",
]

# ── Sa-Su Drop Schedule ────────────────────────────────────────────────────────
_SASU_0400_FULL = [
    "SJSCVICU-NE","SJSCVICU-NW","SJSCVICU-SE","SJSCVICU-SW",
    "SJSICA-N","SJSICA-S","SJSICU4C",
    "SJS11E","SJS11N","SJS11W","SJS9EN","SJS9ES","SJS9WN",
    "SJS8EN","SJS8ES","SJS8WN","SJS7EN","SJS7ES","SJS7WN",
    "SJS6EN","SJS6ES","SJS6WN","SJS5EN","SJS5ES","SJS5WN",
    "SJS3E","SJS3N","SJS4E","SJS4N","SJS4W","SJS4S",
    # ED + WCC combined into weekend 0400
    "SJSEDSO","SJSEDTRIAG","SJSEMS","SJSER","SJSRADM",
    "SJSTRAUMA1","SJSTRAUMA2","SJSTRAUMA3","SJSBRONCH","SJSPEDISED",
    "SJSANTE","SJSBCAB","SJSBCC","SJSBCTRIAG","SJSCSECT1","SJSCSECT2",
    "SJSNICU","SJSNICUC","SJSNICUN","SJSNICUS",
    "SJSPEDN","SJSPEDOP","SJSPEDS","SJSPICU","SJSPIMC",
]

_SASU_1235_FULL = [
    "SJSCVICU-NE","SJSCVICU-NW","SJSCVICU-SE","SJSCVICU-SW",
    "SJSICA-N","SJSICA-S","SJSICU4C",
    "SJS7EN","SJS7ES","SJS7WN",
    "SJSCSECT1","SJSCSECT2","SJSPEDN","SJSPEDS","SJSPICU","SJSPIMC",
    # OR + PHP combined into weekend 1235 (M-F 1430 equivalent)
    "SJSCSOR","SJSCSOR-2","SJSCSOR-3","SJSCSOR-4","SJSCSOR-5",
    "SJSCSOR-6","SJSCSOR-7","SJSCSOR-8",
    "SJSOR","SJSOR2","SJSOR-1","SJSOR-2","SJSOR-3","SJSOR-4",
    "SJSOR-5","SJSOR-6","SJSOR-7","SJSOR-8","SJSOR-9","SJSOR-10",
    "SJSOR-11","SJSOR-12","SJSOR-13","SJSOR-14","SJSOR-15","SJSOR-16",
    "SJSPACU","SJSPACU-21","SJSPACU21MAIN","SJSPACUNOR",
    "SJSPREAN","SJSPREAN2","SJSPEDPREA","SJSRWC","SJSICE",
    "SJSCARDVS","SJSCARDVS2","SJSCARDVS3","SJSCATH4-ANES",
    "SJSCATHL1","SJSCATHL3","SJSCATHL4","SJSCATHL5","SJSCATHL6",
    "SJSCATHL7","SJSCATHL8","SJSCATHL9","SJSCATHL10","SJSCATHL11","SJSCATHL12",
    "SJSDAHP","SJSDIAL",
    "SJSGI1","SJSGI2","SJSGI3","SJSGI4","SJSGIMAIN",
    "SJSNUCMED","SJSOSC1","SJSOSC2","SJSOSC3","SJSOSC4","SJSOSC5",
    "SJSOSCOR","SJSOSCPACU","SJSPRECATH",
]
_SASU_1235_STOCK = [
    "SJS11E","SJS11N","SJS11W","SJS3E","SJS3N","SJSCCU",
    "SJS4E","SJS4N","SJS4W",
    "SJS5EN","SJS5ES","SJS5WN","SJS6EN","SJS6ES","SJS6WN",
    "SJS8EN","SJS8ES","SJS8WN","SJS9EN","SJS9ES","SJS9WN",
    "SJSANTE","SJSBCAB","SJSBCC","SJSBCTRIAG",
    "SJSNICU","SJSNICUC","SJSNICUN","SJSNICUS","SJSPEDOP",
]


def get_schedule(sel_date):
    """Return list of drop dicts for the given date."""
    dow = sel_date.weekday()  # 0=Mon … 6=Sun
    is_weekend = dow >= 5
    is_wednesday = dow == 2

    if is_weekend:
        return [
            {
                "label": "0400 Drop",
                "time": "04:00",
                "win_start": (3, 0),
                "win_end": (11, 30),
                "full": _SASU_0400_FULL,
                "stockouts": [],
            },
            {
                "label": "1235 Drop",
                "time": "12:35",
                "win_start": (11, 31),
                "win_end": (20, 0),
                "full": _SASU_1235_FULL,
                "stockouts": _SASU_1235_STOCK,
            },
        ]
    else:
        mf_0700 = _MF_0700_FULL + (_MF_0700_WED if is_wednesday else [])
        return [
            {
                "label": "0400 Drop",
                "time": "04:00",
                "win_start": (3, 0),
                "win_end": (6, 29),
                "full": _MF_0400_FULL,
                "stockouts": [],
            },
            {
                "label": "0700 Drop",
                "time": "07:00",
                "win_start": (6, 30),
                "win_end": (11, 0),
                "full": mf_0700,
                "stockouts": [],
                "wed_note": is_wednesday,
            },
            {
                "label": "1235 Drop",
                "time": "12:35",
                "win_start": (10, 30),
                "win_end": (14, 14),
                "full": _MF_1235_FULL,
                "stockouts": _MF_1235_STOCK,
            },
            {
                "label": "1430 Drop",
                "time": "14:30",
                "win_start": (14, 0),
                "win_end": (20, 0),
                "full": _MF_1430_FULL,
                "stockouts": [],
            },
        ]


# ═══════════════════════════════════════════════════════════════════════════════
# DATA LOADER
# ═══════════════════════════════════════════════════════════════════════════════

@st.cache_data(ttl=300)
def load_replenishment(sel_date):
    """All load/refill/restock events for the selected date."""
    try:
        sql = text("""
            SELECT pk, dt, user_name, device, med_id, med_desc, event_type,
                   qty, beginning_qty, ending_qty
            FROM events
            WHERE dt::date = :d
              AND (
                    event_type ILIKE '%load%'
                 OR event_type ILIKE '%refill%'
                 OR event_type ILIKE '%restock%'
                 OR event_type ILIKE '%replenish%'
              )
              AND event_type NOT ILIKE '%unload%'
              AND event_type NOT ILIKE '%cancel%'
              AND event_type NOT ILIKE '%empty%'
        """)
        with engine.connect() as conn:
            df = pd.read_sql(sql, conn, params={"d": sel_date})
        df["dt"]        = pd.to_datetime(df["dt"], errors="coerce")
        df["device"]    = df["device"].fillna("Unknown").astype(str).str.strip()
        df["user_name"] = df["user_name"].fillna("Unknown").astype(str).str.strip()
        df["qty"]       = pd.to_numeric(df["qty"], errors="coerce").fillna(0)
        return df
    except Exception as e:
        st.error(f"[load_replenishment] {e}")
        return pd.DataFrame()


def to_excel_bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False)
    return buf.getvalue()


# ═══════════════════════════════════════════════════════════════════════════════
# DATE SELECTOR
# ═══════════════════════════════════════════════════════════════════════════════

with st.sidebar:
    st.markdown("### 📅 Select Drop Date")
    sel_date = st.date_input("Date", value=date.today(), key="cdt_date")
    st.caption(f"Day: **{sel_date.strftime('%A')}**")
    is_weekend = sel_date.weekday() >= 5
    st.info("Sa-Su schedule active." if is_weekend else "M-F schedule active." +
            (" (Wednesday — SM/SMOR included)" if sel_date.weekday() == 2 else ""))

with st.spinner("Loading replenishment events..."):
    df_loads = load_replenishment(sel_date)

schedule = get_schedule(sel_date)


# ═══════════════════════════════════════════════════════════════════════════════
# HELPER — BUILD DROP TABLE
# ═══════════════════════════════════════════════════════════════════════════════

def build_drop_df(drop, df_loads):
    """Returns a DataFrame of all scheduled devices for a drop with actual qty/events."""
    h0, m0 = drop["win_start"]
    h1, m1 = drop["win_end"]
    start_min = h0 * 60 + m0
    end_min   = h1 * 60 + m1

    if not df_loads.empty:
        ev_min = df_loads["dt"].dt.hour * 60 + df_loads["dt"].dt.minute
        window = df_loads[(ev_min >= start_min) & (ev_min <= end_min)]
    else:
        window = pd.DataFrame()

    # Aggregate by device
    if not window.empty:
        agg = (
            window.groupby("device")
            .agg(
                items_loaded = ("pk",        "count"),
                total_qty    = ("qty",       "sum"),
                techs        = ("user_name", lambda x: ", ".join(sorted(x.dropna().astype(str).unique()))),
                first_time   = ("dt",        "min"),
                last_time    = ("dt",        "max"),
            )
            .reset_index()
        )
    else:
        agg = pd.DataFrame(columns=["device","items_loaded","total_qty","techs","first_time","last_time"])

    rows = []
    for dev in drop["full"]:
        m = agg[agg["device"] == dev]
        row = {
            "device":        dev,
            "area":          DEVICE_AREA.get(dev, "Other"),
            "drop_type":     "Full Drop",
            "stockout_print": "✅ Auto-Print" if dev in STOCKOUTS_PRINT_AUTO_SET else "❌ No Print",
        }
        if not m.empty:
            row["items_loaded"] = int(m["items_loaded"].iloc[0])
            row["total_qty"]    = float(m["total_qty"].iloc[0])
            row["techs"]        = m["techs"].iloc[0]
            row["first_time"]   = m["first_time"].iloc[0]
            row["last_time"]    = m["last_time"].iloc[0]
            row["status"]       = "✅ Touched"
        else:
            row["items_loaded"] = 0
            row["total_qty"]    = 0.0
            row["techs"]        = ""
            row["first_time"]   = None
            row["last_time"]    = None
            row["status"]       = "❌ Missed"
        rows.append(row)

    for dev in drop["stockouts"]:
        m = agg[agg["device"] == dev]
        row = {
            "device":        dev,
            "area":          DEVICE_AREA.get(dev, "Other"),
            "drop_type":     "Stockouts Only",
            "stockout_print": "✅ Auto-Print" if dev in STOCKOUTS_PRINT_AUTO_SET else "❌ No Print",
        }
        if not m.empty:
            row["items_loaded"] = int(m["items_loaded"].iloc[0])
            row["total_qty"]    = float(m["total_qty"].iloc[0])
            row["techs"]        = m["techs"].iloc[0]
            row["first_time"]   = m["first_time"].iloc[0]
            row["last_time"]    = m["last_time"].iloc[0]
            row["status"]       = "✅ Touched (Stockout)"
        else:
            row["items_loaded"] = 0
            row["total_qty"]    = 0.0
            row["techs"]        = ""
            row["first_time"]   = None
            row["last_time"]    = None
            row["status"]       = "🔵 No Stockout"
        rows.append(row)

    return pd.DataFrame(rows)


# ═══════════════════════════════════════════════════════════════════════════════
# TOP-LINE KPIs (across all drops for the day)
# ═══════════════════════════════════════════════════════════════════════════════

all_drop_dfs = {d["label"]: build_drop_df(d, df_loads) for d in schedule}
combined = pd.concat(all_drop_dfs.values(), ignore_index=True) if all_drop_dfs else pd.DataFrame()

total_scheduled  = int((combined["drop_type"] == "Full Drop").sum()) if not combined.empty else 0
total_touched    = int((combined["status"] == "✅ Touched").sum())   if not combined.empty else 0
total_missed     = int((combined["status"] == "❌ Missed").sum())    if not combined.empty else 0
stockout_touched = int((combined["status"] == "✅ Touched (Stockout)").sum()) if not combined.empty else 0
total_items      = int(df_loads["pk"].count()) if not df_loads.empty else 0
total_qty        = float(df_loads["qty"].sum()) if not df_loads.empty else 0.0
completion_pct   = round(total_touched / total_scheduled * 100, 1) if total_scheduled > 0 else 0.0

k1, k2, k3, k4, k5, k6 = st.columns(6)
k1.metric("Scheduled Full Drops", total_scheduled)
k2.metric("✅ Completed", total_touched, delta=f"{completion_pct}%")
k3.metric("❌ Missed", total_missed)
k4.metric("🔵 Stockout Activations", stockout_touched)
k5.metric("Total Med Transactions", f"{total_items:,}")
k6.metric("Total Units Loaded", f"{total_qty:,.0f}")

if total_missed > 0:
    st.warning(f"⚠️ **{total_missed} device(s) scheduled for a full drop were not touched** — see per-drop tabs below.")
elif total_scheduled > 0:
    st.success(f"✅ All {total_scheduled} full-drop devices completed for {sel_date.strftime('%A %b %d, %Y')}.")

st.divider()


# ═══════════════════════════════════════════════════════════════════════════════
# TABS — one per drop + summary + reference
# ═══════════════════════════════════════════════════════════════════════════════

drop_labels = [d["label"] for d in schedule]
tab_labels  = drop_labels + ["📊 Day Summary", "📅 Schedule Reference", "🖨️ Stockout Print Config"]
tabs        = st.tabs(tab_labels)

# ── Per-drop tabs ──────────────────────────────────────────────────────────────
for i, drop in enumerate(schedule):
    with tabs[i]:
        drop_df = all_drop_dfs[drop["label"]]
        full_df  = drop_df[drop_df["drop_type"] == "Full Drop"]
        stock_df = drop_df[drop_df["drop_type"] == "Stockouts Only"]

        touched_ct  = int((full_df["status"] == "✅ Touched").sum())
        missed_ct   = int((full_df["status"] == "❌ Missed").sum())
        total_full  = len(full_df)
        drop_items  = int(full_df["items_loaded"].sum())
        drop_qty    = float(full_df["total_qty"].sum())

        st.subheader(f"{drop['label']} — Scheduled {drop['time']}")
        if drop.get("wed_note"):
            st.info("📅 Wednesday: SM/SMOR devices included in this drop.")

        d1, d2, d3, d4, d5 = st.columns(5)
        d1.metric("Devices Scheduled", total_full)
        d2.metric("✅ Touched", touched_ct)
        d3.metric("❌ Missed", missed_ct)
        d4.metric("Med Transactions", drop_items)
        d5.metric("Units Loaded", f"{drop_qty:,.0f}")

        # Progress bar
        pct = touched_ct / total_full if total_full > 0 else 0
        st.progress(pct, text=f"{touched_ct}/{total_full} devices completed ({pct*100:.0f}%)")

        st.divider()

        # ── Full-drop devices ──────────────────────────────────────────────
        if not full_df.empty:
            st.markdown("#### Full Drop Devices")

            # Bar chart — qty per device (touched only)
            chart_df = full_df[full_df["items_loaded"] > 0].sort_values("items_loaded", ascending=False)
            if not chart_df.empty:
                fig = px.bar(
                    chart_df,
                    x="items_loaded", y="device",
                    orientation="h",
                    color="area",
                    color_discrete_map=AREA_COLOR,
                    labels={"items_loaded": "Med Transactions", "device": "", "area": "Area"},
                    title=f"{drop['label']} — Med Transactions per Device",
                    text="items_loaded",
                    hover_data=["total_qty", "techs"],
                )
                fig.update_traces(textposition="outside")
                fig.update_layout(
                    yaxis={"categoryorder": "total ascending"},
                    height=max(300, len(chart_df) * 26 + 80),
                    margin=dict(l=0, r=60, t=40, b=0),
                    legend=dict(orientation="h", yanchor="bottom", y=1.02),
                )
                st.plotly_chart(fig, use_container_width=True)

            # Area filter
            areas = sorted(full_df["area"].unique())
            sel_areas = st.multiselect("Filter by Area", areas, default=areas, key=f"area_{i}")
            view = full_df[full_df["area"].isin(sel_areas)] if sel_areas else full_df

            st.dataframe(
                view[["status","device","area","items_loaded","total_qty",
                      "techs","first_time","last_time","stockout_print"]],
                use_container_width=True,
                hide_index=True,
                column_config={
                    "status":        st.column_config.TextColumn("Status"),
                    "device":        st.column_config.TextColumn("Device"),
                    "area":          st.column_config.TextColumn("Area"),
                    "items_loaded":  st.column_config.NumberColumn("Med Txns",    format="%d"),
                    "total_qty":     st.column_config.NumberColumn("Total Units", format="%.0f"),
                    "techs":         st.column_config.TextColumn("Technician(s)"),
                    "first_time":    st.column_config.DatetimeColumn("First Load", format="HH:mm"),
                    "last_time":     st.column_config.DatetimeColumn("Last Load",  format="HH:mm"),
                    "stockout_print":st.column_config.TextColumn("Stockout Report"),
                }
            )
            st.download_button(
                f"⬇️ Export {drop['label']} to Excel",
                data=to_excel_bytes(view),
                file_name=f"carousel_{drop['label'].replace(' ','_').lower()}_{sel_date}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key=f"dl_{i}_full",
            )

        # ── Stockouts-only devices ──────────────────────────────────────────
        if not stock_df.empty:
            st.divider()
            st.markdown("#### Stockouts Only Devices")
            st.caption("These devices only receive a drop if they had a stockout. 🔵 = no stockout needed, ✅ = activated.")
            touched_stock = int((stock_df["status"] == "✅ Touched (Stockout)").sum())
            st.markdown(f"**{touched_stock}** of {len(stock_df)} stockout-only devices activated today.")
            st.dataframe(
                stock_df[["status","device","area","items_loaded","total_qty",
                          "techs","first_time","last_time","stockout_print"]],
                use_container_width=True,
                hide_index=True,
                column_config={
                    "status":        st.column_config.TextColumn("Status"),
                    "device":        st.column_config.TextColumn("Device"),
                    "area":          st.column_config.TextColumn("Area"),
                    "items_loaded":  st.column_config.NumberColumn("Med Txns",    format="%d"),
                    "total_qty":     st.column_config.NumberColumn("Total Units", format="%.0f"),
                    "techs":         st.column_config.TextColumn("Technician(s)"),
                    "first_time":    st.column_config.DatetimeColumn("First Load", format="HH:mm"),
                    "last_time":     st.column_config.DatetimeColumn("Last Load",  format="HH:mm"),
                    "stockout_print":st.column_config.TextColumn("Stockout Report"),
                }
            )


# ── Day Summary Tab ───────────────────────────────────────────────────────────
with tabs[len(schedule)]:
    st.subheader(f"Day Summary — {sel_date.strftime('%A %b %d, %Y')}")

    if combined.empty or df_loads.empty:
        st.info("No load events found for this date.")
    else:
        # Completion by drop
        drop_summary = []
        for drop in schedule:
            ddf = all_drop_dfs[drop["label"]]
            full = ddf[ddf["drop_type"] == "Full Drop"]
            touched = int((full["status"] == "✅ Touched").sum())
            total   = len(full)
            drop_summary.append({
                "Drop":       drop["label"],
                "Scheduled":  total,
                "Completed":  touched,
                "Missed":     total - touched,
                "Completion": round(touched / total * 100, 1) if total > 0 else 0.0,
                "Total Units": float(full["total_qty"].sum()),
            })
        ds = pd.DataFrame(drop_summary)

        fig_comp = px.bar(
            ds, x="Drop", y="Completion",
            color="Completion",
            color_continuous_scale=["#ef4444", "#facc15", "#22c55e"],
            range_color=[0, 100],
            labels={"Completion": "% Complete"},
            title="Drop Completion % by Time",
            text=ds["Completion"].astype(str) + "%",
        )
        fig_comp.update_traces(textposition="outside")
        fig_comp.update_layout(coloraxis_showscale=False, height=300)
        st.plotly_chart(fig_comp, use_container_width=True)

        st.dataframe(
            ds,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Completion":   st.column_config.NumberColumn("Completion %", format="%.1f%%"),
                "Total Units":  st.column_config.NumberColumn("Units Loaded", format="%.0f"),
            }
        )

        st.divider()

        # Hourly load activity
        st.subheader("Hourly Load Activity")
        df_loads["_hour"] = df_loads["dt"].dt.hour
        hourly = df_loads.groupby("_hour").agg(txns=("pk","count"), units=("qty","sum")).reset_index()
        fig_hr = px.bar(
            hourly, x="_hour", y="txns",
            labels={"_hour": "Hour of Day", "txns": "Med Transactions"},
            title="Transactions by Hour",
            color="txns", color_continuous_scale="Blues",
            text="txns",
        )
        fig_hr.update_traces(textposition="outside")
        fig_hr.update_layout(coloraxis_showscale=False, xaxis=dict(tickmode="linear", dtick=1))
        st.plotly_chart(fig_hr, use_container_width=True)

        st.divider()

        # Top technicians for the day
        st.subheader("Technician Load Totals")
        tech_day = (
            df_loads.groupby("user_name")
            .agg(txns=("pk","count"), units=("qty","sum"), devices=("device","nunique"))
            .reset_index().sort_values("txns", ascending=False)
        )
        st.dataframe(
            tech_day,
            use_container_width=True, hide_index=True,
            column_config={
                "txns":    st.column_config.NumberColumn("Transactions", format="%d"),
                "units":   st.column_config.NumberColumn("Units Loaded", format="%.0f"),
                "devices": st.column_config.NumberColumn("Devices",      format="%d"),
            }
        )

        # All devices ranked by units loaded
        st.divider()
        st.subheader("All Devices — Units Loaded Today")
        dev_day = (
            df_loads.groupby("device")
            .agg(txns=("pk","count"), units=("qty","sum"))
            .reset_index().sort_values("units", ascending=False)
        )
        dev_day["area"] = dev_day["device"].map(lambda d: DEVICE_AREA.get(d, "Other"))
        fig_dev = px.bar(
            dev_day, x="units", y="device",
            orientation="h",
            color="area",
            color_discrete_map=AREA_COLOR,
            labels={"units": "Units Loaded", "device": ""},
            title="Units Loaded per Device",
            text="units",
        )
        fig_dev.update_traces(texttemplate="%{text:.0f}", textposition="outside")
        fig_dev.update_layout(
            yaxis={"categoryorder": "total ascending"},
            height=max(400, len(dev_day) * 22 + 80),
            legend=dict(orientation="h", yanchor="bottom", y=1.02),
        )
        st.plotly_chart(fig_dev, use_container_width=True)

        st.download_button(
            "⬇️ Export Full Day Summary to Excel",
            data=to_excel_bytes(combined),
            file_name=f"carousel_day_{sel_date}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )


# ── Schedule Reference Tab ────────────────────────────────────────────────────
with tabs[len(schedule) + 1]:
    st.subheader("📅 Schedule Reference")
    is_weekend_ref = sel_date.weekday() >= 5
    st.caption("Sa-Su schedule" if is_weekend_ref else "M-F schedule (Wednesday adds SM/SMOR to 0700)")

    ref_rows = []
    for drop in schedule:
        for dev in drop["full"]:
            ref_rows.append({"drop": drop["label"], "device": dev, "type": "Full Drop",
                             "area": DEVICE_AREA.get(dev, "Other")})
        for dev in drop.get("stockouts", []):
            ref_rows.append({"drop": drop["label"], "device": dev, "type": "Stockouts Only",
                             "area": DEVICE_AREA.get(dev, "Other")})
        if drop.get("wed_note") and not is_weekend_ref:
            for dev in _MF_0700_WED:
                ref_rows.append({"drop": drop["label"], "device": dev, "type": "Wed Only",
                                 "area": DEVICE_AREA.get(dev, "Other")})

    ref_df = pd.DataFrame(ref_rows)

    drop_filter = st.multiselect("Drop", options=drop_labels, default=drop_labels, key="ref_drop")
    area_filter = st.multiselect("Area", options=sorted(ref_df["area"].unique()),
                                 default=sorted(ref_df["area"].unique()), key="ref_area")
    ref_view = ref_df[ref_df["drop"].isin(drop_filter) & ref_df["area"].isin(area_filter)]

    st.dataframe(ref_view, use_container_width=True, hide_index=True,
                 column_config={
                     "drop":   st.column_config.TextColumn("Drop Time"),
                     "device": st.column_config.TextColumn("Device"),
                     "type":   st.column_config.TextColumn("Drop Type"),
                     "area":   st.column_config.TextColumn("Area"),
                 })
    st.caption(f"{len(ref_view):,} devices shown.")


# ── Stockout Print Config Tab ─────────────────────────────────────────────────
with tabs[len(schedule) + 2]:
    st.subheader("🖨️ Stockout Report Print Configuration")
    col_a, col_b = st.columns(2)
    with col_a:
        st.markdown("#### ✅ Prints Automatically")
        auto_df = pd.DataFrame([
            {"device": d, "area": DEVICE_AREA.get(d, "Other")}
            for d in sorted(STOCKOUTS_PRINT_AUTO)
        ])
        st.dataframe(auto_df, use_container_width=True, hide_index=True)
    with col_b:
        st.markdown("#### ❌ Does NOT Print")
        all_scheduled = set()
        for d in schedule:
            all_scheduled.update(d["full"])
            all_scheduled.update(d.get("stockouts", []))
        no_print = sorted(all_scheduled - STOCKOUTS_PRINT_AUTO_SET)
        no_df = pd.DataFrame([
            {"device": d, "area": DEVICE_AREA.get(d, "Other")}
            for d in no_print
        ])
        st.dataframe(no_df, use_container_width=True, hide_index=True)
