FROM python:3.13-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN pip install --upgrade pip

# Copiar requirements e instalar dependências
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar o restante do projeto
COPY . .

# Definir settings do Django
ENV DJANGO_SETTINGS_MODULE=citosharp.settings

# Criar pasta de estáticos
RUN mkdir -p /app/staticfiles

# Coletar arquivos estáticos
RUN python manage.py collectstatic --noinput

# Expor porta
EXPOSE 8000

# Comando padrão
CMD ["gunicorn", "citosharp.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2", "--timeout", "120"]