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
ENV DJANGO_SETTINGS_MODULE=pandora.settings

# Criar pasta de estáticos
RUN mkdir -p /app/staticfiles

# Segredos NUNCA entram na imagem (ADR-0024): os comandos de boot usam uma
# SECRET_KEY dummy que vive só no RUN; a real chega em runtime via env do
# compose. Não reintroduzir ARG/ENV de segredo aqui — imagem com segredo
# vaza o segredo junto com a layer.

# Gate: model mudou sem migration -> falha o build (sem imagem, sem deploy)
RUN SECRET_KEY=build-dummy python manage.py makemigrations --check --dry-run

# Coletar arquivos estáticos (precisa de uma SECRET_KEY qualquer pro boot)
RUN SECRET_KEY=build-dummy python manage.py collectstatic --noinput

# Expor porta
EXPOSE 8000

# Comando padrão
CMD ["gunicorn", "pandora.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2", "--timeout", "120"]