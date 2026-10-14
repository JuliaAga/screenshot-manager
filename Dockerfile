FROM debian:bookworm-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOST=0.0.0.0 PORT=8787 DATA_DIR=/data
RUN apt-get update && apt-get install -y --no-install-recommends python3 ca-certificates tesseract-ocr tesseract-ocr-eng tesseract-ocr-rus \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home later \
    && mkdir /data && chown later:later /data
WORKDIR /opt/later
COPY app ./app
USER later
VOLUME /data
EXPOSE 8787
CMD ["python3", "-m", "app.server"]
