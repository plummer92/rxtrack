import streamlit as st
import pandas as pd
from App import engine, normalize_name

st.set_page_config(page_title="Med Audit Trail", page_icon="🔍", layout="wide")

st.header("🔍 Medication Audit Trail")
st.caption("Enter a medication name to see the full 'Last Touch' history across all devices.")

# 1. User Input
med_search = st.text_input("Enter Med Description (e.g., Heparin):", "heparin 5000")

if med_search:
    # 2. Direct Database Search (Bypassing sidebar filters)
    query = f"""
        SELECT dt, user_name, device, event_type, qty, beginning_qty, ending_qty
        FROM events 
        WHERE med_desc ILIKE '%%{med_search}%%'
        ORDER BY dt DESC
    """
    
    with st.spinner("Searching global history..."):
        df_audit = pd.read_sql(query, engine)

    if not df_audit.empty:
        # 3. Apply Naming Logic
        df_audit['tech_name'] = df_audit['user_name'].apply(normalize_name)
        
        # 4. Display the Timeline
        st.success(f"Found {len(df_audit)} transactions for '{med_search}'")
        
        st.dataframe(
            df_audit[['dt', 'tech_name', 'device', 'event_type', 'qty', 'beginning_qty', 'ending_qty']],
            width='stretch',
            column_config={
                "dt": st.column_config.DatetimeColumn("Timestamp", format="MM/DD HH:mm:ss"),
                "tech_name": "Technician",
                "beginning_qty": "Start Count",
                "ending_qty": "End Count"
            }
        )
    else:
        st.warning(f"No records found for '{med_search}'. Check spelling or ensure data is uploaded.")
