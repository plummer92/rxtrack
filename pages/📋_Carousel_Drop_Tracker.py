import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
import io
from datetime import date, timedelta
from sqlalchemy import text
import App

_debug_event = getattr(App, "record_ui_debug_event", lambda *args, **kwargs: None)
_debug_panel = getattr(App, "render_ui_debugger", lambda *args, **kwargs: None)

st.set_page_config(page_title="Carousel Drop Tracker", page_icon="ðŸ“‹", layout="wide")
if hasattr(App, "render_sidebar"):
    start_date, end_date = App.render_sidebar()
else:
    App.render_sidebar_chrome()
    start_date = date.today() - timedelta(days=14)
    end_date = date.today()

engine = App.engine

if hasattr(App, "render_page_intro"):
    App.render_page_intro(
        "Carousel Drop Tracker",
        "Track quantity loaded per Pyxis device at each scheduled carousel drop with the same updated shell used on the overview page.",
        kicker="Tools",
    )
    _debug_event("Carousel Drop Tracker", "shared_intro_loaded")
    _debug_panel("Carousel Drop Tracker", intro_mode="shared")
else:
    st.header("ðŸ“‹ Carousel Drop Tracker")
    st.caption("Track quantity loaded per Pyxis device at each scheduled carousel drop.")
    _debug_event("Carousel Drop Tracker", "fallback_header_used")
    _debug_panel("Carousel Drop Tracker", intro_mode="fallback")

# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# SCHEDULE DATA
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

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

# Device â†’ hospital area
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

# â”€â”€ M-F Drop Schedule â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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

# â”€â”€ Sa-Su Drop Schedule â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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
    dow = sel_date.weekday()  # 0=Mon â€¦ 6=Sun
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


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# DATA LOADER
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

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
              AND COALESCE(UPPER(med_id), '') != 'PATCAS'
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
    """Pyxis Pull lines from pharmacy_orders â€” represent carousel pull demand."""
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


@st.cache_data(ttl=300)
def load_available_drop_dates(start_date, end_date):
    """Return dates in the selected window that have either refill or pull activity."""
    try:
        sql = text("""
            WITH refill_days AS (
                SELECT DISTINCT dt::date AS activity_date
                FROM events
                WHERE dt::date BETWEEN :start_date AND :end_date
                  AND event_type = 'Refill'
                  AND COALESCE(UPPER(med_id), '') != 'PATCAS'
            ),
            pull_days AS (
                SELECT DISTINCT dt::date AS activity_date
                FROM pharmacy_orders
                WHERE dt::date BETWEEN :start_date AND :end_date
                  AND priority ILIKE '%pyxis%pull%'
            )
            SELECT DISTINCT activity_date
            FROM (
                SELECT activity_date FROM refill_days
                UNION
                SELECT activity_date FROM pull_days
            ) days
            ORDER BY activity_date
        """)
        with engine.connect() as conn:
            df = pd.read_sql(
                sql,
                conn,
                params={"start_date": str(start_date), "end_date": str(end_date)},
            )
        if df.empty:
            return []
        return [pd.to_datetime(value).date() for value in df["activity_date"].tolist()]
    except Exception as e:
        st.error(f"[load_available_drop_dates] {e}")
        return []


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# DATE SELECTOR
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

available_dates = load_available_drop_dates(start_date, end_date)
default_sel_date = available_dates[-1] if available_dates else end_date

if "carousel_selected_date" not in st.session_state:
    st.session_state.carousel_selected_date = default_sel_date
elif st.session_state.carousel_selected_date not in available_dates and available_dates:
    st.session_state.carousel_selected_date = default_sel_date
elif not available_dates:
    st.session_state.carousel_selected_date = end_date

with st.sidebar:
    st.markdown("### ðŸ“… Select Drop Date")
    st.caption(f"Window: **{start_date.strftime('%m/%d/%Y')}** to **{end_date.strftime('%m/%d/%Y')}**")
    if available_dates:
        sel_date = st.selectbox(
            "Date",
            options=available_dates,
            index=available_dates.index(st.session_state.carousel_selected_date),
            key="cdt_date",
            format_func=lambda value: value.strftime("%m/%d/%Y (%A)"),
        )
        st.session_state.carousel_selected_date = sel_date
    else:
        sel_date = st.date_input(
            "Date",
            value=st.session_state.carousel_selected_date,
            min_value=start_date,
            max_value=end_date,
            key="cdt_date_empty",
        )
        st.session_state.carousel_selected_date = sel_date
        st.warning("No drop activity found in the selected window.")
    st.caption(f"Day: **{sel_date.strftime('%A')}**")
    is_weekend = sel_date.weekday() >= 5
    st.info("Sa-Su schedule active." if is_weekend else "M-F schedule active." +
            (" (Wednesday â€” SM/SMOR included)" if sel_date.weekday() == 2 else ""))

with st.spinner("Loading pharmacy pulls and Pyxis refills..."):
    df_refills = load_refills(sel_date)
    df_pulls   = load_pyxis_pulls(sel_date)

schedule = get_schedule(sel_date)


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# HELPER â€” BUILD DROP TABLE
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

def _med_status(pull_qty, refill_qty):
    if pull_qty > 0 and refill_qty == 0:   return "âŒ Pulled Not Refilled"
    if pull_qty == 0 and refill_qty > 0:   return "ðŸ”µ Refilled Not Pulled"
    if pull_qty == refill_qty:             return "âœ… Matched Exact"
    return "âš ï¸ Qty Mismatch"

def _device_recon_status(matched, mismatch, missing, extra, pull_lines, refill_lines):
    if pull_lines == 0 and refill_lines == 0: return "â¬œ No Activity"
    if pull_lines == 0:                       return "ðŸ”µ Refills Only"
    if refill_lines == 0:                     return "âŒ No Refills"
    if missing == 0 and mismatch == 0 and extra == 0: return "âœ… Full Match"
    if missing > 0:  return "âŒ Missing Refills"
    if mismatch > 0: return "âš ï¸ Qty Mismatch"
    return "ðŸ”µ Extra Refills"

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

    matched  = int((mg["med_status"] == "âœ… Matched Exact").sum())
    mismatch = int((mg["med_status"] == "âš ï¸ Qty Mismatch").sum())
    missing  = int((mg["med_status"] == "âŒ Pulled Not Refilled").sum())
    extra    = int((mg["med_status"] == "ðŸ”µ Refilled Not Pulled").sum())

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

    Pull window  = win_start â†’ win_end  (tight: when carousel is being worked)
    Refill window = win_start â†’ refill_win_end  (wider: techs walk to floors and
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
            "stockout_print": "âœ… Auto-Print" if dev in STOCKOUTS_PRINT_AUTO_SET else "âŒ No Print",
            **summary,
        })

    return pd.DataFrame(rows)


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# TOP-LINE KPIs (across all drops for the day)
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

def _time_mask(df, start_minute, end_minute, column="dt"):
    if df.empty:
        return pd.DataFrame()
    minutes = df[column].dt.hour * 60 + df[column].dt.minute
    return df[(minutes >= start_minute) & (minutes <= end_minute)].copy()


def _format_clock(ts):
    if pd.isna(ts):
        return "-"
    return pd.Timestamp(ts).strftime("%H:%M")


def _format_duration(minutes):
    if pd.isna(minutes):
        return "-"
    minutes = int(round(float(minutes)))
    hours, mins = divmod(minutes, 60)
    if hours:
        return f"{hours}h {mins}m"
    return f"{mins}m"


def _average_gap_minutes(df):
    if df.empty or len(df) < 2:
        return np.nan
    ordered = df.sort_values("dt")
    gaps = ordered["dt"].diff().dt.total_seconds().div(60).dropna()
    if gaps.empty:
        return np.nan
    return gaps.mean()


def build_drop_timing_summary(drop, sel_date, df_pulls, df_refills):
    start_min = drop["win_start"][0] * 60 + drop["win_start"][1]
    end_min = drop["win_end"][0] * 60 + drop["win_end"][1]
    refill_end_tuple = drop.get("refill_win_end", drop["win_end"])
    refill_end_min = refill_end_tuple[0] * 60 + refill_end_tuple[1]
    scheduled_ts = pd.Timestamp(f"{sel_date} {drop['time']}")

    pull_win = _time_mask(df_pulls, start_min, end_min)
    refill_win = _time_mask(df_refills, start_min, refill_end_min)

    device_rows = []
    device_plan = (
        [(device, "Full Drop") for device in drop["full"]]
        + [(device, "Stockouts Only") for device in drop.get("stockouts", [])]
    )

    for device, drop_type in device_plan:
        device_pulls = pull_win[pull_win["destination"] == device] if not pull_win.empty else pd.DataFrame()
        device_refills = refill_win[refill_win["device"] == device] if not refill_win.empty else pd.DataFrame()

        first_pull = device_pulls["dt"].min() if not device_pulls.empty else pd.NaT
        last_pull = device_pulls["dt"].max() if not device_pulls.empty else pd.NaT
        pull_completion_minutes = (
            max((last_pull - scheduled_ts).total_seconds() / 60, 0)
            if pd.notna(last_pull)
            else np.nan
        )
        pull_span_minutes = (
            max((last_pull - first_pull).total_seconds() / 60, 0)
            if pd.notna(first_pull) and pd.notna(last_pull)
            else np.nan
        )
        avg_pull_gap_minutes = _average_gap_minutes(device_pulls)

        device_rows.append(
            {
                "device": device,
                "area": DEVICE_AREA.get(device, "Other"),
                "drop_type": drop_type,
                "pull_qty": float(device_pulls["qty"].sum()) if not device_pulls.empty else 0.0,
                "pull_lines": int(device_pulls["pk"].count()) if not device_pulls.empty else 0,
                "loaded_qty": float(device_refills["qty"].sum()) if not device_refills.empty else 0.0,
                "refill_lines": int(device_refills["pk"].count()) if not device_refills.empty else 0,
                "meds_loaded": int(device_refills["med_id"].nunique()) if not device_refills.empty else 0,
                "first_pull": first_pull,
                "last_pull": last_pull,
                "pull_completion_minutes": pull_completion_minutes,
                "pull_span_minutes": pull_span_minutes,
                "avg_pull_gap_minutes": avg_pull_gap_minutes,
                "pull_completion_display": _format_duration(pull_completion_minutes),
                "pull_span_display": _format_duration(pull_span_minutes),
                "avg_pull_gap_display": _format_duration(avg_pull_gap_minutes),
                "first_pull_display": _format_clock(first_pull),
                "last_pull_display": _format_clock(last_pull),
                "status": "Pulled" if not device_pulls.empty else "No pull activity",
            }
        )

    detail_df = pd.DataFrame(device_rows).sort_values(
        ["pull_qty", "pull_completion_minutes"], ascending=[False, True], na_position="last"
    )
    active_pull_df = detail_df[detail_df["pull_lines"] > 0].copy()
    active_refill_df = detail_df[detail_df["refill_lines"] > 0].copy()
    first_pull = active_pull_df["first_pull"].min() if not active_pull_df.empty else pd.NaT
    last_pull = active_pull_df["last_pull"].max() if not active_pull_df.empty else pd.NaT
    pull_completion_minutes = (
        max((last_pull - scheduled_ts).total_seconds() / 60, 0)
        if pd.notna(last_pull)
        else np.nan
    )
    pull_span_minutes = (
        max((last_pull - first_pull).total_seconds() / 60, 0)
        if pd.notna(first_pull) and pd.notna(last_pull)
        else np.nan
    )
    avg_pull_gap_minutes = _average_gap_minutes(pull_win)

    summary = {
        "Drop": drop["label"],
        "Scheduled": drop["time"],
        "Scheduled Devices": len(detail_df),
        "Devices Loaded": int((detail_df["pull_lines"] > 0).sum()),
        "Full Drop Devices": int((detail_df["drop_type"] == "Full Drop").sum()),
        "Stockout Devices": int((detail_df["drop_type"] == "Stockouts Only").sum()),
        "Units Loaded": float(detail_df["loaded_qty"].sum()),
        "Pull Demand": float(detail_df["pull_qty"].sum()),
        "Pull Lines": int(detail_df["pull_lines"].sum()),
        "Refill Lines": int(detail_df["refill_lines"].sum()),
        "Distinct Meds": int(active_refill_df["meds_loaded"].sum()) if not active_refill_df.empty else 0,
        "First Pull": _format_clock(first_pull),
        "Last Pull": _format_clock(last_pull),
        "Pull Completion": _format_duration(pull_completion_minutes),
        "Pull Completion Minutes": pull_completion_minutes,
        "Pull Span": _format_duration(pull_span_minutes),
        "Pull Span Minutes": pull_span_minutes,
        "Avg Pull Gap": _format_duration(avg_pull_gap_minutes),
        "Avg Pull Gap Minutes": avg_pull_gap_minutes,
    }
    return summary, detail_df, pull_win, refill_win


@st.cache_data(ttl=300)
def build_window_trend_df(date_values):
    trend_rows = []
    for sel_day in date_values:
        pulls = load_pyxis_pulls(sel_day)
        refills = load_refills(sel_day)
        for drop in get_schedule(sel_day):
            summary, _, _, _ = build_drop_timing_summary(drop, sel_day, pulls, refills)
            trend_rows.append(
                {
                    "Date": sel_day,
                    "Weekday": pd.Timestamp(sel_day).strftime("%A"),
                    **summary,
                }
            )
    if not trend_rows:
        return pd.DataFrame()
    return pd.DataFrame(trend_rows)


drop_summaries = []
drop_details = {}
drop_pull_windows = {}
drop_refill_windows = {}
for drop in schedule:
    summary, detail_df, pull_win, refill_win = build_drop_timing_summary(drop, sel_date, df_pulls, df_refills)
    drop_summaries.append(summary)
    drop_details[drop["label"]] = detail_df
    drop_pull_windows[drop["label"]] = pull_win
    drop_refill_windows[drop["label"]] = refill_win

summary_df = pd.DataFrame(drop_summaries)
trend_df = build_window_trend_df(tuple(available_dates))

st.info(
    "This stripped-down view answers two questions first: how much pull demand sits on each drop, "
    "and how long the carousel pull portion took based on pull transactions."
)
st.caption(
    "Pull timing is measured from the scheduled drop time to the last pull transaction in that drop window. "
    "Refill fields are left in as reference, but the timing clocks below are now pull-side only."
)

day_units = float(summary_df["Units Loaded"].sum()) if not summary_df.empty else 0.0
day_pull_lines = int(summary_df["Pull Lines"].sum()) if not summary_df.empty else 0
drop_count = len(summary_df)
avg_completion = summary_df["Pull Completion Minutes"].dropna().mean() if not summary_df.empty else np.nan

k1, k2, k3, k4 = st.columns(4)
k1.metric("Drops Today", f"{drop_count}")
k2.metric("Pull Demand", f"{float(summary_df['Pull Demand'].sum()) if not summary_df.empty else 0.0:,.0f}")
k3.metric("Pull Lines", f"{day_pull_lines:,}")
k4.metric("Avg Pull Completion", _format_duration(avg_completion))

if summary_df.empty or day_pull_lines == 0:
    st.warning("No pull activity was found for this date in Carousel Drop Tracker.")
else:
    overview_tab, trend_tab, detail_tab, raw_tab = st.tabs(["Drop Overview", "Across Days", "Device Breakdown", "Raw Activity"])

    with overview_tab:
        st.markdown("### Drop Overview")
        overview_display = summary_df[
            [
                "Drop",
                "Scheduled",
                "Scheduled Devices",
                "Devices Loaded",
                "Pull Demand",
                "Pull Lines",
                "Units Loaded",
                "Refill Lines",
                "First Pull",
                "Last Pull",
                "Pull Completion",
                "Pull Span",
                "Avg Pull Gap",
            ]
        ].copy()
        st.dataframe(
            overview_display,
            hide_index=True,
            width="stretch",
            column_config={
                "Units Loaded": st.column_config.NumberColumn("Units Loaded", format="%.0f"),
                "Pull Demand": st.column_config.NumberColumn("Pull Demand", format="%.0f"),
            },
        )

        c1, c2 = st.columns(2)
        with c1:
            units_fig = px.bar(
                summary_df,
                x="Drop",
                y="Pull Demand",
                text="Pull Demand",
                title="Pull Demand by Drop",
                color="Drop",
            )
            units_fig.update_traces(texttemplate="%{y:.0f}", textposition="outside")
            units_fig.update_layout(showlegend=False, margin=dict(l=0, r=0, t=60, b=0))
            st.plotly_chart(units_fig, width="stretch")
        with c2:
            completion_chart = summary_df.dropna(subset=["Pull Completion Minutes"]).copy()
            completion_fig = px.bar(
                completion_chart,
                x="Drop",
                y="Pull Completion Minutes",
                text=completion_chart["Pull Completion"].tolist(),
                title="Pull Completion Time by Drop",
                color="Drop",
            )
            completion_fig.update_traces(textposition="outside")
            completion_fig.update_layout(showlegend=False, margin=dict(l=0, r=0, t=60, b=0), yaxis_title="Minutes")
            st.plotly_chart(completion_fig, width="stretch")

    with trend_tab:
        st.markdown("### Across Days")
        st.caption("Use this to spot high days, low days, and the typical median level across the selected window.")

        if trend_df.empty:
            st.info("No multi-day pull trend data was found in the selected window.")
        else:
            metric_options = {
                "Pull Demand": "Pull Demand",
                "Pull Completion": "Pull Completion Minutes",
                "Pull Span": "Pull Span Minutes",
                "Pull Lines": "Pull Lines",
            }
            selected_metric_label = st.radio(
                "Trend metric",
                options=list(metric_options.keys()),
                horizontal=True,
                key="carousel_trend_metric",
            )
            metric_col = metric_options[selected_metric_label]

            trend_work = trend_df.copy()
            trend_work["DateLabel"] = pd.to_datetime(trend_work["Date"]).dt.strftime("%m/%d")

            daily_stats = (
                trend_work.groupby("Date", as_index=False)[metric_col]
                .agg(["max", "min", "median"])
                .reset_index()
                .rename(columns={"max": "Daily High", "min": "Daily Low", "median": "Daily Median"})
            )
            daily_stats["DateLabel"] = pd.to_datetime(daily_stats["Date"]).dt.strftime("%m/%d")

            trend_table = trend_work.pivot_table(
                index=["Date", "Weekday"],
                columns="Drop",
                values=metric_col,
                aggfunc="sum",
            ).reset_index()
            trend_table = trend_table.merge(daily_stats[["Date", "Daily High", "Daily Low", "Daily Median"]], on="Date", how="left")
            trend_table["Date"] = pd.to_datetime(trend_table["Date"]).dt.strftime("%m/%d/%Y")
            st.dataframe(trend_table, hide_index=True, width="stretch")

            trend_fig = go.Figure()
            for drop_name in trend_work["Drop"].dropna().unique():
                drop_slice = trend_work[trend_work["Drop"] == drop_name].sort_values("Date")
                trend_fig.add_trace(
                    go.Scatter(
                        x=drop_slice["DateLabel"],
                        y=drop_slice[metric_col],
                        mode="lines+markers",
                        name=drop_name,
                    )
                )

            trend_fig.add_trace(
                go.Scatter(
                    x=daily_stats["DateLabel"],
                    y=daily_stats["Daily Median"],
                    mode="lines",
                    name="Median",
                    line=dict(color="#facc15", width=3, dash="dash"),
                )
            )
            trend_fig.add_trace(
                go.Scatter(
                    x=daily_stats["DateLabel"],
                    y=daily_stats["Daily High"],
                    mode="lines",
                    name="Daily High",
                    line=dict(color="#22c55e", width=2, dash="dot"),
                )
            )
            trend_fig.add_trace(
                go.Scatter(
                    x=daily_stats["DateLabel"],
                    y=daily_stats["Daily Low"],
                    mode="lines",
                    name="Daily Low",
                    line=dict(color="#ef4444", width=2, dash="dot"),
                )
            )
            trend_fig.update_layout(
                title=f"{selected_metric_label} Across Days",
                xaxis_title="Date",
                yaxis_title=selected_metric_label,
                margin=dict(l=0, r=0, t=60, b=0),
                legend=dict(orientation="h", yanchor="bottom", y=1.02),
            )
            st.plotly_chart(trend_fig, width="stretch")

            high_row = trend_work.loc[trend_work[metric_col].idxmax()] if not trend_work.empty else None
            low_row = trend_work.loc[trend_work[metric_col].idxmin()] if not trend_work.empty else None
            median_value = trend_work[metric_col].median() if not trend_work.empty else np.nan

            h1, h2, h3 = st.columns(3)
            if high_row is not None:
                h1.metric(
                    "Highest Observed",
                    f"{high_row[metric_col]:,.0f}" if selected_metric_label in {"Pull Demand", "Pull Lines"} else _format_duration(high_row[metric_col]),
                    f"{high_row['Drop']} on {pd.Timestamp(high_row['Date']).strftime('%m/%d')}",
                )
            if low_row is not None:
                h2.metric(
                    "Lowest Observed",
                    f"{low_row[metric_col]:,.0f}" if selected_metric_label in {"Pull Demand", "Pull Lines"} else _format_duration(low_row[metric_col]),
                    f"{low_row['Drop']} on {pd.Timestamp(low_row['Date']).strftime('%m/%d')}",
                )
            h3.metric(
                "Median Across Window",
                f"{median_value:,.0f}" if selected_metric_label in {"Pull Demand", "Pull Lines"} else _format_duration(median_value),
            )

    with detail_tab:
        st.markdown("### Device Breakdown")
        selected_drop = st.radio(
            "Choose a drop",
            options=summary_df["Drop"].tolist(),
            horizontal=True,
            key="carousel_drop_focus",
        )
        selected_summary = summary_df.loc[summary_df["Drop"] == selected_drop].iloc[0]
        selected_detail = drop_details[selected_drop].copy()

        d1, d2, d3, d4 = st.columns(4)
        d1.metric("Pull Demand", f"{selected_summary['Pull Demand']:,.0f}")
        d2.metric("Devices Loaded", f"{int(selected_summary['Devices Loaded'])}")
        d3.metric("Pull Completion", selected_summary["Pull Completion"])
        d4.metric("Pull Span", selected_summary["Pull Span"])

        st.caption(
            f"{selected_drop} started at {selected_summary['Scheduled']} and its last pull transaction was "
            f"{selected_summary['Last Pull']}."
        )

        area_options = sorted(selected_detail["area"].unique())
        selected_areas = st.multiselect(
            "Filter devices by area",
            options=area_options,
            default=area_options,
            key=f"carousel_area_filter_{selected_drop}",
        )
        if selected_areas:
            selected_detail = selected_detail[selected_detail["area"].isin(selected_areas)]

        device_view = selected_detail[
            [
                "device",
                "area",
                "drop_type",
                "status",
                "pull_qty",
                "pull_lines",
                "first_pull_display",
                "last_pull_display",
                "pull_completion_display",
                "pull_span_display",
                "avg_pull_gap_display",
                "loaded_qty",
                "refill_lines",
            ]
        ].rename(
            columns={
                "device": "Device",
                "area": "Area",
                "drop_type": "Drop Type",
                "status": "Status",
                "pull_qty": "Pull Demand",
                "pull_lines": "Pull Lines",
                "first_pull_display": "First Pull",
                "last_pull_display": "Last Pull",
                "pull_completion_display": "Pull Completion",
                "pull_span_display": "Pull Span",
                "avg_pull_gap_display": "Avg Pull Gap",
                "loaded_qty": "Units Loaded",
                "refill_lines": "Refill Lines",
            }
        )
        st.dataframe(
            device_view,
            hide_index=True,
            width="stretch",
            column_config={
                "Units Loaded": st.column_config.NumberColumn("Units Loaded", format="%.0f"),
                "Pull Demand": st.column_config.NumberColumn("Pull Demand", format="%.0f"),
            },
        )

        device_chart_df = selected_detail[selected_detail["pull_qty"] > 0].nlargest(15, "pull_qty")
        if not device_chart_df.empty:
            device_fig = px.bar(
                device_chart_df.sort_values("pull_qty"),
                y="device",
                x="pull_qty",
                orientation="h",
                color="area",
                text="pull_qty",
                title=f"{selected_drop}: Top Devices by Pull Demand",
                color_discrete_map=AREA_COLOR,
            )
            device_fig.update_traces(texttemplate="%{x:.0f}", textposition="outside")
            device_fig.update_layout(margin=dict(l=0, r=20, t=60, b=0), yaxis_title="")
            st.plotly_chart(device_fig, width="stretch")

        st.download_button(
            f"Export {selected_drop} device breakdown",
            data=to_excel_bytes(device_view),
            file_name=f"carousel_drop_{selected_drop.replace(' ', '_').lower()}_{sel_date}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key=f"download_{selected_drop}",
        )

    with raw_tab:
        st.markdown("### Raw Activity")
        raw_drop = st.selectbox("Raw activity for drop", options=summary_df["Drop"].tolist(), key="carousel_raw_drop")
        r1, r2 = st.columns(2)
        with r1:
            st.markdown("**Pull Demand Lines**")
            pull_raw = drop_pull_windows[raw_drop][["dt", "destination", "user_name", "med_id", "med_desc", "qty"]].copy()
            pull_raw = pull_raw.rename(
                columns={
                    "dt": "Time",
                    "destination": "Device",
                    "user_name": "User",
                    "med_id": "Med ID",
                    "med_desc": "Medication",
                    "qty": "Qty",
                }
            ).sort_values("Time")
            st.dataframe(pull_raw, hide_index=True, width="stretch")
        with r2:
            st.markdown("**Refill Activity Lines**")
            refill_raw = drop_refill_windows[raw_drop][["dt", "device", "user_name", "med_id", "med_desc", "qty"]].copy()
            refill_raw = refill_raw.rename(
                columns={
                    "dt": "Time",
                    "device": "Device",
                    "user_name": "Tech",
                    "med_id": "Med ID",
                    "med_desc": "Medication",
                    "qty": "Qty",
                }
            ).sort_values("Time")
            st.dataframe(refill_raw, hide_index=True, width="stretch")
