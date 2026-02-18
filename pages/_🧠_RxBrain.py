import streamlit as st

st.header("🧠 RxBrain Import Diagnostic")

st.write("Attempting to import App module safely...")

try:
    import App
    
    st.success("✅ App module imported successfully.")
    
    st.write("Available attributes inside App:")
    st.write(dir(App))
    
    # Check critical symbols
    checks = [
        "parse_shift_start",
        "normalize_name",
        "load_data",
        "ADMIN_USERS",
        "engine"
    ]
    
    for item in checks:
        if hasattr(App, item):
            st.success(f"✅ {item} exists in App.")
        else:
            st.error(f"❌ {item} is MISSING from App.")

except Exception as e:
    st.error("❌ App module failed during import.")
    st.exception(e)
