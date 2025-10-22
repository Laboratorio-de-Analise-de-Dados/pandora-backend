# Dockerfile de produção para Django
FROM python:3.10.15-slim

# Diretório de trabalho
WORKDIR /app

# Evitar arquivos .pyc e buffer no stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Atualizar pip
RUN pip install --upgrade pip

# Copiar apenas requirements primeiro para aproveitar cache
COPY requirements.txt /app/

# Instalar dependências
RUN pip install --no-cache-dir -r requirements.txt

# Copiar o restante do projeto
COPY . /app/

# Coletar arquivos estáticos
RUN python manage.py collectstatic --noinput

# Expor a porta que o Gunicorn vai usar
EXPOSE 8000

# Comando padrão: roda Gunicorn em produção
CMD ["gunicorn", "pandora.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "4"]
