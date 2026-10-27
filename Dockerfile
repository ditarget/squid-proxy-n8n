ARG BASE_IMAGE=alpine:3.23
FROM ${BASE_IMAGE} AS runtime
RUN apk add --no-cache squid python3 ca-certificates
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY proxy_manager ./proxy_manager
CMD ["python3", "-m", "proxy_manager"]

FROM runtime AS test
RUN apk add --no-cache make git openssl
COPY . .
CMD ["python3", "-m", "unittest", "discover", "-s", "tests", "-v"]
