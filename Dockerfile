# Używamy oficjalnego obrazu Pythona
FROM python:3.11-slim

# Blokada tworzenia plików .pyc i włączenie logowania w czasie rzeczywistym
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

WORKDIR /app

# Instalacja zależności systemowych dla PostgreSQL
RUN apt-get update && apt-get install -y \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Instalacja zależności Pythona
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Kopiowanie reszty plików projektu
COPY . /app/

# Zbieranie plików statycznych (wymagane dla CSS/JS w monolicie)
RUN python manage.py collectstatic --noinput

# Port na którym działa Django
EXPOSE 8000

# Start aplikacji za pomocą Gunicorn
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "project.wsgi:application"]