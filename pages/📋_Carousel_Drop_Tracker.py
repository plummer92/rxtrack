import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import plotly.express as px
import io
from datetime import date
from sqlalchemy import text
from App import engine

st.set_page_config(page_title="Carousel Drop Tracker", page_icon="📋", layout="wide")

st.header("📋 Carousel Drop Tracker")
st.caption(
    "Track pharmacy Pyxis Pull demand by drop, then compare it against actual Pyxis refill activity to confirm the loop is closed."
)

# ═══════════════════════════════════════════════════════════════════════════════
# SCHEDULE + DEVICE CONFIG
# NOTE:
# These mappings are intentionally kept the same structure as your current page.
# If you already have updated versions in production, keep yours.
# ═══════════════════════════════════════════════════════════════════════════════

AREA_COLOR = {
    "Stack": "#22c55e",
    "Adult ICU": "#f97316",
    "ED": "#facc15",
    "WCC": "#ec4899",
    "OR": "#818cf8",
    "PHP": "#c084fc",
    "Montvale": "#fda4af",
    "Other": "#94a3b8",
}

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

STOCKOUTS_PRINT_AUTO = [
    "SJS7EN","SJS7ES","SJS7WN",
    "SJSCVICU-NE","SJSCVICU-NW","SJSCVICU-SE","SJSCVICU-SW",
    "SJSICA-N","SJSICA-S","SJSICU4C",
    "SJSTRAUMA1","SJSTRAUMA2","SJSTRAUMA3",
    "SJSEDSO","SJSEDTRIAG","SJSEMS","SJSER",
    "SJSCSECT1","SJSCSECT2","SJSNICUC","SJSNICUN","SJSNICUS",
    "SJSPICU","SJSBCC",
    "SJSOR","SJSOR2",
]
STOCKOUTS_PRINT_AUTO_SET = set(STOCKOUTS_PRINT_AUTO)

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

_SASU_0400_FULL = [
    "SJSCVICU-NE","SJSCVICU-NW","SJSCVICU-SE","SJSCVICU-SW",
    "SJSICA-N","SJSICA-S","SJSICU4C",
    "SJS11E","SJS11N","SJS11W","SJS9EN","SJS9ES","SJS9WN",
    "SJS8EN","SJS8ES","SJS8WN","SJS7EN","SJS7ES","SJS7WN",
    "SJS6EN","SJS6ES","SJS6WN","SJS5EN","SJS5ES","SJS5WN",
    "SJS3E","SJS3N","SJS4E","SJS4N","SJS4W","SJS4S",
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
    dow = sel_date.weekday()
    is_weekend = dow >= 5
    is_wednesday = dow == 2

    if is_weekend:
        return [
            {"label": "0400 Drop", "time": "04:00", "win_start": (3, 0), "win_end": (11, 30), "full": _SASU_0400_FULL, "stockouts": []},
            {"label": "1235 Drop", "time": "12:35", "win_start": (11, 31), "win_end": (20, 0), "full": _SASU_1235_FULL, "stockouts": _SASU_1235_STOCK},
        ]

    mf_0700 = _MF_0700_FULL + (_MF_0700_WED if is_wednesday else [])
    return [
        {"label": "0400 Drop", "time": "04:00", "win_start": (3, 0), "win_end": (6, 29), "full": _MF_0400_FULL, "stockouts": []},
        {"label": "0700 Drop", "time": "07:00", "win_start": (6, 30), "win_end": (11, 0), "full": mf_0700, "stockouts": [], "wed_note": is_wednesday},
        {"label": "1235 Drop", "time": "12:35", "win_start": (10, 30), "win_end": (14, 14), "full": _MF_1235_FULL, "stockouts": _MF_1235_STOCK},
        {"label": "1430 Drop", "time": "14:30", "win_start": (14, 0), "win_end": (20, 0), "full": _MF_1430_FULL, "stockouts": []},
    ]


# ═══════════════════════════════════════════════════════════════════════════════
# DATA LOADERS
# ═══════════════════════════════════════════════════════════════════════════════

@st.cache_data(ttl=300)
def load_refills(sel_date):
    try:
        sql = text(
            """
            SELECT pk, dt, user_name, device, med_id, med_desc, event_type,
                   qty, beginning_qty, ending_qty
            FROM events
            WHERE dt::date = :d
              AND event_type ILIKE '%refill%'
              AND event_type NOT ILIKE '%unload%'
              AND event_type NOT ILIKE '%cancel%'
              AND event_type NOT ILIKE '%empty%'
            """
        )
        with engine.connect() as conn:
            df = pd.read_sql(sql, conn, params={"d": sel_date})
        df["dt"] = pd.to_datetime(df["dt"], errors="coerce")
        df["device"] = df["device"].fillna("Unknown").astype(str).str.strip()
        df["user_name"] = df["user_name"].fillna("Unknown").astype(str).str.strip()
        df["event_type"] = df["event_type"].fillna("").astype(str).str.strip()
        df["qty"] = pd.to_numeric(df["qty"], errors="coerce").fillna(0)
        df["beginning_qty"] = pd.to_numeric(df.get("beginning_qty"), errors="coerce")
        df["ending_qty"] = pd.to_numeric(df.get("ending_qty"), errors="coerce")
        return df
    except Exception as e:
        st.error(f"[load_refills] {e}")
        return pd.DataFrame()


@st.cache_data(ttl=300)
def load_other_replenishment(sel_date):
    try:
        sql = text(
            """
            SELECT pk, dt, user_name, device, med_id, med_desc, event_type,
                   qty, beginning_qty, ending_qty
            FROM events
            WHERE dt::date = :d
              AND (
                    event_type ILIKE '%load%'
                 OR event_type ILIKE '%restock%'
                 OR event_type ILIKE '%replenish%'
              )
              AND event_type NOT ILIKE '%refill%'
              AND event_type NOT ILIKE '%unload%'
              AND event_type NOT ILIKE '%cancel%'
              AND event_type NOT ILIKE '%empty%'
            """
        )
        with engine.connect() as conn:
            df = pd.read_sql(sql, conn, params={"d": sel_date})
        df["dt"] = pd.to_datetime(df["dt"], errors="coerce")
        df["device"] = df["device"].fillna("Unknown").astype(str).str.strip()
        df["user_name"] = df["user_name"].fillna("Unknown").astype(str).str.strip()
        df["event_type"] = df["event_type"].fillna("").astype(str).str.strip()
        df["qty"] = pd.to_numeric(df["qty"], errors="coerce").fillna(0)
        df["beginning_qty"] = pd.to_numeric(df.get("beginning_qty"), errors="coerce")
        df["ending_qty"] = pd.to_numeric(df.get("ending_qty"), errors="coerce")
        return df
    except Exception as e:
        st.error(f"[load_other_replenishment] {e}")
        return pd.DataFrame()


@st.cache_data(ttl=300)
def load_pyxis_pulls(sel_date):
    try:
        sql = text(
            """
            SELECT pk, dt, user_name, destination, med_id, med_desc, priority, qty
            FROM pharmacy_orders
            WHERE dt::date = :d
              AND priority ILIKE '%pyxis%pull%'
            """
        )
        with engine.connect() as conn:
            df = pd.read_sql(sql, conn, params={"d": str(sel_date)})
        df["dt"] = pd.to_datetime(df["dt"], errors="coerce")
        df["destination"] = df["destination"].fillna("Unknown").astype(str).str.strip()
        df["user_name"] = df["user_name"].fillna("Unknown").astype(str).str.strip()
        df["priority"] = df["priority"].fillna("").astype(str).str.strip()
        df["qty"] = pd.to_numeric(df["qty"], errors="coerce").fillna(0)
        return df
    except Exception as e:
        st.error(f"[load_pyxis_pulls] {e}")
        return pd.DataFrame()


@st.cache_data(ttl=300)
def load_all_events_for_date(sel_date):
    try:
        sql = text(
            """
            SELECT pk, dt, user_name, device, med_id, med_desc, event_type, qty
            FROM events
            WHERE dt::date = :d
            """
        )
        with engine.connect() as conn:
            df = pd.read_sql(sql, conn, params={"d": sel_date})
        df["dt"] = pd.to_datetime(df["dt"], errors="coerce")
        return df
    except Exception as e:
        st.error(f"[load_all_events_for_date] {e}")
        return pd.DataFrame()


def to_excel_bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False)
    return buf.getvalue()


def add_minutes(df: pd.DataFrame, dt_col: str = "dt") -> pd.DataFrame:
    if df.empty:
        return df.copy()
    out = df.copy()
    out["_mins"] = out[dt_col].dt.hour * 60 + out[dt_col].dt.minute
    return out


def filter_window(df: pd.DataFrame, start_min: int, end_min: int) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    return df[(df["_mins"] >= start_min) & (df["_mins"] <= end_min)].copy()


def loop_status(pull_qty: float, refill_qty: float, refill_txns: int, other_txns: int) -> str:
    if pull_qty == 0 and refill_txns == 0 and other_txns == 0:
        return "⬜ No Activity"
    if pull_qty == 0 and refill_txns > 0:
        return "ℹ️ Refill / No Pull"
    if pull_qty > 0 and refill_txns == 0 and other_txns > 0:
        return "⚠️ Other Repl / No Refill"
    if pull_qty > 0 and refill_txns == 0:
        return "❌ Not Refilled"
    coverage = refill_qty / pull_qty if pull_qty > 0 else 0
    if coverage >= 0.90:
        return "✅ Closed Loop"
    return "⚠️ Partial"


def coverage_pct(numer: float, denom: float):
    if denom <= 0:
        return None
    return round((numer / denom) * 100, 1)


def build_drop_df(drop, df_refills, df_other_repl, df_pulls):
    h0, m0 = drop["win_start"]
    h1, m1 = drop["win_end"]
    start_min = h0 * 60 + m0
    end_min = h1 * 60 + m1

    refill_win = filter_window(df_refills, start_min, end_min)
    other_win = filter_window(df_other_repl, start_min, end_min)
    pull_win = filter_window(df_pulls, start_min, end_min)

    if not refill_win.empty:
        refill_agg = (
            refill_win.groupby("device", dropna=False)
            .agg(
                refill_txns=("pk", "count"),
                refill_qty=("qty", "sum"),
                refill_techs=("user_name", lambda x: ", ".join(sorted(pd.Series(x).dropna().astype(str).unique()))),
                refill_first_time=("dt", "min"),
                refill_last_time=("dt", "max"),
            )
            .reset_index()
        )
    else:
        refill_agg = pd.DataFrame(columns=["device", "refill_txns", "refill_qty", "refill_techs", "refill_first_time", "refill_last_time"])

    if not other_win.empty:
        other_agg = (
            other_win.groupby("device", dropna=False)
            .agg(
                other_txns=("pk", "count"),
                other_qty=("qty", "sum"),
            )
            .reset_index()
        )
    else:
        other_agg = pd.DataFrame(columns=["device", "other_txns", "other_qty"])

    if not pull_win.empty:
        pull_agg = (
            pull_win.groupby("destination", dropna=False)
            .agg(
                pull_lines=("pk", "count"),
                pull_qty=("qty", "sum"),
            )
            .reset_index()
            .rename(columns={"destination": "device"})
        )
    else:
        pull_agg = pd.DataFrame(columns=["device", "pull_lines", "pull_qty"])

    def build_row(dev: str, drop_type: str, idle_status: str):
        p = pull_agg[pull_agg["device"] == dev]
        r = refill_agg[refill_agg["device"] == dev]
        o = other_agg[other_agg["device"] == dev]

        pull_lines = int(p["pull_lines"].iloc[0]) if not p.empty else 0
        pull_qty = float(p["pull_qty"].iloc[0]) if not p.empty else 0.0
        refill_txns = int(r["refill_txns"].iloc[0]) if not r.empty else 0
        refill_qty = float(r["refill_qty"].iloc[0]) if not r.empty else 0.0
        other_txns = int(o["other_txns"].iloc[0]) if not o.empty else 0
        other_qty = float(o["other_qty"].iloc[0]) if not o.empty else 0.0

        return {
            "device": dev,
            "area": DEVICE_AREA.get(dev, "Other"),
            "drop_type": drop_type,
            "stockout_print": "✅ Auto-Print" if dev in STOCKOUTS_PRINT_AUTO_SET else "❌ No Print",
            "pull_lines": pull_lines,
            "pull_qty": pull_qty,
            "refill_txns": refill_txns,
            "refill_qty": refill_qty,
            "other_txns": other_txns,
            "other_qty": other_qty,
            "qty_coverage": coverage_pct(refill_qty, pull_qty),
            "loop_status": loop_status(pull_qty, refill_qty, refill_txns, other_txns),
            "techs": r["refill_techs"].iloc[0] if not r.empty else "",
            "first_time": r["refill_first_time"].iloc[0] if not r.empty else None,
            "last_time": r["refill_last_time"].iloc[0] if not r.empty else None,
            "status": "✅ Refilled" if refill_txns > 0 or other_txns > 0 else idle_status,
        }

    rows = []
    for dev in drop["full"]:
        rows.append(build_row(dev, "Full Drop", "❌ Missed"))
    for dev in drop["stockouts"]:
        rows.append(build_row(dev, "Stockouts Only", "🔵 No Stockout"))

    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.sort_values(["pull_qty", "pull_lines", "device"], ascending=[False, False, True]).reset_index(drop=True)
    return out


# ═══════════════════════════════════════════════════════════════════════════════
# DATE SELECTOR + LOAD
# ═══════════════════════════════════════════════════════════════════════════════

with st.sidebar:
    st.markdown("### 📅 Select Drop Date")
    sel_date = st.date_input("Date", value=date.today(), key="cdt_date")
    st.caption(f"Day: **{sel_date.strftime('%A')}**")
    is_weekend = sel_date.weekday() >= 5
    st.info(
        "Sa-Su schedule active."
        if is_weekend
        else "M-F schedule active." + (" (Wednesday — SM/SMOR included)" if sel_date.weekday() == 2 else "")
    )

with st.spinner("Loading pull and refill data..."):
    df_refills = add_minutes(load_refills(sel_date))
    df_other_repl = add_minutes(load_other_replenishment(sel_date))
    df_pulls = add_minutes(load_pyxis_pulls(sel_date))

schedule = get_schedule(sel_date)
all_drop_dfs = {d["label"]: build_drop_df(d, df_refills, df_other_repl, df_pulls) for d in schedule}
combined = pd.concat(all_drop_dfs.values(), ignore_index=True) if all_drop_dfs else pd.DataFrame()
full_combined = combined[combined["drop_type"] == "Full Drop"] if not combined.empty else pd.DataFrame()

# ═══════════════════════════════════════════════════════════════════════════════
# TOP-LINE KPIs
# ═══════════════════════════════════════════════════════════════════════════════

total_scheduled = len(full_combined)
total_pull_lines = int(df_pulls["pk"].count()) if not df_pulls.empty else 0
total_pull_qty = float(df_pulls["qty"].sum()) if not df_pulls.empty else 0.0
total_refill_lines = int(df_refills["pk"].count()) if not df_refills.empty else 0
total_refill_qty = float(df_refills["qty"].sum()) if not df_refills.empty else 0.0
total_other_lines = int(df_other_repl["pk"].count()) if not df_other_repl.empty else 0
total_other_qty = float(df_other_repl["qty"].sum()) if not df_other_repl.empty else 0.0
day_coverage_pct = coverage_pct(total_refill_qty, total_pull_qty) or 0.0

closed_ct = int((full_combined["loop_status"] == "✅ Closed Loop").sum()) if not full_combined.empty else 0
partial_ct = int((full_combined["loop_status"] == "⚠️ Partial").sum()) if not full_combined.empty else 0
no_refill_ct = int((full_combined["loop_status"] == "❌ Not Refilled").sum()) if not full_combined.empty else 0

st.markdown("##### 🛒 Pull Demand → 🔄 Refill Completion")
p1, p2, p3, p4 = st.columns(4)
p1.metric("Pull Lines", f"{total_pull_lines:,}", help="Line items pulled from carousel via pharmacy workflow")
p2.metric("Pull Qty", f"{total_pull_qty:,.0f}", help="Primary workload metric — total units pulled from carousel")
p3.metric("Refill Qty", f"{total_refill_qty:,.0f}", help="True refill units captured in events table")
p4.metric("Qty Coverage", f"{day_coverage_pct:.1f}%")

c1, c2, c3, c4, c5, c6 = st.columns(6)
c1.metric("Scheduled Full Drops", f"{total_scheduled:,}")
c2.metric("✅ Closed", f"{closed_ct:,}")
c3.metric("⚠️ Partial", f"{partial_ct:,}")
c4.metric("❌ Not Refilled", f"{no_refill_ct:,}")
c5.metric("Refill Transactions", f"{total_refill_lines:,}")
c6.metric("Other Repl Txns", f"{total_other_lines:,}")

if total_pull_qty == 0:
    st.info("No Pyxis Pull records found for this date.")
elif day_coverage_pct >= 100:
    st.success(
        f"✅ Full loop closed — {total_pull_qty:,.0f} units pulled, {total_refill_qty:,.0f} units refilled ({day_coverage_pct:.1f}% coverage)."
    )
else:
    gap = total_pull_qty - total_refill_qty
    st.warning(
        f"⚠️ Loop not fully closed — {gap:,.0f} units remain between pull demand and refill completion ({day_coverage_pct:.1f}% coverage)."
    )

st.divider()

# ═══════════════════════════════════════════════════════════════════════════════
# TABS
# ═══════════════════════════════════════════════════════════════════════════════

drop_labels = [d["label"] for d in schedule]
tab_labels = drop_labels + ["📊 Day Summary", "📅 Schedule Reference", "🖨️ Stockout Print Config", "🧪 Event Diagnostics"]
tabs = st.tabs(tab_labels)

# ── Per-drop tabs ──────────────────────────────────────────────────────────────
for i, drop in enumerate(schedule):
    with tabs[i]:
        drop_df = all_drop_dfs[drop["label"]].copy()
        full_df = drop_df[drop_df["drop_type"] == "Full Drop"].copy()
        stock_df = drop_df[drop_df["drop_type"] == "Stockouts Only"].copy()

        drop_pull_qty = float(full_df["pull_qty"].sum()) if not full_df.empty else 0.0
        drop_pull_lines = int(full_df["pull_lines"].sum()) if not full_df.empty else 0
        drop_refill_qty = float(full_df["refill_qty"].sum()) if not full_df.empty else 0.0
        drop_refill_lines = int(full_df["refill_txns"].sum()) if not full_df.empty else 0
        drop_coverage = coverage_pct(drop_refill_qty, drop_pull_qty) or 0.0
        drop_closed = int((full_df["loop_status"] == "✅ Closed Loop").sum()) if not full_df.empty else 0
        drop_partial = int((full_df["loop_status"] == "⚠️ Partial").sum()) if not full_df.empty else 0
        drop_missed = int((full_df["loop_status"] == "❌ Not Refilled").sum()) if not full_df.empty else 0

        st.markdown(f"### {drop['label']} · {drop['time']}")
        if drop.get("wed_note"):
            st.caption("Wednesday note: Montvale devices are included in this drop.")

        m1, m2, m3, m4, m5, m6 = st.columns(6)
        m1.metric("Pull Qty", f"{drop_pull_qty:,.0f}")
        m2.metric("Pull Lines", f"{drop_pull_lines:,}")
        m3.metric("Refill Qty", f"{drop_refill_qty:,.0f}")
        m4.metric("Refill Txns", f"{drop_refill_lines:,}")
        m5.metric("Closed", f"{drop_closed:,}")
        m6.metric("Coverage", f"{drop_coverage:.1f}%")

        cov_bar = min(max(drop_coverage / 100.0, 0.0), 1.0)
        st.progress(cov_bar, text=f"Qty coverage: {drop_coverage:.1f}% ({drop_refill_qty:,.0f} / {drop_pull_qty:,.0f} units)")

        chart_df = full_df.sort_values(["pull_qty", "device"], ascending=[False, True]).copy()
        if not chart_df.empty:
            fig = go.Figure()
            fig.add_trace(go.Bar(
                y=chart_df["device"],
                x=chart_df["pull_qty"],
                name="🛒 Pull Qty",
                orientation="h",
            ))
            fig.add_trace(go.Bar(
                y=chart_df["device"],
                x=chart_df["refill_qty"],
                name="🔄 Refill Qty",
                orientation="h",
            ))
            fig.update_layout(
                barmode="group",
                height=max(450, len(chart_df) * 24),
                title=f"{drop['label']} — Pull Demand vs Refill Completion (Units)",
                yaxis_title="Device",
                xaxis_title="Units",
                margin=dict(l=20, r=20, t=60, b=20),
            )
            st.plotly_chart(fig, use_container_width=True)

        st.markdown("#### Full Drop Devices")
        full_view = full_df.sort_values(["pull_qty", "pull_lines", "device"], ascending=[False, False, True]).copy()
        st.dataframe(
            full_view[[
                "loop_status", "device", "area", "pull_qty", "pull_lines",
                "refill_qty", "refill_txns", "other_qty", "other_txns",
                "qty_coverage", "techs", "first_time", "last_time", "stockout_print"
            ]],
            use_container_width=True,
            hide_index=True,
            column_config={
                "loop_status": st.column_config.TextColumn("Loop Status", width="medium"),
                "device": st.column_config.TextColumn("Device"),
                "area": st.column_config.TextColumn("Area"),
                "pull_qty": st.column_config.NumberColumn("🛒 Pull Qty", format="%.0f"),
                "pull_lines": st.column_config.NumberColumn("Pull Lines", format="%d"),
                "refill_qty": st.column_config.NumberColumn("🔄 Refill Qty", format="%.0f"),
                "refill_txns": st.column_config.NumberColumn("Refill Txns", format="%d"),
                "other_qty": st.column_config.NumberColumn("Other Repl Qty", format="%.0f"),
                "other_txns": st.column_config.NumberColumn("Other Repl Txns", format="%d"),
                "qty_coverage": st.column_config.NumberColumn("Coverage %", format="%.1f"),
                "techs": st.column_config.TextColumn("Technician(s)"),
                "first_time": st.column_config.DatetimeColumn("First Refill", format="HH:mm"),
                "last_time": st.column_config.DatetimeColumn("Last Refill", format="HH:mm"),
                "stockout_print": st.column_config.TextColumn("Stockout Report"),
            },
        )

        if not stock_df.empty:
            st.markdown("#### Stockout-Only Devices")
            st.dataframe(
                stock_df[[
                    "loop_status", "device", "area", "pull_qty", "pull_lines",
                    "refill_qty", "refill_txns", "other_qty", "other_txns",
                    "qty_coverage", "techs", "first_time", "last_time", "stockout_print"
                ]],
                use_container_width=True,
                hide_index=True,
            )

        st.download_button(
            f"⬇️ Export {drop['label']} to Excel",
            data=to_excel_bytes(drop_df),
            file_name=f"carousel_drop_tracker_{sel_date}_{drop['label'].replace(' ', '_').lower()}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key=f"export_{i}",
        )

        st.divider()
        st.markdown("#### 🔬 Device Drill-Down")
        all_devices_in_drop = full_view["device"].dropna().astype(str).tolist()
        if not stock_df.empty:
            all_devices_in_drop += stock_df["device"].dropna().astype(str).tolist()
        all_devices_in_drop = sorted(pd.unique(all_devices_in_drop).tolist())

        drill_dev = st.selectbox(
            "Select a device to see individual line items",
            options=["— pick a device —"] + all_devices_in_drop,
            key=f"drill_{i}",
        )

        if drill_dev != "— pick a device —":
            h0, m0 = drop["win_start"]
            h1, m1 = drop["win_end"]
            start_min = h0 * 60 + m0
            end_min = h1 * 60 + m1

            pull_detail = filter_window(df_pulls, start_min, end_min)
            pull_detail = pull_detail[pull_detail["destination"] == drill_dev].sort_values("dt").reset_index(drop=True)

            refill_detail = filter_window(df_refills, start_min, end_min)
            refill_detail = refill_detail[refill_detail["device"] == drill_dev].sort_values("dt").reset_index(drop=True)

            other_detail = filter_window(df_other_repl, start_min, end_min)
            other_detail = other_detail[other_detail["device"] == drill_dev].sort_values("dt").reset_index(drop=True)

            col_pull, col_refill = st.columns(2)

            with col_pull:
                st.markdown(f"**🛒 Pharmacy Pull Lines — {drill_dev}**")
                st.caption("From pharmacy_orders (Pyxis Pull priority) — what was pulled from the carousel")
                if pull_detail.empty:
                    st.info("No pharmacy pull lines for this device in this window.")
                else:
                    st.caption(f"{len(pull_detail):,} lines · {pull_detail['qty'].sum():,.0f} units")
                    st.dataframe(
                        pull_detail[["dt", "user_name", "med_id", "med_desc", "qty", "priority"]],
                        use_container_width=True,
                        hide_index=True,
                        column_config={
                            "dt": st.column_config.DatetimeColumn("Time", format="HH:mm:ss"),
                            "user_name": st.column_config.TextColumn("User"),
                            "med_id": st.column_config.TextColumn("Med ID"),
                            "med_desc": st.column_config.TextColumn("Medication"),
                            "qty": st.column_config.NumberColumn("Qty", format="%.0f"),
                            "priority": st.column_config.TextColumn("Priority"),
                        },
                    )

            with col_refill:
                st.markdown(f"**🔄 Pyxis Refill Events — {drill_dev}**")
                st.caption("Primary completion signal from events table — true refill activity")
                if refill_detail.empty:
                    st.info("No Pyxis refill events for this device in this window.")
                else:
                    st.caption(f"{len(refill_detail):,} refill transactions · {refill_detail['qty'].sum():,.0f} units")
                    st.dataframe(
                        refill_detail[["dt", "user_name", "med_id", "med_desc", "event_type", "qty", "beginning_qty", "ending_qty"]],
                        use_container_width=True,
                        hide_index=True,
                        column_config={
                            "dt": st.column_config.DatetimeColumn("Time", format="HH:mm:ss"),
                            "user_name": st.column_config.TextColumn("Tech"),
                            "med_id": st.column_config.TextColumn("Med ID"),
                            "med_desc": st.column_config.TextColumn("Medication"),
                            "event_type": st.column_config.TextColumn("Event"),
                            "qty": st.column_config.NumberColumn("Qty", format="%.0f"),
                            "beginning_qty": st.column_config.NumberColumn("Before", format="%.0f"),
                            "ending_qty": st.column_config.NumberColumn("After", format="%.0f"),
                        },
                    )

                st.markdown("**Secondary fallback: other replenishment events**")
                if other_detail.empty:
                    st.caption("No secondary replenishment events in this window.")
                else:
                    st.caption(f"{len(other_detail):,} fallback transactions · {other_detail['qty'].sum():,.0f} units")
                    st.dataframe(
                        other_detail[["dt", "user_name", "med_id", "med_desc", "event_type", "qty", "beginning_qty", "ending_qty"]],
                        use_container_width=True,
                        hide_index=True,
                    )

# ── Day Summary ───────────────────────────────────────────────────────────────
with tabs[len(schedule)]:
    st.markdown("### 📊 Day Summary")
    if combined.empty:
        st.info("No drop data available for this date.")
    else:
        summary = (
            combined.groupby(["area", "loop_status"], dropna=False)
            .agg(
                devices=("device", "count"),
                pull_qty=("pull_qty", "sum"),
                refill_qty=("refill_qty", "sum"),
            )
            .reset_index()
            .sort_values(["pull_qty", "devices"], ascending=[False, False])
        )
        st.dataframe(summary, use_container_width=True, hide_index=True)

        area_summary = (
            combined.groupby("area", dropna=False)
            .agg(
                devices=("device", "count"),
                pull_qty=("pull_qty", "sum"),
                refill_qty=("refill_qty", "sum"),
            )
            .reset_index()
        )
        area_summary["coverage_pct"] = np.where(
            area_summary["pull_qty"] > 0,
            (area_summary["refill_qty"] / area_summary["pull_qty"] * 100).round(1),
            np.nan,
        )
        fig = px.bar(
            area_summary.sort_values("pull_qty", ascending=False),
            x="area",
            y=["pull_qty", "refill_qty"],
            barmode="group",
            title="Area Summary — Pull Qty vs Refill Qty",
        )
        st.plotly_chart(fig, use_container_width=True)

        st.download_button(
            "⬇️ Export Day Summary to Excel",
            data=to_excel_bytes(combined),
            file_name=f"carousel_drop_tracker_day_summary_{sel_date}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

# ── Schedule reference ────────────────────────────────────────────────────────
with tabs[len(schedule) + 1]:
    st.markdown("### 📅 Schedule Reference")
    ref_rows = []
    for drop in schedule:
        for dev in drop["full"]:
            ref_rows.append({
                "drop_label": drop["label"],
                "drop_time": drop["time"],
                "drop_type": "Full Drop",
                "device": dev,
                "area": DEVICE_AREA.get(dev, "Other"),
            })
        for dev in drop["stockouts"]:
            ref_rows.append({
                "drop_label": drop["label"],
                "drop_time": drop["time"],
                "drop_type": "Stockouts Only",
                "device": dev,
                "area": DEVICE_AREA.get(dev, "Other"),
            })
    ref_df = pd.DataFrame(ref_rows)
    st.dataframe(ref_df, use_container_width=True, hide_index=True)

# ── Stockout print config ─────────────────────────────────────────────────────
with tabs[len(schedule) + 2]:
    st.markdown("### 🖨️ Stockout Print Config")
    print_df = pd.DataFrame({
        "device": sorted(STOCKOUTS_PRINT_AUTO_SET),
        "area": [DEVICE_AREA.get(d, "Other") for d in sorted(STOCKOUTS_PRINT_AUTO_SET)],
        "stockout_report": "✅ Auto-Print",
    })
    st.dataframe(print_df, use_container_width=True, hide_index=True)

# ── Diagnostics ───────────────────────────────────────────────────────────────
with tabs[len(schedule) + 3]:
    st.markdown("### 🧪 Event Diagnostics")
    st.markdown(
        "Use this to confirm the correct Pyxis transaction types are being counted. "
        "This tracker treats **refill** as the primary completion signal and shows "
        "**load / restock / replenish** only as secondary fallback events."
    )

    df_all = load_all_events_for_date(sel_date)
    st.markdown(
        f"**Refill events captured:** {len(df_refills):,} transactions · {df_refills['qty'].sum():,.0f} units · {df_refills['device'].nunique() if not df_refills.empty else 0} devices  \n"
        f"**Other replenishment events captured:** {len(df_other_repl):,} transactions · {df_other_repl['qty'].sum():,.0f} units  \n"
        f"**Total events on date (all types):** {len(df_all):,}"
    )

    if df_all.empty:
        st.info("No event rows found for this date.")
    else:
        et = (
            df_all.assign(event_type=df_all["event_type"].fillna("Unknown").astype(str).str.strip())
            .groupby("event_type", dropna=False)
            .agg(txns=("pk", "count"), qty=("qty", "sum"))
            .reset_index()
            .sort_values(["txns", "qty"], ascending=[False, False])
        )
        st.dataframe(et, use_container_width=True, hide_index=True)
        st.dataframe(df_all.sort_values("dt", ascending=False), use_container_width=True, hide_index=True)
