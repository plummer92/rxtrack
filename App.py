# --- TAB 12: ATTENDANCE AUDIT ---
with tab_attend:
    st.markdown("### 📋 Schedule vs. Reality Audit")
    
    if not df_sched.empty:
        if not df.empty:
            # --- 1. INTELLIGENT NAME MATCHING ---
            
            # Helper to extract a "Match Key" (First Name) from Event Data (Format: "Last, First")
            def get_event_name_key(full_name):
                s = str(full_name).strip().lower()
                if "," in s:
                    # "Epps, Raeven" -> "raeven"
                    parts = s.split(",")
                    if len(parts) >= 2:
                        return parts[1].strip().split(" ")[0] 
                # Fallback: just take first word
                return s.split(" ")[0]

            df['match_key'] = df['user_name'].apply(get_event_name_key)
            
            # Group events by Date and Match Key
            worked_days = df.groupby([df['dt'].dt.date, 'match_key']).agg({
                'user_name': 'first',  # Keep the full original name for display
                'pk': 'count'
            }).reset_index().rename(columns={'pk': 'tx_count', 'user_name': 'actual_user_name'})
            worked_days.columns = ['date_obj', 'match_key', 'actual_user_name', 'tx_count']
            
            # Helper to extract "Match Key" from Schedule Data (Format: "Raeven" or "Melissa S.")
            def get_sched_name_key(staff_name):
                # "Melissa S." -> "melissa"
                return str(staff_name).strip().split(" ")[0].lower()
            
            df_sched['match_key'] = df_sched['staff_name'].apply(get_sched_name_key)
            df_sched['date_obj'] = df_sched['dt'].dt.date
            
            # --- 2. MERGE DATA ---
            # Merge on Date + First Name Key
            audit = pd.merge(
                df_sched, 
                worked_days, 
                on=['date_obj', 'match_key'], 
                how='outer'
            )
            
            # --- 3. CLEAN UP DISPLAY ---
            # Fill NaNs
            # If actual_user_name is missing (No Show), use the scheduled staff_name
            audit['display_name'] = audit['actual_user_name'].fillna(audit['staff_name'])
            # If staff_name is missing (Unscheduled), format the display name
            audit['display_name'] = audit['display_name'].fillna("Unknown")
            
            audit['shift_type'] = audit['shift_type'].fillna("-")
            audit['tx_count'] = audit['tx_count'].fillna(0)
            
            # --- 4. STATUS LOGIC (UPDATED FOR PTO & IV) ---
            def get_attendance_status(row):
                shift = str(row['shift_type']).upper()
                
                # Check for PTO First
                if row['assignment_type'] == 'PTO' or 'PTO' in shift:
                    return "🌴 PTO"
                
                # Check for IV Shifts (Excluded from strict tracking)
                if 'IV' in shift:
                    if row['tx_count'] > 0: return "✅ Present (IV)"
                    return "💉 IV Shift (Not Tracked)"

                # Scheduled (Shift exists) but No Transactions
                if row['shift_type'] != "-" and row['tx_count'] == 0:
                    return "❌ No Show / No Login"
                
                # Not Scheduled but Has Transactions
                if row['shift_type'] == "-" and row['tx_count'] > 0:
                    return "➕ Unscheduled Pick-up"
                
                # Scheduled and Has Transactions
                if row['shift_type'] != "-" and row['tx_count'] > 0:
                    return "✅ Present"
                
                return "Unknown"

            audit['Status'] = audit.apply(get_attendance_status, axis=1)
            
            # --- 5. METRICS & VISUALS ---
            # Filter out IV shifts from standard metrics if they are "Not Tracked"
            standard_audit = audit[~audit['Status'].str.contains("IV")]
            
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Scheduled (Non-IV)", len(standard_audit[standard_audit['shift_type'] != "-"]))
            m2.metric("Present", len(standard_audit[standard_audit['Status'] == "✅ Present"]))
            m3.metric("Unscheduled", len(audit[audit['Status'] == "➕ Unscheduled Pick-up"]))
            m4.metric("No Shows", len(standard_audit[standard_audit['Status'] == "❌ No Show / No Login"]))
            
            st.divider()
            
            # --- 6. PTO SECTION ---
            pto_df = audit[audit['Status'] == "🌴 PTO"].copy()
            if not pto_df.empty:
                with st.expander("🌴 PTO / Time Off Report", expanded=True):
                    st.dataframe(
                        pto_df[['date_obj', 'display_name', 'shift_type', 'note']].sort_values('date_obj', ascending=False),
                        column_config={
                            "date_obj": st.column_config.DateColumn("Date"),
                            "display_name": "Technician",
                            "shift_type": "Leave Type",
                            "note": "Notes"
                        },
                        use_container_width=True
                    )
            
            # --- 7. MAIN ATTENDANCE TABLE (Excluding PTO) ---
            st.subheader("Daily Attendance Log")
            
            filter_status = st.multiselect(
                "Filter Status", 
                options=["❌ No Show / No Login", "➕ Unscheduled Pick-up", "✅ Present", "💉 IV Shift (Not Tracked)", "✅ Present (IV)"], 
                default=["❌ No Show / No Login", "➕ Unscheduled Pick-up"]
            )
            
            # Add text search filter
            search_name = st.text_input("Search Name", "")
            
            view_df = audit[audit['Status'] != "🌴 PTO"].copy() # Exclude PTO from main list
            
            if filter_status: view_df = view_df[view_df['Status'].isin(filter_status)]
            if search_name: view_df = view_df[view_df['display_name'].astype(str).str.contains(search_name, case=False)]
                
            st.dataframe(
                view_df[['date_obj', 'display_name', 'shift_type', 'Status', 'tx_count', 'note']].sort_values('date_obj', ascending=False), 
                column_config={
                    "date_obj": st.column_config.DateColumn("Date"),
                    "display_name": "Technician",
                    "shift_type": "Shift",
                    "tx_count": st.column_config.NumberColumn("Transactions"),
                    "note": "Notes"
                },
                use_container_width=True
            )
        else:
            st.warning("Schedule loaded, but no Event data found to compare.")
            st.dataframe(df_sched)
    else:
        st.info("No Schedule Data found. Upload 'Staff Schedule' CSV.")
