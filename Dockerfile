FROM python:3.10

ENV PYTHONDONTWRITEBYTECODE 1

ENV PYTHONNUNBUFFERED 1

WORKDIR /code

COPY . /code/

RUN pip install -U pip

RUN pip install -r requirements.txt