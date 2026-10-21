FROM python:3.12-slim
WORKDIR /app
COPY server.py ./
COPY public ./public
COPY templates ./templates
ENV BRICS_HOST=0.0.0.0
ENV BRICS_PORT=8080
ENV BRICS_DATA=/data
EXPOSE 8080
CMD ["python", "-u", "server.py"]
