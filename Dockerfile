FROM python:3.11

WORKDIR /code

COPY ./pyproject.toml /code/pyproject.toml
COPY ./README.md /code/README.md

COPY ./src /code/src

RUN pip install --no-cache-dir --upgrade .

#CMD ["uvicorn", "helioweb.ui.main:app", "--proxy-headers", "--host", "0.0.0.0", "--port", "80"]
CMD ["gunicorn", "helioweb.ui.main:app", "--workers", "32", "--worker-class", "uvicorn.workers.UvicornWorker", "--bind", "0.0.0.0:80"]
