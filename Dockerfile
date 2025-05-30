FROM python:3.13

RUN mkdir /app

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1

ENV PYTHONNUNBUFFERED=1

RUN pip install -U pip

COPY . /app/

RUN pip install --no-cache-dir -r requirements.txt