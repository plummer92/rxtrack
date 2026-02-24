import streamlit as st
import pandas as pd
from sqlalchemy import text
from App import engine

st.header("📥 Master Carousel Mapping Upload")
st.caption("Upload Item Location Report to update master carousel assignments.")

uploaded_file = st.file_uploader(
    "Upload Item Location Report CSV",
    type=["csv"]
)

if uploaded_file:

    # -------------------------------------------------
    # 1️⃣ Load CSV (skip first header row)
    # -------------------------------------------------

    df_master = pd.read_csv(uploaded_file, header=1)

    # -------------------------------------------------
    # 2️⃣ Standardize Column Names
    # -------------------------------------------------

    df_master.columns = (
        df_master.columns
        .str.strip()
        .str.lower()
        .str.replace(" ", "_")
    )

    df_master = df_master.rename(columns={
        "description": "med_desc",
        "drug_name": "drug_name",
        "trade_name": "trade_name",
        "med_id": "med_id",
        "location": "carousel_location"
    })

    required_cols = [
        "med_id",
        "med_desc",
        "drug_name",
        "trade_name",
        "carousel_location"
    ]

    df_master = df_master[required_cols]

    # -------------------------------------------------
    # 3️⃣ Clean & Normalize
    # -------------------------------------------------

    df_master = df_master.dropna(subset=["med_id", "med_desc"])

    df_master["med_id"] = (
        df_master["med_id"]
        .astype(str)
        .str.strip()
        .str.upper()
    )

    df_master["med_desc"] = (
        df_master["med_desc"]
        .astype(str)
        .str.strip()
        .str.upper()
    )

    df_master["carousel_location"] = (
        df_master["carousel_location"]
        .astype(str)
        .str.strip()
        .str.upper()
    )

    # -------------------------------------------------
    # 4️⃣ Remove Duplicates
    # -------------------------------------------------

    df_master = (
        df_master
        .sort_values("carousel_location")
        .drop_duplicates(subset=["med_id"], keep="last")
    )

    st.subheader("Preview")
    st.dataframe(df_master.head(), use_container_width=True)

    # -------------------------------------------------
    # 5️⃣ Replace Table Safely
    # -------------------------------------------------

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
            index=False,
            method="multi"
        )

        st.success("✅ Master mapping uploaded successfully.")
