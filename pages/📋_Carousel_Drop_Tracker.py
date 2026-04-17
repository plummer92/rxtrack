import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
import io
from datetime import date, timedelta
from sqlalchemy import text
import App

st.set_page_config(page_title="Carousel Drop Tracker", page_icon="📋", layout="wide")
if hasattr(App, "render_sidebar_chrome"):
    App.render_sidebar_chrome()
else:
    App.render_sidebar()

engine = App.engine

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
                "refill_win_end": (19, 30),  # 11:30 + 8h
                "full": _SASU_0400_FULL,
                "stockouts": [],
            },
            {
                "label": "1235 Drop",
                "time": "12:35",
                "win_start": (11, 31),
                "win_end": (20, 0),
                "refill_win_end": (23, 59),  # 20:00 + 8h capped
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
                "refill_win_end": (14, 29),  # 6:29 + 8h
                "full": _MF_0400_FULL,
                "stockouts": [],
            },
            {
                "label": "0700 Drop",
                "time": "07:00",
                "win_start": (6, 30),
                "win_end": (11, 0),
                "refill_win_end": (19, 0),   # 11:00 + 8h
                "full": mf_0700,
                "stockouts": [],
                "wed_note": is_wednesday,
            },
            {
                "label": "1235 Drop",
                "time": "12:35",
                "win_start": (10, 30),
                "win_end": (14, 14),
                "refill_win_end": (22, 14),  # 14:14 + 8h
                "full": _MF_1235_FULL,
                "stockouts": _MF_1235_STOCK,
            },
            {
                "label": "1430 Drop",
                "time": "14:30",
                "win_start": (14, 0),
                "win_end": (20, 0),
                "refill_win_end": (23, 59),  # 20:00 + 8h capped
                "full": _MF_1430_FULL,
                "stockouts": [],
            },
        ]


# ═══════════════════════════════════════════════════════════════════════════════
# DATA LOADER
# ═══════════════════════════════════════════════════════════════════════════════

@st.cache_data(ttl=300)
def load_refills(sel_date):
    """Pyxis Refill events for the selected date (event_type = 'Refill' exact)."""
    try:
        sql = text("""
            SELECT pk, dt, user_name, device, med_id, med_desc, event_type,
                   qty, beginning_qty, ending_qty
            FROM events
            WHERE dt::date = :d
              AND event_type = 'Refill'
              AND UPPER(med_id) != 'PATCAS'
        """)
        with engine.connect() as conn:
            df = pd.read_sql(sql, conn, params={"d": str(sel_date)})
        df["dt"]        = pd.to_datetime(df["dt"], errors="coerce")
        df["device"]    = df["device"].fillna("Unknown").astype(str).str.strip()
        df["user_name"] = df["user_name"].fillna("Unknown").astype(str).str.strip()
        df["med_id"]    = df["med_id"].fillna("").astype(str).str.strip()
        df["qty"]       = pd.to_numeric(df["qty"], errors="coerce").fillna(0)
        return df
    except Exception as e:
        st.error(f"[load_refills] {e}")
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

with st.spinner("Loading pharmacy pulls and Pyxis refills..."):
    df_refills = load_refills(sel_date)
    df_pulls   = load_pyxis_pulls(sel_date)

schedule = get_schedule(sel_date)


# ═══════════════════════════════════════════════════════════════════════════════
# HELPER — BUILD DROP TABLE
# ═══════════════════════════════════════════════════════════════════════════════

def _med_status(pull_qty, refill_qty):
    if pull_qty > 0 and refill_qty == 0:   return "❌ Pulled Not Refilled"
    if pull_qty == 0 and refill_qty > 0:   return "🔵 Refilled Not Pulled"
    if pull_qty == refill_qty:             return "✅ Matched Exact"
    return "⚠️ Qty Mismatch"

def _device_recon_status(matched, mismatch, missing, extra, pull_lines, refill_lines):
    if pull_lines == 0 and refill_lines == 0: return "⬜ No Activity"
    if pull_lines == 0:                       return "🔵 Refills Only"
    if refill_lines == 0:                     return "❌ No Refills"
    if missing == 0 and mismatch == 0 and extra == 0: return "✅ Full Match"
    if missing > 0:  return "❌ Missing Refills"
    if mismatch > 0: return "⚠️ Qty Mismatch"
    return "🔵 Extra Refills"

def _reconcile_device(dev_pulls, dev_refills):
    """Merge pull and refill data by med_id. Returns (summary_dict, detail_df)."""
    if not dev_pulls.empty:
        pb = dev_pulls.groupby("med_id").agg(
            pull_qty=("qty","sum"), pull_lines=("pk","count"),
            med_desc=("med_desc","first")
        ).reset_index()
    else:
        pb = pd.DataFrame(columns=["med_id","pull_qty","pull_lines","med_desc"])

    if not dev_refills.empty:
        rb = dev_refills.groupby("med_id").agg(
            refill_qty=("qty","sum"), refill_lines=("pk","count"),
            med_desc=("med_desc","first")
        ).reset_index()
    else:
        rb = pd.DataFrame(columns=["med_id","refill_qty","refill_lines","med_desc"])

    mg = pd.merge(pb, rb, on="med_id", how="outer", suffixes=("_p","_r"))
    mg["med_desc"]     = mg["med_desc_p"].fillna(mg["med_desc_r"]).fillna("")
    mg["pull_qty"]     = mg["pull_qty"].fillna(0)
    mg["pull_lines"]   = mg["pull_lines"].fillna(0).astype(int)
    mg["refill_qty"]   = mg["refill_qty"].fillna(0)
    mg["refill_lines"] = mg["refill_lines"].fillna(0).astype(int)
    mg["qty_diff"]     = mg["refill_qty"] - mg["pull_qty"]
    mg["med_status"]   = mg.apply(lambda r: _med_status(r["pull_qty"], r["refill_qty"]), axis=1)
    mg = mg.drop(columns=["med_desc_p","med_desc_r"], errors="ignore")

    matched  = int((mg["med_status"] == "✅ Matched Exact").sum())
    mismatch = int((mg["med_status"] == "⚠️ Qty Mismatch").sum())
    missing  = int((mg["med_status"] == "❌ Pulled Not Refilled").sum())
    extra    = int((mg["med_status"] == "🔵 Refilled Not Pulled").sum())

    summary = {
        "pull_lines":    int(dev_pulls["pk"].count()) if not dev_pulls.empty else 0,
        "pull_qty":      float(dev_pulls["qty"].sum()) if not dev_pulls.empty else 0.0,
        "refill_lines":  int(dev_refills["pk"].count()) if not dev_refills.empty else 0,
        "refill_qty":    float(dev_refills["qty"].sum()) if not dev_refills.empty else 0.0,
        "matched_lines":  matched,
        "mismatch_lines": mismatch,
        "missing_lines":  missing,
        "extra_lines":    extra,
    }
    summary["recon_status"] = _device_recon_status(
        matched, mismatch, missing, extra,
        summary["pull_lines"], summary["refill_lines"]
    )
    return summary, mg


def build_recon_df(drop, df_pulls, df_refills):
    """Per-device reconciliation summary for a drop window.

    Pull window  = win_start → win_end  (tight: when carousel is being worked)
    Refill window = win_start → refill_win_end  (wider: techs walk to floors and
                    refill Pyxis machines after the carousel pull, often hours later)
    """
    h0, m0 = drop["win_start"]
    h1, m1 = drop["win_end"]
    rh1, rm1 = drop.get("refill_win_end", drop["win_end"])  # fallback to win_end if not set
    start_min      = h0 * 60 + m0
    end_min        = h1 * 60 + m1
    refill_end_min = rh1 * 60 + rm1

    def _pull_win(df):
        if df.empty: return pd.DataFrame()
        m = df["dt"].dt.hour * 60 + df["dt"].dt.minute
        return df[(m >= start_min) & (m <= end_min)]

    def _refill_win(df):
        if df.empty: return pd.DataFrame()
        m = df["dt"].dt.hour * 60 + df["dt"].dt.minute
        return df[(m >= start_min) & (m <= refill_end_min)]

    pull_win   = _pull_win(df_pulls)
    refill_win = _refill_win(df_refills)

    rows = []
    for dev, drop_type in (
        [(d, "Full Drop")      for d in drop["full"]] +
        [(d, "Stockouts Only") for d in drop.get("stockouts", [])]
    ):
        dev_pulls   = pull_win[pull_win["destination"] == dev]   if not pull_win.empty   else pd.DataFrame()
        dev_refills = refill_win[refill_win["device"]  == dev]   if not refill_win.empty else pd.DataFrame()

        summary, _ = _reconcile_device(dev_pulls, dev_refills)
        rows.append({
            "device":         dev,
            "area":           DEVICE_AREA.get(dev, "Other"),
            "drop_type":      drop_type,
            "stockout_print": "✅ Auto-Print" if dev in STOCKOUTS_PRINT_AUTO_SET else "❌ No Print",
            **summary,
        })

    return pd.DataFrame(rows)


# ═══════════════════════════════════════════════════════════════════════════════
# TOP-LINE KPIs (across all drops for the day)
# ═══════════════════════════════════════════════════════════════════════════════

all_drop_dfs = {d["label"]: build_recon_df(d, df_pulls, df_refills) for d in schedule}
combined = pd.concat(all_drop_dfs.values(), ignore_index=True) if all_drop_dfs else pd.DataFrame()

full_combined    = combined[combined["drop_type"] == "Full Drop"] if not combined.empty else pd.DataFrame()
total_scheduled  = len(full_combined)
total_pull_lines   = int(df_pulls["pk"].count())    if not df_pulls.empty   else 0
total_pull_qty     = float(df_pulls["qty"].sum())   if not df_pulls.empty   else 0.0
total_refill_lines = int(df_refills["pk"].count())  if not df_refills.empty else 0
total_refill_qty   = float(df_refills["qty"].sum()) if not df_refills.empty else 0.0

full_match_ct  = int((full_combined["recon_status"] == "✅ Full Match").sum())       if not full_combined.empty else 0
mismatch_ct    = int((full_combined["recon_status"] == "⚠️ Qty Mismatch").sum())     if not full_combined.empty else 0
missing_ct     = int((full_combined["recon_status"] == "❌ Missing Refills").sum())  if not full_combined.empty else 0
no_refill_ct   = int((full_combined["recon_status"] == "❌ No Refills").sum())       if not full_combined.empty else 0

st.markdown("##### 🛒 Pull Demand  →  📦 Refill Actuals")
p1, p2, p3, p4 = st.columns(4)
p1.metric("Pull Lines",   f"{total_pull_lines:,}",   help="Pyxis Pull lines from pharmacy_orders")
p2.metric("Pull Qty",     f"{total_pull_qty:,.0f}",  help="Total units pulled from carousel")
p3.metric("Refill Lines", f"{total_refill_lines:,}", help="event_type = 'Refill' transactions")
p4.metric("Refill Qty",   f"{total_refill_qty:,.0f}",help="Total units loaded via Refill events")

st.markdown("##### 🔎 Reconciliation (Full Drop devices)")
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Devices",           total_scheduled)
c2.metric("✅ Full Match",     full_match_ct)
c3.metric("⚠️ Qty Mismatch",  mismatch_ct)
c4.metric("❌ Missing Refills",missing_ct)
c5.metric("❌ No Refills",     no_refill_ct)

if total_pull_lines == 0:
    st.info("No Pyxis Pull records found for this date — upload pharmacy workflow data.")
elif missing_ct == 0 and mismatch_ct == 0 and no_refill_ct == 0:
    st.success(f"✅ All {full_match_ct} devices reconcile fully — pull and refill quantities match.")
else:
    issues = []
    if no_refill_ct:   issues.append(f"{no_refill_ct} device(s) pulled but never refilled")
    if missing_ct:     issues.append(f"{missing_ct} device(s) missing some refill lines")
    if mismatch_ct:    issues.append(f"{mismatch_ct} device(s) with qty mismatches")
    st.warning("⚠️ " + " · ".join(issues) + " — see per-drop tabs for detail.")

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
        drop_df  = all_drop_dfs[drop["label"]]
        full_df  = drop_df[drop_df["drop_type"] == "Full Drop"]
        stock_df = drop_df[drop_df["drop_type"] == "Stockouts Only"]
        full_tbl_event  = None
        stock_tbl_event = None
        _full_tbl_df    = pd.DataFrame()
        _stock_tbl_df   = pd.DataFrame()

        total_full       = len(full_df)
        drop_pull_qty    = float(full_df["pull_qty"].sum())    if not full_df.empty else 0.0
        drop_pull_lines  = int(full_df["pull_lines"].sum())    if not full_df.empty else 0
        drop_refill_qty  = float(full_df["refill_qty"].sum())  if not full_df.empty else 0.0
        drop_refill_lines= int(full_df["refill_lines"].sum())  if not full_df.empty else 0
        drop_matched     = int(full_df["matched_lines"].sum()) if not full_df.empty else 0
        drop_mismatch    = int(full_df["mismatch_lines"].sum())if not full_df.empty else 0
        drop_missing     = int(full_df["missing_lines"].sum()) if not full_df.empty else 0
        drop_extra       = int(full_df["extra_lines"].sum())   if not full_df.empty else 0
        full_match_devs  = int((full_df["recon_status"] == "✅ Full Match").sum()) if not full_df.empty else 0

        st.subheader(f"{drop['label']} — Scheduled {drop['time']}")
        if drop.get("wed_note"):
            st.info("📅 Wednesday: SM/SMOR devices included in this drop.")

        st.markdown("**Pull  →  Refill**")
        p1, p2, p3, p4, p5 = st.columns(5)
        p1.metric("Pull Qty",     f"{drop_pull_qty:,.0f}")
        p2.metric("Pull Lines",   f"{drop_pull_lines:,}")
        p3.metric("Refill Qty",   f"{drop_refill_qty:,.0f}")
        p4.metric("Refill Lines", f"{drop_refill_lines:,}")
        p5.metric("✅ Devices Full Match", full_match_devs)

        st.markdown("**By Med-ID**")
        r1, r2, r3, r4 = st.columns(4)
        r1.metric("✅ Matched Exact",      drop_matched)
        r2.metric("⚠️ Qty Mismatch",      drop_mismatch)
        r3.metric("❌ Pulled Not Refilled",drop_missing)
        r4.metric("🔵 Refilled Not Pulled",drop_extra)

        st.divider()

        # ── Device reconciliation table — sorted by pull_qty desc ─────────
        if not full_df.empty:
            st.markdown("#### Full Drop — Reconciliation by Device")

            chart_df = full_df[full_df["pull_qty"] > 0].sort_values("pull_qty", ascending=False)
            if chart_df.empty:
                chart_df = full_df[full_df["refill_qty"] > 0].sort_values("refill_qty", ascending=False)

            if not chart_df.empty:
                fig = go.Figure()
                fig.add_trace(go.Bar(
                    y=chart_df["device"], x=chart_df["pull_qty"],
                    name="🛒 Pull Qty", orientation="h", marker_color="#1f77b4",
                    text=chart_df["pull_qty"].map(lambda v: f"{v:.0f}"), textposition="outside",
                ))
                fig.add_trace(go.Bar(
                    y=chart_df["device"], x=chart_df["refill_qty"],
                    name="📦 Refill Qty", orientation="h", marker_color="#2ca02c",
                    text=chart_df["refill_qty"].map(lambda v: f"{v:.0f}"), textposition="outside",
                ))
                fig.update_layout(
                    barmode="group",
                    yaxis={"categoryorder": "total ascending"},
                    height=max(320, len(chart_df) * 32 + 80),
                    margin=dict(l=0, r=80, t=40, b=0),
                    legend=dict(orientation="h", yanchor="bottom", y=1.02),
                    title=f"{drop['label']} — Pull Qty vs Refill Qty by Device",
                    xaxis_title="Units",
                )
                st.plotly_chart(fig, use_container_width=True)

            areas = sorted(full_df["area"].unique())
            sel_areas = st.multiselect("Filter by Area", areas, default=areas, key=f"area_{i}")
            view = full_df[full_df["area"].isin(sel_areas)] if sel_areas else full_df
            view = view.sort_values("pull_qty", ascending=False)

            _COL_CFG = {
                "recon_status":   st.column_config.TextColumn("Reconciliation", width="medium"),
                "device":         st.column_config.TextColumn("Device"),
                "area":           st.column_config.TextColumn("Area"),
                "pull_lines":     st.column_config.NumberColumn("Pull Lines",    format="%d"),
                "pull_qty":       st.column_config.NumberColumn("🛒 Pull Qty",   format="%.0f"),
                "refill_lines":   st.column_config.NumberColumn("Refill Lines",  format="%d"),
                "refill_qty":     st.column_config.NumberColumn("📦 Refill Qty", format="%.0f"),
                "matched_lines":  st.column_config.NumberColumn("✅ Matched",    format="%d"),
                "mismatch_lines": st.column_config.NumberColumn("⚠️ Mismatch",  format="%d"),
                "missing_lines":  st.column_config.NumberColumn("❌ Missing",    format="%d"),
                "extra_lines":    st.column_config.NumberColumn("🔵 Extra",      format="%d"),
                "stockout_print": st.column_config.TextColumn("Stockout Report"),
            }
            full_tbl_event = st.dataframe(
                view[["recon_status","device","area",
                      "pull_lines","pull_qty","refill_lines","refill_qty",
                      "matched_lines","mismatch_lines","missing_lines","extra_lines",
                      "stockout_print"]],
                use_container_width=True, hide_index=True, column_config=_COL_CFG,
                on_select="rerun", selection_mode="single-row",
                key=f"full_tbl_{i}",
            )
            _full_tbl_df = view.reset_index(drop=True)
            st.download_button(
                f"⬇️ Export {drop['label']} to Excel",
                data=to_excel_bytes(view),
                file_name=f"carousel_recon_{drop['label'].replace(' ','_').lower()}_{sel_date}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key=f"dl_{i}_full",
            )

        # ── Stockouts-only devices ─────────────────────────────────────────
        if not stock_df.empty:
            st.divider()
            st.markdown("#### Stockouts Only Devices")
            st.caption("Drop triggered by stockout only.")
            stock_active = stock_df[stock_df["refill_lines"] > 0]
            st.markdown(f"**{len(stock_active)}** of {len(stock_df)} stockout-only devices had refill activity.")
            stock_tbl_event = st.dataframe(
                stock_df[["recon_status","device","area",
                          "pull_lines","pull_qty","refill_lines","refill_qty",
                          "matched_lines","mismatch_lines","missing_lines","extra_lines"]],
                use_container_width=True, hide_index=True, column_config=_COL_CFG,
                on_select="rerun", selection_mode="single-row",
                key=f"stock_tbl_{i}",
            )
            _stock_tbl_df = stock_df.reset_index(drop=True)

        # ── Device reconciliation drill-down ───────────────────────────────
        # Determine selected device from whichever table had a row clicked
        drill_dev = None
        _full_rows  = full_tbl_event.selection.rows  if full_tbl_event  is not None else []
        _stock_rows = stock_tbl_event.selection.rows if stock_tbl_event is not None else []
        if _full_rows and not _full_tbl_df.empty:
            drill_dev = _full_tbl_df.iloc[_full_rows[0]]["device"]
        elif _stock_rows and not _stock_tbl_df.empty:
            drill_dev = _stock_tbl_df.iloc[_stock_rows[0]]["device"]

        if drill_dev:
            st.divider()
            st.markdown(f"#### 🔬 Device Drill-Down — {drill_dev}")
            st.caption("Pull window = tight carousel pull times. Refill window extends further — techs walk to floors and refill Pyxis after the carousel pull.")
            h0, m0 = drop["win_start"]
            h1, m1 = drop["win_end"]
            rh1, rm1 = drop.get("refill_win_end", drop["win_end"])
            start_min      = h0  * 60 + m0
            end_min        = h1  * 60 + m1
            refill_end_min = rh1 * 60 + rm1

            def _pull_filter(df):
                if df.empty: return pd.DataFrame()
                m = df["dt"].dt.hour * 60 + df["dt"].dt.minute
                return df[(m >= start_min) & (m <= end_min)]

            def _refill_filter(df):
                if df.empty: return pd.DataFrame()
                m = df["dt"].dt.hour * 60 + df["dt"].dt.minute
                return df[(m >= start_min) & (m <= refill_end_min)]

            dev_pulls_raw   = _pull_filter(df_pulls)
            dev_refills_raw = _refill_filter(df_refills)
            dev_pulls_raw   = dev_pulls_raw[dev_pulls_raw["destination"] == drill_dev]   if not dev_pulls_raw.empty   else pd.DataFrame()
            dev_refills_raw = dev_refills_raw[dev_refills_raw["device"]  == drill_dev]   if not dev_refills_raw.empty else pd.DataFrame()

            _, detail_df = _reconcile_device(dev_pulls_raw, dev_refills_raw)

            # ── 1. Matched / Mismatched comparison ────────────────────────
            both = detail_df[detail_df["pull_qty"] > 0][detail_df["refill_qty"] > 0] if not detail_df.empty else pd.DataFrame()
            st.markdown(f"**1 of 3 — Pull vs Refill by Med (both sides present) — {drill_dev}**")
            if both.empty:
                st.info("No meds found on both sides for this device in this window.")
            else:
                both_disp = both[["med_status","med_id","med_desc","pull_qty","refill_qty","qty_diff"]].sort_values("pull_qty", ascending=False)
                st.dataframe(both_disp, use_container_width=True, hide_index=True, column_config={
                    "med_status": st.column_config.TextColumn("Status"),
                    "med_id":     st.column_config.TextColumn("Med ID"),
                    "med_desc":   st.column_config.TextColumn("Medication"),
                    "pull_qty":   st.column_config.NumberColumn("Pull Qty",   format="%.0f"),
                    "refill_qty": st.column_config.NumberColumn("Refill Qty", format="%.0f"),
                    "qty_diff":   st.column_config.NumberColumn("Diff (Refill−Pull)", format="%.0f"),
                })

            # ── 2. Pulled Not Refilled ─────────────────────────────────────
            st.markdown(f"**2 of 3 — ❌ Pulled Not Refilled (pull exists, no refill)**")
            not_refilled = detail_df[detail_df["med_status"] == "❌ Pulled Not Refilled"] if not detail_df.empty else pd.DataFrame()
            if not_refilled.empty:
                st.success("None — all pulled meds have a matching refill.")
            else:
                nr_raw = dev_pulls_raw[dev_pulls_raw["med_id"].isin(not_refilled["med_id"])]\
                    [["dt","user_name","med_id","med_desc","qty"]].sort_values("dt").reset_index(drop=True)
                st.caption(f"{len(nr_raw):,} pull lines · {nr_raw['qty'].sum():,.0f} units with no matching refill")
                st.dataframe(nr_raw, use_container_width=True, hide_index=True, column_config={
                    "dt":        st.column_config.DatetimeColumn("Pull Time", format="HH:mm:ss"),
                    "user_name": st.column_config.TextColumn("User"),
                    "med_id":    st.column_config.TextColumn("Med ID"),
                    "med_desc":  st.column_config.TextColumn("Medication"),
                    "qty":       st.column_config.NumberColumn("Pull Qty", format="%.0f"),
                })

            # ── 3. Refilled Not Pulled ─────────────────────────────────────
            st.markdown(f"**3 of 3 — 🔵 Refilled Not Pulled (refill exists, no pull record)**")
            not_pulled = detail_df[detail_df["med_status"] == "🔵 Refilled Not Pulled"] if not detail_df.empty else pd.DataFrame()
            if not_pulled.empty:
                st.success("None — all refill events have a matching pull record.")
            else:
                np_raw = dev_refills_raw[dev_refills_raw["med_id"].isin(not_pulled["med_id"])]\
                    [["dt","user_name","med_id","med_desc","qty","beginning_qty","ending_qty"]].sort_values("dt").reset_index(drop=True)
                st.caption(f"{len(np_raw):,} refill lines · {np_raw['qty'].sum():,.0f} units with no matching pull")
                st.dataframe(np_raw, use_container_width=True, hide_index=True, column_config={
                    "dt":            st.column_config.DatetimeColumn("Refill Time", format="HH:mm:ss"),
                    "user_name":     st.column_config.TextColumn("Tech"),
                    "med_id":        st.column_config.TextColumn("Med ID"),
                    "med_desc":      st.column_config.TextColumn("Medication"),
                    "qty":           st.column_config.NumberColumn("Refill Qty", format="%.0f"),
                    "beginning_qty": st.column_config.NumberColumn("Before",     format="%.0f"),
                    "ending_qty":    st.column_config.NumberColumn("After",      format="%.0f"),
                })


# ── Day Summary Tab ───────────────────────────────────────────────────────────
with tabs[len(schedule)]:
    st.subheader(f"Day Summary — {sel_date.strftime('%A %b %d, %Y')}")

    if combined.empty:
        st.info("No data found for this date.")
    else:
        drop_summary = []
        for drop in schedule:
            ddf  = all_drop_dfs[drop["label"]]
            full = ddf[ddf["drop_type"] == "Full Drop"]
            drop_summary.append({
                "Drop":          drop["label"],
                "Devices":       len(full),
                "✅ Full Match": int((full["recon_status"] == "✅ Full Match").sum()),
                "⚠️ Mismatch":  int((full["recon_status"] == "⚠️ Qty Mismatch").sum()),
                "❌ Missing":    int((full["recon_status"] == "❌ Missing Refills").sum()),
                "❌ No Refill":  int((full["recon_status"] == "❌ No Refills").sum()),
                "Pull Qty":      float(full["pull_qty"].sum()),
                "Refill Qty":    float(full["refill_qty"].sum()),
                "Pull Lines":    int(full["pull_lines"].sum()),
                "Matched Lines": int(full["matched_lines"].sum()),
                "Missing Lines": int(full["missing_lines"].sum()),
            })
        ds = pd.DataFrame(drop_summary)
        ds["Match %"] = ds.apply(
            lambda r: round(r["Matched Lines"] / r["Pull Lines"] * 100, 1) if r["Pull Lines"] > 0 else 0.0,
            axis=1
        )

        fig_comp = px.bar(
            ds, x="Drop", y="Match %",
            color="Match %",
            color_continuous_scale=["#ef4444", "#facc15", "#22c55e"],
            range_color=[0, 100],
            title="Reconciliation Match % by Drop (Matched ÷ Pull Lines)",
            text=ds["Match %"].astype(str) + "%",
        )
        fig_comp.update_traces(textposition="outside")
        fig_comp.update_layout(coloraxis_showscale=False, height=300)
        st.plotly_chart(fig_comp, use_container_width=True)

        st.dataframe(ds, use_container_width=True, hide_index=True, column_config={
            "Pull Qty":      st.column_config.NumberColumn("🛒 Pull Qty",    format="%.0f"),
            "Refill Qty":    st.column_config.NumberColumn("📦 Refill Qty",  format="%.0f"),
            "Pull Lines":    st.column_config.NumberColumn("Pull Lines",     format="%d"),
            "Matched Lines": st.column_config.NumberColumn("✅ Matched",     format="%d"),
            "Missing Lines": st.column_config.NumberColumn("❌ Missing",     format="%d"),
            "Match %":       st.column_config.NumberColumn("Match %",        format="%.1f"),
        })

        st.divider()
        st.subheader("Technician Refill Activity")
        if not df_refills.empty:
            tech_day = (
                df_refills.groupby("user_name")
                .agg(lines=("pk","count"), units=("qty","sum"), devices=("device","nunique"))
                .reset_index().sort_values("units", ascending=False)
            )
            st.dataframe(tech_day, use_container_width=True, hide_index=True, column_config={
                "lines":   st.column_config.NumberColumn("Refill Lines", format="%d"),
                "units":   st.column_config.NumberColumn("Units",        format="%.0f"),
                "devices": st.column_config.NumberColumn("Devices",      format="%d"),
            })
        else:
            st.info("No Refill events for this date.")

        st.download_button(
            "⬇️ Export Full Day Reconciliation to Excel",
            data=to_excel_bytes(combined.drop(columns=["stockout_print"], errors="ignore")),
            file_name=f"carousel_recon_day_{sel_date}.xlsx",
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
        "The tracker currently captures events with **exact** `event_type = 'Refill'`. "
        "The table below shows all distinct event types present on this date so you can "
        "confirm 'Refill' exists and is spelled correctly in your Pyxis data."
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
            etype_all["captured?"] = etype_all["event_type"].apply(
                lambda e: "✅ Yes" if e == "Refill" else "—"
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
                    lambda e: "✅ Yes" if e == "Refill" else "—"
                )
                st.dataframe(etype_sched, use_container_width=True, hide_index=True)

        st.markdown(
            f"**Refill events captured:** {len(df_refills):,} transactions · "
            f"{df_refills['qty'].sum():,.0f} units · "
            f"{df_refills['device'].nunique()} devices  \n"
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
