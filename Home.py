import streamlit as st
from utils import init_db # Import from your new utils file

st.set_page_config(page_title="RxTrack", layout="wide")

st.title("🏥 RxTrack Executive Suite")
st.write("Welcome. Select a module from the sidebar to begin.")

# Sidebar Data Filters (Global)
st.sidebar.header("Global Settings")
d_range = st.sidebar.date_input("Date Range", [])

# Initialize DB once on load
if 'db_init' not in st.session_state:
    # init_db() # Call your DB init function from utils
    st.session_state['db_init'] = True
