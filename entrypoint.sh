#!/bin/sh
set -e

echo "Waiting for database..."
while ! python -c "import psycopg2; psycopg2.connect(host='$DB_HOST', dbname='$DB_NAME', user='$DB_USER', password='$DB_PASSWORD')" 2>/dev/null; do
  sleep 1
done
echo "Database ready."

echo "Waiting for redis..."
while ! python -c "import redis; redis.Redis(host='$REDIS_HOST', port=int('$REDIS_PORT')).ping()" 2>/dev/null; do
  sleep 1
done
echo "Redis ready."

python manage.py migrate --noinput
python manage.py collectstatic --noinput

exec daphne -b 0.0.0.0 -p 8000 quiz_project.asgi:application