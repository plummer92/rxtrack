import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
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


@st.cache_data(ttl=300)
def load_pyxis_pulls(sel_date):
    """Pyxis Pull lines from pharmacy_orders — represent carousel pull demand."""
    try:
        sql = text("""
            SELECT pk, dt, user_name, destination, med_id, med_desc, priority, qty
            FROM pharmacy_orders
            WHERE dt::date = :d
              AND priority ILIKE '%pyxis%pull%'
        """)
        with engine.connect() as conn:
            df = pd.read_sql(sql, conn, params={"d": str(sel_date)})
        df["dt"]          = pd.to_datetime(df["dt"], errors="coerce")
        df["destination"] = df["destination"].fillna("Unknown").astype(str).str.strip()
        df["user_name"]   = df["user_name"].fillna("Unknown").astype(str).str.strip()
        df["qty"]         = pd.to_numeric(df["qty"], errors="coerce").fillna(0)
        return df
    except Exception as e:
        st.error(f"[load_pyxis_pulls] {e}")
        return pd.DataFrame()


@st.cache_data(ttl=300)
def load_all_events_for_date(sel_date):
    """Load every event for a date (no event_type filter) for diagnostics."""
    try:
        sql = text("""
            SELECT pk, dt, user_name, device, med_id, med_desc, event_type, qty
            FROM events
            WHERE dt::date = :d
        """)
        with engine.connect() as conn:
            df = pd.read_sql(sql, conn, params={"d": sel_date})
        df["dt"] = pd.to_datetime(df["dt"], errors="coerce")
        return df
    except Exception as e:
        st.error(f"[load_all_events_for_date] {e}")
        return pd.DataFrame()


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

with st.spinner("Loading replenishment and pull data..."):
    df_loads = load_replenishment(sel_date)
    df_pulls = load_pyxis_pulls(sel_date)

schedule = get_schedule(sel_date)


# ═══════════════════════════════════════════════════════════════════════════════
# HELPER — BUILD DROP TABLE
# ═══════════════════════════════════════════════════════════════════════════════

def build_drop_df(drop, df_loads, df_pulls):
    """Returns a DataFrame of all scheduled devices for a drop with actual qty/events and pull demand."""
    h0, m0 = drop["win_start"]
    h1, m1 = drop["win_end"]
    start_min = h0 * 60 + m0
    end_min   = h1 * 60 + m1

    # ── Pyxis load events in window ──────────────────────────────────────────
    if not df_loads.empty:
        ev_min = df_loads["dt"].dt.hour * 60 + df_loads["dt"].dt.minute
        window = df_loads[(ev_min >= start_min) & (ev_min <= end_min)]
    else:
        window = pd.DataFrame()

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

    # ── Pharmacy Pyxis Pull lines in window ──────────────────────────────────
    if not df_pulls.empty:
        pu_min = df_pulls["dt"].dt.hour * 60 + df_pulls["dt"].dt.minute
        pull_win = df_pulls[(pu_min >= start_min) & (pu_min <= end_min)]
    else:
        pull_win = pd.DataFrame()

    if not pull_win.empty:
        pull_agg = (
            pull_win.groupby("destination")
            .agg(
                pull_lines = ("pk",  "count"),
                pull_qty   = ("qty", "sum"),
            )
            .reset_index()
            .rename(columns={"destination": "device"})
        )
    else:
        pull_agg = pd.DataFrame(columns=["device","pull_lines","pull_qty"])

    def _loop_status(pull_qty, items_loaded, loaded_qty):
        if pull_qty == 0 and items_loaded == 0:
            return "⬜ No Activity"
        if pull_qty == 0 and items_loaded > 0:
            return "⚠️ Loaded / No Pull Record"
        if pull_qty > 0 and items_loaded == 0:
            return "❌ Not Refilled"
        coverage = loaded_qty / pull_qty if pull_qty > 0 else 0
        return "✅ Closed Loop" if coverage >= 0.90 else "⚠️ Partial"

    rows = []
    for dev in drop["full"]:
        m = agg[agg["device"] == dev]
        p = pull_agg[pull_agg["device"] == dev]
        pull_lines = int(p["pull_lines"].iloc[0]) if not p.empty else 0
        pull_qty   = float(p["pull_qty"].iloc[0])  if not p.empty else 0.0
        items_loaded = int(m["items_loaded"].iloc[0])   if not m.empty else 0
        loaded_qty   = float(m["total_qty"].iloc[0])    if not m.empty else 0.0
        row = {
            "device":         dev,
            "area":           DEVICE_AREA.get(dev, "Other"),
            "drop_type":      "Full Drop",
            "stockout_print": "✅ Auto-Print" if dev in STOCKOUTS_PRINT_AUTO_SET else "❌ No Print",
            "pull_lines":     pull_lines,
            "pull_qty":       pull_qty,
            "items_loaded":   items_loaded,
            "total_qty":      loaded_qty,
            "qty_coverage":   round(loaded_qty / pull_qty * 100, 1) if pull_qty > 0 else None,
            "loop_status":    _loop_status(pull_qty, items_loaded, loaded_qty),
            "techs":          m["techs"].iloc[0]     if not m.empty else "",
            "first_time":     m["first_time"].iloc[0] if not m.empty else None,
            "last_time":      m["last_time"].iloc[0]  if not m.empty else None,
            "status":         "✅ Touched" if not m.empty else "❌ Missed",
        }
        rows.append(row)

    for dev in drop["stockouts"]:
        m = agg[agg["device"] == dev]
        p = pull_agg[pull_agg["device"] == dev]
        pull_lines = int(p["pull_lines"].iloc[0]) if not p.empty else 0
        pull_qty   = float(p["pull_qty"].iloc[0])  if not p.empty else 0.0
        items_loaded = int(m["items_loaded"].iloc[0])   if not m.empty else 0
        loaded_qty   = float(m["total_qty"].iloc[0])    if not m.empty else 0.0
        row = {
            "device":         dev,
            "area":           DEVICE_AREA.get(dev, "Other"),
            "drop_type":      "Stockouts Only",
            "stockout_print": "✅ Auto-Print" if dev in STOCKOUTS_PRINT_AUTO_SET else "❌ No Print",
            "pull_lines":     pull_lines,
            "pull_qty":       pull_qty,
            "items_loaded":   items_loaded,
            "total_qty":      loaded_qty,
            "qty_coverage":   round(loaded_qty / pull_qty * 100, 1) if pull_qty > 0 else None,
            "loop_status":    _loop_status(pull_qty, items_loaded, loaded_qty),
            "techs":          m["techs"].iloc[0]     if not m.empty else "",
            "first_time":     m["first_time"].iloc[0] if not m.empty else None,
            "last_time":      m["last_time"].iloc[0]  if not m.empty else None,
            "status":         "✅ Touched (Stockout)" if not m.empty else "🔵 No Stockout",
        }
        rows.append(row)

    return pd.DataFrame(rows)


# ═══════════════════════════════════════════════════════════════════════════════
# TOP-LINE KPIs (across all drops for the day)
# ═══════════════════════════════════════════════════════════════════════════════

all_drop_dfs = {d["label"]: build_drop_df(d, df_loads, df_pulls) for d in schedule}
combined = pd.concat(all_drop_dfs.values(), ignore_index=True) if all_drop_dfs else pd.DataFrame()

full_combined    = combined[combined["drop_type"] == "Full Drop"] if not combined.empty else pd.DataFrame()
total_scheduled  = len(full_combined)
total_pull_lines = int(df_pulls["pk"].count())      if not df_pulls.empty else 0
total_pull_qty   = float(df_pulls["qty"].sum())     if not df_pulls.empty else 0.0
total_load_qty   = float(df_loads["qty"].sum())     if not df_loads.empty else 0.0
total_load_lines = int(df_loads["pk"].count())      if not df_loads.empty else 0
day_coverage_pct = round(total_load_qty / total_pull_qty * 100, 1) if total_pull_qty > 0 else 0.0

closed_ct  = int((full_combined["loop_status"] == "✅ Closed Loop").sum())      if not full_combined.empty else 0
partial_ct = int((full_combined["loop_status"] == "⚠️ Partial").sum())          if not full_combined.empty else 0
no_fill_ct = int((full_combined["loop_status"] == "❌ Not Refilled").sum())      if not full_combined.empty else 0

# ── Primary pull metrics ──────────────────────────────────────────────────────
st.markdown("##### 🛒 Pull Demand (Pharmacy Workflow)")
p1, p2, p3, p4 = st.columns(4)
p1.metric("Pull Lines",   f"{total_pull_lines:,}",  help="Line items pulled from carousel (pharmacy_orders)")
p2.metric("Pull Qty",     f"{total_pull_qty:,.0f}", help="Total units pulled from carousel — primary workload metric")
p3.metric("Loaded Qty",   f"{total_load_qty:,.0f}", help="Total units loaded into Pyxis machines (refill/load events)")
p4.metric("Qty Coverage", f"{day_coverage_pct:.1f}%",
          delta=f"{day_coverage_pct - 100:.1f}%" if total_pull_qty > 0 else None,
          help="Loaded ÷ Pulled × 100 — 100% = fully closed loop")

st.markdown("##### 🔄 Loop Closure")
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Devices Scheduled",  total_scheduled)
c2.metric("✅ Closed Loop",     closed_ct)
c3.metric("⚠️ Partial",        partial_ct)
c4.metric("❌ Not Refilled",    no_fill_ct)
c5.metric("Load Lines",         f"{total_load_lines:,}", help="Pyxis refill/load transaction count")

if day_coverage_pct >= 100:
    st.success(f"✅ Full loop closed — {total_pull_qty:,.0f} units pulled, {total_load_qty:,.0f} loaded ({day_coverage_pct:.1f}% coverage).")
elif total_pull_qty == 0:
    st.info("No Pyxis Pull records found for this date. Check pharmacy workflow data.")
else:
    gap = total_pull_qty - total_load_qty
    st.warning(f"⚠️ Loop not fully closed — {gap:,.0f} units pulled but not yet loaded ({day_coverage_pct:.1f}% coverage).")

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

        total_full    = len(full_df)
        drop_pull_qty = float(full_df["pull_qty"].sum())   if not full_df.empty else 0.0
        drop_pull_lines = int(full_df["pull_lines"].sum()) if not full_df.empty else 0
        drop_load_qty = float(full_df["total_qty"].sum())  if not full_df.empty else 0.0
        drop_load_lines = int(full_df["items_loaded"].sum()) if not full_df.empty else 0
        drop_coverage = round(drop_load_qty / drop_pull_qty * 100, 1) if drop_pull_qty > 0 else 0.0
        drop_closed   = int((full_df["loop_status"] == "✅ Closed Loop").sum())   if not full_df.empty else 0
        drop_partial  = int((full_df["loop_status"] == "⚠️ Partial").sum())       if not full_df.empty else 0
        drop_nofill   = int((full_df["loop_status"] == "❌ Not Refilled").sum())   if not full_df.empty else 0

        st.subheader(f"{drop['label']} — Scheduled {drop['time']}")
        if drop.get("wed_note"):
            st.info("📅 Wednesday: SM/SMOR devices included in this drop.")

        st.markdown("**🛒 Pull Demand**")
        p1, p2, p3, p4 = st.columns(4)
        p1.metric("Pull Qty",     f"{drop_pull_qty:,.0f}", help="Total units pulled from carousel for this drop")
        p2.metric("Pull Lines",   f"{drop_pull_lines:,}",  help="Line items in pharmacy pull")
        p3.metric("Loaded Qty",   f"{drop_load_qty:,.0f}", help="Units actually loaded into Pyxis machines")
        p4.metric("Qty Coverage", f"{drop_coverage:.1f}%",
                  delta=f"{drop_coverage - 100:.1f}%" if drop_pull_qty > 0 else None)

        st.markdown("**🔄 Loop Closure**")
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Devices",          total_full)
        c2.metric("✅ Closed Loop",   drop_closed)
        c3.metric("⚠️ Partial",      drop_partial)
        c4.metric("❌ Not Refilled",  drop_nofill)
        c5.metric("Load Transactions", f"{drop_load_lines:,}")

        if drop_pull_qty > 0:
            cov_bar = min(drop_coverage / 100, 1.0)
            st.progress(cov_bar, text=f"Qty coverage: {drop_coverage:.1f}% ({drop_load_qty:,.0f} / {drop_pull_qty:,.0f} units)")

        st.divider()

        # ── Full-drop devices — sorted by pull_qty desc ────────────────────
        if not full_df.empty:
            st.markdown("#### Full Drop Devices — Ranked by Pull Size")

            # Dual bar: pull_qty and total_qty side by side
            chart_df = full_df[full_df["pull_qty"] > 0].sort_values("pull_qty", ascending=False)
            if chart_df.empty:
                chart_df = full_df[full_df["total_qty"] > 0].sort_values("total_qty", ascending=False)

            if not chart_df.empty:
                fig = go.Figure()
                fig.add_trace(go.Bar(
                    y=chart_df["device"],
                    x=chart_df["pull_qty"],
                    name="🛒 Pull Qty (Demand)",
                    orientation="h",
                    marker_color="#1f77b4",
                    text=chart_df["pull_qty"].map(lambda v: f"{v:.0f}"),
                    textposition="outside",
                ))
                fig.add_trace(go.Bar(
                    y=chart_df["device"],
                    x=chart_df["total_qty"],
                    name="📦 Loaded Qty (Actual)",
                    orientation="h",
                    marker_color="#2ca02c",
                    text=chart_df["total_qty"].map(lambda v: f"{v:.0f}"),
                    textposition="outside",
                ))
                fig.update_layout(
                    barmode="group",
                    yaxis={"categoryorder": "total ascending"},
                    height=max(320, len(chart_df) * 32 + 80),
                    margin=dict(l=0, r=80, t=40, b=0),
                    legend=dict(orientation="h", yanchor="bottom", y=1.02),
                    title=f"{drop['label']} — Pull Demand vs Loaded (Units)",
                    xaxis_title="Units",
                )
                st.plotly_chart(fig, use_container_width=True)

            # Table — sorted by pull_qty desc, loop_status first column
            areas = sorted(full_df["area"].unique())
            sel_areas = st.multiselect("Filter by Area", areas, default=areas, key=f"area_{i}")
            view = full_df[full_df["area"].isin(sel_areas)] if sel_areas else full_df
            view = view.sort_values("pull_qty", ascending=False)

            st.dataframe(
                view[["loop_status","device","area",
                      "pull_qty","pull_lines","total_qty","items_loaded","qty_coverage",
                      "techs","first_time","last_time","stockout_print"]],
                use_container_width=True,
                hide_index=True,
                column_config={
                    "loop_status":   st.column_config.TextColumn("Loop Status",    width="medium"),
                    "device":        st.column_config.TextColumn("Device"),
                    "area":          st.column_config.TextColumn("Area"),
                    "pull_qty":      st.column_config.NumberColumn("🛒 Pull Qty",  format="%.0f",
                                         help="Units pulled from carousel (pharmacy workflow)"),
                    "pull_lines":    st.column_config.NumberColumn("Pull Lines",   format="%d"),
                    "total_qty":     st.column_config.NumberColumn("📦 Loaded Qty",format="%.0f",
                                         help="Units loaded into Pyxis (refill/load events)"),
                    "items_loaded":  st.column_config.NumberColumn("Load Lines",   format="%d"),
                    "qty_coverage":  st.column_config.NumberColumn("Coverage %",   format="%.1f",
                                         help="Loaded ÷ Pulled × 100"),
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

        # ── Device drill-down ──────────────────────────────────────────────
        st.divider()
        st.markdown("#### 🔬 Device Drill-Down")
        all_devices_in_drop = sorted(drop_df["device"].unique().tolist())
        drill_dev = st.selectbox(
            "Select a device to see individual line items",
            options=["— pick a device —"] + all_devices_in_drop,
            key=f"drill_{i}",
        )

        if drill_dev != "— pick a device —":
            # Time window for this drop
            h0, m0 = drop["win_start"]
            h1, m1 = drop["win_end"]
            start_min = h0 * 60 + m0
            end_min   = h1 * 60 + m1

            col_pull, col_load = st.columns(2)

            # ── Pharmacy Pull Lines ──────────────────────────────────────
            with col_pull:
                st.markdown(f"**🛒 Pharmacy Pull Lines — {drill_dev}**")
                st.caption("From pharmacy_orders (Pyxis Pull priority) — what was pulled from the carousel")
                if not df_pulls.empty:
                    pu_min  = df_pulls["dt"].dt.hour * 60 + df_pulls["dt"].dt.minute
                    pull_detail = df_pulls[
                        (df_pulls["destination"] == drill_dev) &
                        (pu_min >= start_min) & (pu_min <= end_min)
                    ][["dt","user_name","med_id","med_desc","qty","priority"]].sort_values("dt").reset_index(drop=True)
                else:
                    pull_detail = pd.DataFrame()

                if pull_detail.empty:
                    st.info("No pharmacy pull lines for this device in this window.")
                else:
                    st.caption(f"{len(pull_detail):,} lines · {pull_detail['qty'].sum():,.0f} units")
                    st.dataframe(
                        pull_detail,
                        use_container_width=True,
                        hide_index=True,
                        column_config={
                            "dt":        st.column_config.DatetimeColumn("Time",       format="HH:mm:ss"),
                            "user_name": st.column_config.TextColumn("User"),
                            "med_id":    st.column_config.TextColumn("Med ID"),
                            "med_desc":  st.column_config.TextColumn("Medication"),
                            "qty":       st.column_config.NumberColumn("Qty",          format="%.0f"),
                            "priority":  st.column_config.TextColumn("Priority"),
                        }
                    )

            # ── Pyxis Load Events ────────────────────────────────────────
            with col_load:
                st.markdown(f"**📦 Pyxis Load Events — {drill_dev}**")
                st.caption("From events table (refill/load) — what was actually loaded into the machine")
                if not df_loads.empty:
                    ev_min      = df_loads["dt"].dt.hour * 60 + df_loads["dt"].dt.minute
                    load_detail = df_loads[
                        (df_loads["device"] == drill_dev) &
                        (ev_min >= start_min) & (ev_min <= end_min)
                    ][["dt","user_name","med_id","med_desc","event_type","qty","beginning_qty","ending_qty"]].sort_values("dt").reset_index(drop=True)
                else:
                    load_detail = pd.DataFrame()

                if load_detail.empty:
                    st.info("No Pyxis load events for this device in this window.")
                else:
                    st.caption(f"{len(load_detail):,} transactions · {load_detail['qty'].sum():,.0f} units")
                    st.dataframe(
                        load_detail,
                        use_container_width=True,
                        hide_index=True,
                        column_config={
                            "dt":            st.column_config.DatetimeColumn("Time",       format="HH:mm:ss"),
                            "user_name":     st.column_config.TextColumn("Tech"),
                            "med_id":        st.column_config.TextColumn("Med ID"),
                            "med_desc":      st.column_config.TextColumn("Medication"),
                            "event_type":    st.column_config.TextColumn("Event"),
                            "qty":           st.column_config.NumberColumn("Qty",          format="%.0f"),
                            "beginning_qty": st.column_config.NumberColumn("Before",       format="%.0f"),
                            "ending_qty":    st.column_config.NumberColumn("After",        format="%.0f"),
                        }
                    )


# ── Day Summary Tab ───────────────────────────────────────────────────────────
with tabs[len(schedule)]:
    st.subheader(f"Day Summary — {sel_date.strftime('%A %b %d, %Y')}")

    if combined.empty:
        st.info("No data found for this date.")
    else:
        # Completion by drop — pull-first
        drop_summary = []
        for drop in schedule:
            ddf = all_drop_dfs[drop["label"]]
            full = ddf[ddf["drop_type"] == "Full Drop"]
            pull_qty   = float(full["pull_qty"].sum())
            loaded_qty = float(full["total_qty"].sum())
            closed     = int((full["loop_status"] == "✅ Closed Loop").sum())
            total      = len(full)
            drop_summary.append({
                "Drop":         drop["label"],
                "Pull Qty":     pull_qty,
                "Loaded Qty":   loaded_qty,
                "Coverage %":   round(loaded_qty / pull_qty * 100, 1) if pull_qty > 0 else 0.0,
                "Pull Lines":   int(full["pull_lines"].sum()),
                "✅ Closed":    closed,
                "Devices":      total,
            })
        ds = pd.DataFrame(drop_summary)

        fig_comp = px.bar(
            ds, x="Drop", y="Coverage %",
            color="Coverage %",
            color_continuous_scale=["#ef4444", "#facc15", "#22c55e"],
            range_color=[0, 100],
            labels={"Coverage %": "Qty Coverage %"},
            title="Loop Closure Coverage % by Drop (Loaded ÷ Pulled)",
            text=ds["Coverage %"].astype(str) + "%",
        )
        fig_comp.update_traces(textposition="outside")
        fig_comp.update_layout(coloraxis_showscale=False, height=300)
        st.plotly_chart(fig_comp, use_container_width=True)

        st.dataframe(
            ds,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Pull Qty":    st.column_config.NumberColumn("🛒 Pull Qty",   format="%.0f"),
                "Loaded Qty":  st.column_config.NumberColumn("📦 Loaded Qty", format="%.0f"),
                "Coverage %":  st.column_config.NumberColumn("Coverage %",    format="%.1f"),
                "Pull Lines":  st.column_config.NumberColumn("Pull Lines",    format="%d"),
                "✅ Closed":   st.column_config.NumberColumn("✅ Closed Loop",format="%d"),
                "Devices":     st.column_config.NumberColumn("Devices",       format="%d"),
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


# ═══════════════════════════════════════════════════════════════════════════════
# DIAGNOSTIC — Event Type Verification
# ═══════════════════════════════════════════════════════════════════════════════

st.divider()

with st.expander("🛠️ Diagnostic: Verify Event Types Being Captured", expanded=False):
    st.markdown(
        "Use this to confirm the correct Pyxis transaction types are being counted. "
        "The tracker currently includes events matching **refill / load / restock / replenish** "
        "(excluding unload / cancel / empty). If your Pyxis uses a different label, the filters "
        "can be updated to match."
    )

    df_all = load_all_events_for_date(sel_date)

    # Build the set of all scheduled devices for this day
    all_sched_devices = set()
    for drop in schedule:
        all_sched_devices.update(drop.get("full", []))
        all_sched_devices.update(drop.get("stockouts", []))

    if df_all.empty:
        st.info("No events found for this date.")
    else:
        col_d1, col_d2 = st.columns(2)

        with col_d1:
            st.markdown("#### All event types on this date")
            etype_all = (
                df_all.groupby("event_type")
                .agg(count=("pk", "count"))
                .reset_index()
                .sort_values("count", ascending=False)
            )
            # Flag which ones are currently captured by our filter
            _capture = r"refill|load|restock|replenish"
            _exclude = r"unload|cancel|empty"
            etype_all["captured?"] = etype_all["event_type"].apply(
                lambda e: "✅ Yes" if (
                    pd.Series([e]).str.contains(_capture, case=False, na=False).iloc[0]
                    and not pd.Series([e]).str.contains(_exclude, case=False, na=False).iloc[0]
                ) else "—"
            )
            st.dataframe(etype_all, use_container_width=True, hide_index=True)

        with col_d2:
            st.markdown("#### Event types on **scheduled devices** only")
            df_sched_only = df_all[df_all["device"].isin(all_sched_devices)]
            if df_sched_only.empty:
                st.info("No events on scheduled devices for this date.")
            else:
                etype_sched = (
                    df_sched_only.groupby("event_type")
                    .agg(count=("pk", "count"))
                    .reset_index()
                    .sort_values("count", ascending=False)
                )
                etype_sched["captured?"] = etype_sched["event_type"].apply(
                    lambda e: "✅ Yes" if (
                        pd.Series([e]).str.contains(_capture, case=False, na=False).iloc[0]
                        and not pd.Series([e]).str.contains(_exclude, case=False, na=False).iloc[0]
                    ) else "—"
                )
                st.dataframe(etype_sched, use_container_width=True, hide_index=True)

        st.markdown(
            f"**Load/Refill events captured:** {len(df_loads):,} transactions · "
            f"{df_loads['qty'].sum():,.0f} units · "
            f"{df_loads['device'].nunique()} devices  \n"
            f"**Total events on date (all types):** {len(df_all):,}"
        )

        st.divider()
        st.markdown("#### Pharmacy Pull Priorities on this date")
        st.caption("Verifies 'Pyxis Pull' priority is present and being captured from pharmacy_orders.")
        if df_pulls.empty:
            st.warning("No Pyxis Pull records found in pharmacy_orders for this date. "
                       "Check that pharmacy workflow data has been uploaded and the priority "
                       "value matches 'Pyxis Pull' (case-insensitive).")
        else:
            # Show distinct priority values in pharmacy_orders for this date
            sql_pri = text("""
                SELECT priority, COUNT(*) AS count
                FROM pharmacy_orders
                WHERE dt::date = :d
                GROUP BY priority ORDER BY count DESC
            """)
            try:
                with engine.connect() as _conn:
                    pri_df = pd.read_sql(sql_pri, _conn, params={"d": str(sel_date)})
                pri_df["captured?"] = pri_df["priority"].apply(
                    lambda p: "✅ Yes" if pd.Series([p]).str.contains(
                        r"pyxis.*pull|pull.*pyxis", case=False, na=False, regex=True
                    ).iloc[0] else "—"
                )
                st.dataframe(pri_df, use_container_width=True, hide_index=True)
            except Exception as _e:
                st.error(str(_e))
            st.markdown(
                f"**Pyxis Pull lines captured:** {len(df_pulls):,} lines across "
                f"{df_pulls['destination'].nunique()} devices"
            )
