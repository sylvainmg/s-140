FROM python:3.11-slim

WORKDIR /app

# 1. Installation des dépendances de compilation de base
RUN apt-get update && apt-get install -y \
    gcc \
    libcurl4-openssl-dev \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

# 2. Installation des dépendances Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 3. Installation de Playwright Chromium ET de ses dépendances système Linux
# (Indispensable pour éviter les erreurs de bibliothèques manquantes comme libgbm sur Render)
RUN playwright install --with-deps chromium

# 4. Copie du code de l'application
COPY . .

EXPOSE 8000

CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]
