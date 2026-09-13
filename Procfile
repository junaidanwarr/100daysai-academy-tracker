# Two processes. Both are required.
#
#   web     serves the application
#   worker  runs deadline scans, alert generation, notification delivery and
#           YouTube synchronisation. Without it the system still serves pages,
#           but nothing is monitored automatically and no alert ever fires.
#
# Release runs migrations before the new version takes traffic.
release: python manage.py migrate --noinput && python manage.py setup_schedules
web: gunicorn config.wsgi:application --bind 0.0.0.0:$PORT --workers 3 --timeout 120 --access-logfile -
worker: python manage.py qcluster
