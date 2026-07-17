FROM python:3.11-slim

ARG HERALD_VERSION=0.1.0
LABEL org.opencontainers.image.title="Herald Subnet" \
      org.opencontainers.image.version="$HERALD_VERSION"

WORKDIR /app

# System deps for bittensor (substrate-interface needs these)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ libssl-dev pkg-config curl git \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install the package
COPY pyproject.toml setup.py README.md ./
COPY herald/ herald/
RUN pip install --no-cache-dir --no-build-isolation -e .

# Source code (neurons, core)
COPY neurons/ neurons/
COPY core/ core/

# Entrypoint script (bootstraps wallet from secrets)
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Bittensor wallet path
ENV BT_WALLET_PATH=/root/.bittensor/wallets

# Entrypoint and command are set via Terraform task definition.
# ARG ROLE is not used at build time — it's documented here for clarity.
ARG ROLE=miner
ENTRYPOINT ["/entrypoint.sh"]
