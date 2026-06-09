web: python migrate_add_audit_log.py && python migrate_add_gl_scans.py && gunicorn run:app --bind 0.0.0.0:$PORT --workers 2 --timeout 60
