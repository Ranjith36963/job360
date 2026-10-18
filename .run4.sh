cd /var/tmp/wt-wave1c/backend
export DATABASE_URL=postgresql://job360:job360dev@localhost:5432/job360
exec /var/tmp/j360venv/bin/python -m pytest -q -p no:randomly tests/test_bulk_recipes_blocked_sites.py tests/test_recipes.py tests/test_blocked_record.py tests/test_apply_run_check_fixes.py tests/test_morning_check.py tests/test_slice4_stats.py tests/test_mcp_gate_parity.py tests/test_outreach_people.py tests/test_tools_states_recipes.py tests/test_event_types_json_matches_settings.py > /var/tmp/wt-wave1c/.r4.txt 2>&1
