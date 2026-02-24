import streamlit as st
import pandas as pd
from sqlalchemy import text
from App import engine  # make sure this is your SQLAlchemy engine

st.header("📥 Master Carousel Mapping Upload")
st.caption("Upload Item Location Report to update master carousel assignments.")

uploaded_file = st.file_uploader(
    "Upload Item Location Report CSV",
    type=["csv"]
)

if uploaded_file:

    # ----------------------------
    # 1️⃣ Load CSV (skip header row)
    # ----------------------------
    df_master = pd.read_csv(uploaded_file, header=1)

    # ----------------------------
    # 2️⃣ Rename Columns Cleanly
    # ----------------------------
    df_master = df_master.rename(columns={
        "Description": "med_desc",
        "Drug Name": "drug_name",
        "Trade Name": "trade_name",
        "Med ID": "med_id",
        "Location": "carousel_location"
    })

    # Keep only what we need
    df_master = df_master[[
        "med_id",
        "med_desc",
        "drug_name",
        "trade_name",
        "carousel_location"
    ]]

    # Drop rows missing med_id
    df_master = df_master.dropna(subset=["med_id"])

    # Strip whitespace
    df_master["med_id"] = df_master["med_id"].astype(str).str.strip()
    df_master["carousel_location"] = df_master["carousel_location"].astype(str).str.strip()

    st.subheader("Preview")
    st.dataframe(df_master.head(), use_container_width=True)

    st.write(f"Total rows detected: {len(df_master)}")

    # ----------------------------
    # 3️⃣ Upload To Neon
    # ----------------------------
    if st.button("🚀 Replace Master Mapping Table"):

        with engine.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS carousel_master_mapping"))

            conn.execute(text("""
                CREATE TABLE carousel_master_mapping (
                    med_id TEXT PRIMARY KEY,
                    med_desc TEXT,
                    drug_name TEXT,
                    trade_name TEXT,
                    carousel_location TEXT
                )
            """))

        df_master.to_sql(
            "carousel_master_mapping",
            engine,
            if_exists="append",
            index=False
        )

        st.success("✅ Master mapping uploaded successfully.")
