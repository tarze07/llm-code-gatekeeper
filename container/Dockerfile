# Obraz narzędzi bramy: `gatekeeper-tools`.
#
# Backend izolacji `container` (core/gatekeeper_core/core/container.py)
# uruchamia w nim każde narzędzie: semgrep, gitleaks, pip-audit, ruff, mypy,
# pytest/coverage/diff-cover, node/npm, dotnet + gatekeeper-cs-helper.
# Kontekst budowania to korzeń repozytorium:
#
#   docker build -t gatekeeper-tools:latest -f container/Dockerfile .
#
# Zależności ocenianego repo NIE wchodzą tutaj — to obraz projektu
# (`FROM gatekeeper-tools` + instalacja zależności), wskazywany przez
# GATEKEEPER_CONTAINER_IMAGE. Wersje są przypięte; zmiana wersji = zmiana tego pliku.

ARG PYTHON_IMAGE=python:3.12-slim-bookworm
ARG NODE_IMAGE=node:22-bookworm-slim
ARG DOTNET_SDK_IMAGE=mcr.microsoft.com/dotnet/sdk:8.0-bookworm-slim

# --- helper Roslyn dla pack'a C# -------------------------------------------
FROM ${DOTNET_SDK_IMAGE} AS cs-helper
WORKDIR /src
COPY csharp/tools/gatekeeper-cs-helper/ ./
RUN rm -rf bin obj \
 && dotnet publish GatekeeperCsHelper.csproj -c Release -o /out --nologo

FROM ${NODE_IMAGE} AS node
FROM ${DOTNET_SDK_IMAGE} AS dotnet

# --- obraz narzędzi -----------------------------------------------------------
FROM ${PYTHON_IMAGE}

ARG GITLEAKS_VERSION=8.21.2
ARG GITLEAKS_SHA256=5bc41815076e6ed6ef8fbecc9d9b75bcae31f39029ceb55da08086315316e3ba

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DOTNET_ROOT=/usr/share/dotnet \
    DOTNET_CLI_TELEMETRY_OPTOUT=1 \
    DOTNET_NOLOGO=1 \
    DOTNET_SKIP_FIRST_TIME_EXPERIENCE=1 \
    NUGET_PACKAGES=/opt/nuget \
    NODE_PATH=/usr/local/lib/node_modules

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      git ca-certificates curl libicu72 libssl3 zlib1g tini \
 && rm -rf /var/lib/apt/lists/*

# gitleaks: przypięta wersja + suma kontrolna (nie ufamy samemu pobraniu).
RUN curl -fsSL -o /tmp/gitleaks.tgz \
      "https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/gitleaks_${GITLEAKS_VERSION}_linux_x64.tar.gz" \
 && echo "${GITLEAKS_SHA256}  /tmp/gitleaks.tgz" | sha256sum -c - \
 && tar -xzf /tmp/gitleaks.tgz -C /usr/local/bin gitleaks \
 && rm /tmp/gitleaks.tgz

# Node + npm z oficjalnego obrazu (ta sama dystrybucja: bookworm).
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
 && npm install -g --no-fund --no-audit \
      @typescript-eslint/parser@8.69.0 eslint@10.8.1 typescript@7.0.2 vitest@5.0.0 \
 && npm cache clean --force

# .NET SDK 8 + helper Roslyn.
COPY --from=dotnet /usr/share/dotnet /usr/share/dotnet
COPY --from=cs-helper /out /opt/gatekeeper-cs-helper
RUN ln -s /usr/share/dotnet/dotnet /usr/local/bin/dotnet \
 && printf '#!/bin/sh\nexec dotnet /opt/gatekeeper-cs-helper/GatekeeperCsHelper.dll "$@"\n' \
      > /usr/local/bin/gatekeeper-cs-helper \
 && chmod 0755 /usr/local/bin/gatekeeper-cs-helper \
 && mkdir -p /opt/nuget && chmod 0755 /opt/nuget

# Narzędzia Pythona — te same wersje, na których przechodzą testy bramy.
RUN pip install \
      "ruff==0.16.5" \
      "mypy==2.3.1" \
      "pytest==9.1.1" \
      "coverage==7.16.0" \
      "diff-cover==10.5.1" \
      "semgrep==1.176.0" \
      "pip-audit>=2.7,<3"

# Bez sieci (domyślnie) semgrep czekałby na timeouty sprawdzania wersji
# i wysyłki metryk; kod nie ma też wychodzić na zewnątrz.
ENV SEMGREP_ENABLE_VERSION_CHECK=0 \
    SEMGREP_SEND_METRICS=off

# Uruchamiane jako nie-root (backend i tak podaje --user); /work to kopia kodu.
RUN useradd --uid 1000 --create-home --shell /usr/sbin/nologin gatekeeper \
 && mkdir -p /work && chown gatekeeper:gatekeeper /work
USER gatekeeper
WORKDIR /work

LABEL org.opencontainers.image.title="gatekeeper-tools" \
      org.opencontainers.image.description="Narzędzia llm-code-gatekeeper uruchamiane w izolacji" \
      org.opencontainers.image.source="https://github.com/tarze07/llm-code-gatekeeper"
