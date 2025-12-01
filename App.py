TypeError: '<' not supported between instances of 'NoneType' and 'str'

2025-12-01 18:15:57.088 503 GET /script-health-check (127.0.0.1) 2424.98ms

2025-12-01 18:15:59.227 Please replace `use_container_width` with `width`.


`use_container_width` will be removed after 2025-12-31.


For `use_container_width=True`, use `width='stretch'`. For `use_container_width=False`, use `width='content'`.

2025-12-01 18:15:59.329 Please replace `use_container_width` with `width`.


`use_container_width` will be removed after 2025-12-31.


For `use_container_width=True`, use `width='stretch'`. For `use_container_width=False`, use `width='content'`.

2025-12-01 18:15:59.671 Please replace `use_container_width` with `width`.


`use_container_width` will be removed after 2025-12-31.


For `use_container_width=True`, use `width='stretch'`. For `use_container_width=False`, use `width='content'`.

2025-12-01 18:15:59.692 Please replace `use_container_width` with `width`.


`use_container_width` will be removed after 2025-12-31.


For `use_container_width=True`, use `width='stretch'`. For `use_container_width=False`, use `width='content'`.

────────────────────── Traceback (most recent call last) ───────────────────────

  /home/adminuser/venv/lib/python3.13/site-packages/streamlit/runtime/scriptru  

  nner/exec_code.py:129 in exec_func_with_error_handling                        

                                                                                

  /home/adminuser/venv/lib/python3.13/site-packages/streamlit/runtime/scriptru  

  nner/script_runner.py:669 in code_to_exec                                     

                                                                                

  /mount/src/rxtrack/App.py:517 in <module>                                     

                                                                                

    514 │                                                                       

    515 │   # NEW: Filter Controls                                              

    516 │   c_f1, c_f2, c_f3 = st.columns(3)                                    

  ❱ 517 │   filter_user = c_f1.multiselect("Tech", sorted(adds_df['user_name']  

    518 │   filter_device = c_f2.multiselect("Device", sorted(adds_df['device'  

    519 │   filter_med = c_f3.multiselect("Med ID", sorted(adds_df['med_id'].u  

    520                                                                         

────────────────────────────────────────────────────────────────────────────────

TypeError: '<' not supported between instances of 'NoneType' and 'str'

2025-12-01 18:16:00.491 503 GET /script-health-check (127.0.0.1) 2734.37ms

2025-12-01 18:16:04.084 Please replace `use_container_width` with `width`.


`use_container_width` will be removed after 2025-12-31.


For `use_container_width=True`, use `width='stretch'`. For `use_container_width=False`, use `width='content'`.

2025-12-01 18:16:04.171 Please replace `use_container_width` with `width`.


`use_container_width` will be removed after 2025-12-31.


For `use_container_width=True`, use `width='stretch'`. For `use_container_width=False`, use `width='content'`.

2025-12-01 18:16:04.461 Please replace `use_container_width` with `width`.


`use_container_width` will be removed after 2025-12-31.


For `use_container_width=True`, use `width='stretch'`. For `use_container_width=False`, use `width='content'`.

2025-12-01 18:16:04.474 Please replace `use_container_width` with `width`.


`use_container_width` will be removed after 2025-12-31.


For `use_container_width=True`, use `width='stretch'`. For `use_container_width=False`, use `width='content'`.

────────────────────── Traceback (most recent call last) ───────────────────────

  /home/adminuser/venv/lib/python3.13/site-packages/streamlit/runtime/scriptru  

  nner/exec_code.py:129 in exec_func_with_error_handling                        

                                                                                

  /home/adminuser/venv/lib/python3.13/site-packages/streamlit/runtime/scriptru  

  nner/script_runner.py:669 in code_to_exec                                     

                                                                                

  /mount/src/rxtrack/App.py:517 in <module>                                     

                                                                                

    514 │                                                                       

    515 │   # NEW: Filter Controls                                              

    516 │   c_f1, c_f2, c_f3 = st.columns(3)                                    

  ❱ 517 │   filter_user = c_f1.multiselect("Tech", sorted(adds_df['user_name']  

    518 │   filter_device = c_f2.multiselect("Device", sorted(adds_df['device'  

    519 │   filter_med = c_f3.multiselect("Med ID", sorted(adds_df['med_id'].u  

    520                                                                         

────────────────────────────────────────────────────────────────────────────────

TypeError: '<' not supported between instances of 'NoneType' and 'str'

main
plummer92/rxtrack/main/App.py


