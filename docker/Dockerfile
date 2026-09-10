# 單一容器:build 前端 → FastAPI serve API + 靜態檔
FROM node:24-alpine AS fe
WORKDIR /fe
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install
COPY frontend/ ./
RUN npm run build

# 決定書 PDF 要內嵌楷體:內建 china-t 沒有 ToUnicode CMap,產出的 PDF 文字複製不出來。
# ukai.ttc 內有 CN/HK/TW/TW-MBE 四個 face,PyMuPDF 只取得到 face 0(CN);
# 繁體公文要 TW 的字形,故在此把 face 2 拆成獨立 TTF。
FROM python:3.12-slim AS font
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-arphic-ukai \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir fonttools \
    && python -c "from fontTools.ttLib import TTCollection; TTCollection('/usr/share/fonts/truetype/arphic/ukai.ttc').fonts[2].save('/ukai-tw.ttf')"

FROM python:3.12-slim
WORKDIR /app
COPY --from=font /ukai-tw.ttf /usr/share/fonts/truetype/ukai-tw.ttf
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
