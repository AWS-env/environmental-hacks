# Synthetic INF-09 fixture: identity boundaries.
FROM python:3.12-bookworm AS base
RUN pip install flask
RUN pip install gunicorn

FROM base
CMD ["gunicorn", "app:app"]
