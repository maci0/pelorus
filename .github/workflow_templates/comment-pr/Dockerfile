FROM python:3.13-slim

# TODO create repo for this action and use poetry and dependabot to look the
# python dependencies
RUN pip install --no-cache-dir "pydantic==2.12.5" "pydantic-settings==2.9.1" "pygithub==2.6.0" && \
    useradd --create-home appuser

COPY ./main.py /app/main.py

USER appuser
WORKDIR /app

CMD ["python", "main.py"]
