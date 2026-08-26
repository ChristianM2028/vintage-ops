FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN chmod +x /app/start.sh && addgroup --system vintage && adduser --system --ingroup vintage vintage \
    && mkdir -p /data /tmp/vintage-ops && chown -R vintage:vintage /data /tmp/vintage-ops
USER vintage
ENV TMPDIR=/tmp/vintage-ops
CMD ["/app/start.sh"]
