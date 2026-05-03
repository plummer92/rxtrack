import contextlib
import hashlib
import re

import pandas as pd
import psycopg2
import streamlit as st
from psycopg2.extras import execute_batch
from sqlalchemy import create_engine, text


engine = create_engine(
    st.secrets["neon"]["db_url"],
    pool_pre_ping=True,
    pool_recycle=300,
)

_DEFAULT_ADMIN_USERS = {"emily", "joe", "krista"}

NAME_MAPPINGS = {
    "phi": "ali", "ho": "ali", "rebekah": "bekah",
    "nugent": "kathy", "kathleen": "kathy",
    "spain": "dee", "deloris": "dee",
    "jabusch": "dan", "daniel": "dan",
    "nicholas": "nick",
}

AMBIGUOUS_NAMES = {
    "melissa", "emily", "sarah", "megan", "erin", "kyle",
    "jessica", "andy", "heather", "michelle", "taylor",
}


@st.cache_data(ttl=300)
def load_admin_users():
    """Load admin usernames from DB. Falls back to defaults if table is empty or unavailable."""
    try:
        with engine.connect() as conn:
            result = conn.execute(text("SELECT username FROM admin_users"))
            users = {row[0].strip().lower() for row in result if row[0]}
        return users if users else _DEFAULT_ADMIN_USERS
    except Exception:
        return _DEFAULT_ADMIN_USERS


@contextlib.contextmanager
def db_cursor():
    """Context manager for database connections."""
    conn = None
    try:
        conn = psycopg2.connect(st.secrets["neon"]["db_url"])
        cur = conn.cursor()
        yield conn, cur
    except Exception as e:
        st.error(f"Database Connection Error: {e}")
        raise e
    finally:
        if conn:
            conn.close()


def execute_statement(sql, params, batch=False, table_name="Data"):
    """Executes INSERT/UPDATE statements."""
    try:
        def _sql_safe(value):
            if pd.isna(value):
                return None
            return value

        def _normalize_params(payload):
            if batch:
                return [
                    {k: _sql_safe(v) for k, v in row.items()}
                    for row in payload
                ]
            if isinstance(payload, dict):
                return {k: _sql_safe(v) for k, v in payload.items()}
            return payload

        params = _normalize_params(params)
        with db_cursor() as (conn, cur):
            if batch:
                execute_batch(cur, sql, params, page_size=2000)
            else:
                cur.execute(sql, params)
            conn.commit()
            st.toast(f"Successfully processed {len(params)} records for {table_name}!")
    except Exception as e:
        st.error(f"Error executing {table_name}: {e}")


def seconds_to_mmss(seconds):
    if pd.isna(seconds) or seconds < 0:
        return "-"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}h {m}m {s}s"
    if m > 0:
        return f"{m}m {s}s"
    return f"{s}s"


def generate_pk(row):
    subset = [str(x) for x in row.values if pd.notnull(x)]
    row_str = "|".join(subset)
    return hashlib.sha256(row_str.encode()).hexdigest()


def normalize_identifier_text(value):
    """Normalize identifiers so Excel-style numeric text like 528661992.0 becomes 528661992."""
    if pd.isna(value):
        return None
    text_value = str(value).strip()
    if text_value in {"", "nan", "None"}:
        return None
    if re.fullmatch(r"-?\d+\.0+", text_value):
        return text_value.split(".")[0]
    return text_value


def normalize_name(full_name):
    if not full_name or pd.isna(full_name):
        return "unknown"

    s = str(full_name).strip().lower()
    if not s or s == ",":
        return "unknown"

    first_name, last_initial = "", ""

    if "," in s:
        parts = s.split(",")
        if len(parts) >= 2:
            last_name_part = parts[0].strip()
            first_name_parts = parts[1].strip().split(" ")
            if first_name_parts and len(first_name_parts[0]) > 0:
                first_name = first_name_parts[0]
            if len(last_name_part) > 0:
                last_initial = last_name_part[0]
    else:
        parts = s.split(" ")
        if len(parts) > 0:
            first_name = parts[0]
            if len(parts) > 1 and len(parts[1]) > 0:
                last_initial = parts[1][0]

    for key, val in NAME_MAPPINGS.items():
        if key in first_name:
            first_name = val
            break

    if first_name in AMBIGUOUS_NAMES and last_initial:
        return f"{first_name} {last_initial}"
    return first_name


def parse_shift_start(date_obj, shift_str):
    if not shift_str or pd.isna(shift_str):
        return None

    s = str(shift_str).lower().strip()

    m_time = re.search(r"(\d{1,2}):(\d{2})", s)
    if m_time:
        h, m = int(m_time.group(1)), int(m_time.group(2))
        if "p" in s and h < 12:
            h += 12
        if "a" in s and h == 12:
            h = 0
        try:
            return pd.to_datetime(f"{date_obj} {h:02d}:{m:02d}")
        except Exception:
            return None

    m_ampm = re.search(r"(\d{1,2})\s*([ap])", s)
    if m_ampm:
        h = int(m_ampm.group(1))
        ampm = m_ampm.group(2)
        if ampm == "p" and h < 12:
            h += 12
        if ampm == "a" and h == 12:
            h = 0
        try:
            return pd.to_datetime(f"{date_obj} {h:02d}:00")
        except Exception:
            return None

    m_mil = re.search(r"(\d{4})", s)
    if m_mil:
        val = int(m_mil.group(1))
        if 0 <= val <= 2400:
            h, m = divmod(val, 100)
            try:
                return pd.to_datetime(f"{date_obj} {h:02d}:{m:02d}")
            except Exception:
                return None

    return None
