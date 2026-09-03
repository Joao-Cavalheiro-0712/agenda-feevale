web: python -m agenda.cli migrate && gunicorn wsgi:app --workers 2 --threads 8 --timeout 180 --bind 0.0.0.0:$PORT --access-logfile - --error-logfile -
