# 單一容器:build 前端 → FastAPI serve API + 靜態檔
FROM node:24-alpine AS fe
WORKDIR /fe
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app ./app
COPY data_show/sample_appeals ./data_show/sample_appeals
COPY --from=fe /fe/dist ./static
ENV MOCK_DATA_DIR=/app/data_show/sample_appeals \
    STATIC_DIR=/app/static \
    PYTHONUNBUFFERED=1
RUN useradd --create-home appuser && chown -R appuser /app
USER appuser
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
