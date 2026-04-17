import streamlit as st
import pandas as pd
from sqlalchemy import text
import App

st.set_page_config(page_title="Admin & Mapping", page_icon="⚙️", layout="wide")
if hasattr(App, "render_sidebar_chrome"):
    App.render_sidebar_chrome()
else:
    App.render_sidebar()

engine = App.engine
load_admin_users = App.load_admin_users
_DEFAULT_ADMIN_USERS = App._DEFAULT_ADMIN_USERS

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

    try:
        df_master = pd.read_csv(uploaded_file, header=1)
    except Exception as e:
        st.error(f"❌ Could not read CSV: {e}")
        st.stop()

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

    # -------------------------------------------------
    # 2b️⃣ Schema Validation — stop before any DB write
    # -------------------------------------------------

    missing_cols = [c for c in required_cols if c not in df_master.columns]
    if missing_cols:
        st.error(
            f"❌ Uploaded file is missing required columns: **{', '.join(missing_cols)}**\n\n"
            f"Found columns: `{', '.join(df_master.columns.tolist())}`\n\n"
            "The table was **not** modified. Fix the file and re-upload."
        )
        st.stop()

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

        try:
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
                    conn,
                    if_exists="append",
                    index=False,
                    method="multi"
                )
            st.success(f"✅ Master mapping uploaded successfully ({len(df_master):,} rows).")
            st.cache_data.clear()
        except Exception as e:
            st.error(f"❌ Upload failed: {e}\n\nThe table may be in an inconsistent state — re-upload to restore.")

st.divider()

# =============================================================
# 📊 CYCLE COUNT VARIANCE UPLOAD
# =============================================================

st.header("📊 Cycle Count Variance Upload")
st.caption("Upload the Cycle Count Variance Report CSV to track inventory shrinkage and gain events.")

variance_file = st.file_uploader(
    "Upload Cycle Count Variance Report CSV",
    type=["csv"],
    key="variance_upload"
)

if variance_file:

    # -------------------------------------------------
    # 1️⃣ Load CSV (skip first header row)
    # -------------------------------------------------

    df_var = pd.read_csv(variance_file, header=1)

    # -------------------------------------------------
    # 2️⃣ Standardize Column Names
    # -------------------------------------------------

    df_var.columns = (
        df_var.columns
        .str.strip()
        .str.lower()
        .str.replace(" ", "_")
        .str.replace("/", "_")
    )

    df_var = df_var.rename(columns={
        "grptype":           "variance_type",
        "date_time":         "dt",
        "item_id":           "med_id",
        "description":       "med_desc",
        "starting_qoh":      "starting_qty",
        "new_qoh":           "new_qty",
        "quantity_variance": "qty_variance",
        "cost":              "unit_cost",
        "extended_cost":     "extended_cost",
        "user":              "user_name",
    })

    required_cols = [
        "variance_type", "dt", "med_id", "med_desc",
        "starting_qty", "new_qty", "qty_variance",
        "unit_cost", "extended_cost", "user_name"
    ]

    for col in required_cols:
        if col not in df_var.columns:
            df_var[col] = None

    # -------------------------------------------------
    # 3️⃣ Clean & Normalize
    # -------------------------------------------------

    df_var = df_var.dropna(subset=["med_id", "dt"])

    df_var["med_id"] = (
        df_var["med_id"].astype(str).str.strip().str.upper()
    )
    df_var["med_desc"] = (
        df_var["med_desc"].astype(str).str.strip()
    )
    df_var["user_name"] = (
        df_var["user_name"].astype(str).str.strip()
    )
    df_var["dt"] = pd.to_datetime(df_var["dt"], errors="coerce")
    df_var = df_var.dropna(subset=["dt"])

    for col in ["starting_qty", "new_qty", "qty_variance", "unit_cost", "extended_cost"]:
        df_var[col] = pd.to_numeric(df_var[col], errors="coerce").fillna(0)

    df_var["dt"] = df_var["dt"].astype(str)

    # -------------------------------------------------
    # 4️⃣ Generate PK
    # -------------------------------------------------

    import hashlib
    def gen_pk(row):
        s = "|".join([str(row["dt"]), str(row["med_id"]), str(row["qty_variance"]), str(row["user_name"])])
        return hashlib.sha256(s.encode()).hexdigest()

    df_var["pk"] = df_var.apply(gen_pk, axis=1)

    # -------------------------------------------------
    # 5️⃣ Preview
    # -------------------------------------------------

    st.subheader("Preview")
    st.dataframe(df_var[required_cols].head(10), use_container_width=True)

    col1, col2, col3 = st.columns(3)
    col1.metric("Total Records", len(df_var))
    col2.metric("Shrinkage Events", int((df_var["qty_variance"] < 0).sum()))
    col3.metric("Gain Events",      int((df_var["qty_variance"] > 0).sum()))

    # -------------------------------------------------
    # 6️⃣ Upload to Neon
    # -------------------------------------------------

    if st.button("🚀 Upload Variance Data"):
        from sqlalchemy import text as sqla_text
        from App import engine

        with engine.begin() as conn:
            conn.execute(sqla_text("""
                CREATE TABLE IF NOT EXISTS cycle_count_variances (
                    pk              TEXT PRIMARY KEY,
                    variance_type   TEXT,
                    dt              TIMESTAMP,
                    med_id          TEXT,
                    med_desc        TEXT,
                    starting_qty    FLOAT,
                    new_qty         FLOAT,
                    qty_variance    FLOAT,
                    unit_cost       FLOAT,
                    extended_cost   FLOAT,
                    user_name       TEXT
                )
            """))

        df_upload = df_var[["pk"] + required_cols].copy()
        df_upload.to_sql(
            "cycle_count_variances",
            engine,
            if_exists="append",
            index=False,
            method="multi"
        )

        st.success(f"✅ Successfully uploaded {len(df_upload)} variance records.")
        st.cache_data.clear()

st.divider()

# =============================================================
# 👤 ADMIN USER MANAGEMENT
# =============================================================

st.header("👤 Admin User Management")
st.caption("Admin users are excluded from tardiness and workforce analytics.")

current_admins = load_admin_users()

col_left, col_right = st.columns(2)

with col_left:
    st.subheader("Current Admin Users")
    if current_admins:
        for u in sorted(current_admins):
            c1, c2 = st.columns([3, 1])
            c1.write(u)
            if c2.button("Remove", key=f"remove_{u}"):
                if u in _DEFAULT_ADMIN_USERS:
                    st.warning(f"⚠️ Cannot remove built-in admin '{u}'.")
                else:
                    try:
                        with engine.begin() as conn:
                            conn.execute(text("DELETE FROM admin_users WHERE username = :u"), {"u": u})
                        st.success(f"Removed '{u}'.")
                        st.cache_data.clear()
                        st.rerun()
                    except Exception as e:
                        st.error(f"❌ Failed to remove user: {e}")
    else:
        st.info("No admin users configured.")

with col_right:
    st.subheader("Add Admin User")
    new_admin = st.text_input("Username (lowercase)", placeholder="e.g. johndoe")
    display_name = st.text_input("Display Name (optional)", placeholder="e.g. John Doe")
    if st.button("➕ Add Admin User"):
        username = new_admin.strip().lower()
        if not username:
            st.warning("Enter a username.")
        elif username in current_admins:
            st.warning(f"'{username}' is already an admin.")
        else:
            try:
                with engine.begin() as conn:
                    conn.execute(text("""
                        INSERT INTO admin_users (username, display_name)
                        VALUES (:u, :d)
                        ON CONFLICT (username) DO NOTHING
                    """), {"u": username, "d": display_name.strip() or None})
                st.success(f"✅ Added '{username}' as admin.")
                st.cache_data.clear()
                st.rerun()
            except Exception as e:
                st.error(f"❌ Failed to add user: {e}")
