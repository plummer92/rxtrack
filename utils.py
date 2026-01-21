# utils.py
import streamlit as st
import pandas as pd
import numpy as np
import hashlib
import psycopg2
from psycopg2.extras import execute_batch
from datetime import datetime, timedelta, date
import re
import contextlib

# --- CONSTANTS ---
NARC_TERMS = ["OXYCODONE", "MORPHINE", "FENTANYL", "HYDROMORPHONE", "HYDROCODONE", "LORAZEPAM", "MIDAZOLAM", "DIAZEPAM", "ALPRAZOLAM", "CODEINE", "METHADONE", "KETAMINE", "TRAMADOL", "ZOLPIDEM", "PHENOBARBITAL", "BUPRENORPHINE", "LACOSAMIDE", "VIMPAT", "PREGABALIN", "LYRICA", "CHLORDIAZEPOXIDE", "LIBRIUM", "CLONAZEPAM", "KLONOPIN"]
ADMIN_USERS = ['emily', 'joe', 'krista']
NAME_MAPPINGS = {"phi": "ali", "ho": "ali", "rebekah": "bekah", "nugent": "kathy", "kathleen": "kathy", "spain": "dee", "deloris": "dee", "jabusch": "dan", "daniel": "dan", "nicholas": "nick"}

# --- DB HELPERS ---
@contextlib.contextmanager
def db_cursor():
    conn = None
    try:
        conn = psycopg2.connect(st.secrets["neon"]["db_url"])
        cur = conn.cursor()
        yield conn, cur
    except Exception as e:
        st.error(f"DB Error: {e}")
        raise e
    finally:
        if conn: conn.close()

def execute_statement(sql, params, batch=False, table_name="Data"):
    try:
        with db_cursor() as (conn, cur):
            if batch: execute_batch(cur, sql, params, page_size=2000)
            else: cur.execute(sql, params)
            conn.commit()
            st.toast(f"Saved to {table_name}", icon="💾")
    except Exception as e: st.error(f"Error: {e}")

# --- DATA LOADERS ---
@st.cache_data(ttl=300)
def load_data(start_date, end_date):
    queries = {
        "events": "SELECT pk, user_name, device, med_id, med_desc, event_type, dt, qty, discrepancy_qty, discrepancy_reason FROM events WHERE dt::date BETWEEN %s AND %s",
        "pharm": "SELECT pk, priority, dt, med_id, med_desc, destination, user_name, qty FROM pharmacy_orders WHERE dt::date BETWEEN %s AND %s",
        # ... Add other queries here ...
    }
    # ... (Insert the rest of your load_data logic here) ...
    # For now, return empty DFs to prevent crashes if DB isn't ready
    return pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

# --- HELPERS ---
def seconds_to_mmss(seconds):
    if pd.isna(seconds) or seconds < 0: return "-"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}h {m}m {s}s" if h > 0 else f"{m}m {s}s"

def normalize_name(full_name):
    s = str(full_name).strip().lower()
    first_name = s.split(",")[1].strip().split(" ")[0] if "," in s else s.split(" ")[0]
    for key, val in NAME_MAPPINGS.items():
        if key in first_name: return val
    return first_name
