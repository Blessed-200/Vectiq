FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir -e .
VOLUME /app/data
CMD ["python","-m","vectiq","collect","--minutes","100000","--out","/app/data"]
