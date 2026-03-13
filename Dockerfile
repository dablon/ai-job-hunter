FROM python:3.11-slim

LABEL maintainer="Nicolas Alcaraz <nicolasalca@hotmail.com>"
LABEL description="Automated job hunting pipeline with Minimax support"

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy project files
COPY pyproject.toml .
COPY src/ ./src/

# Install Python package in editable mode
RUN pip install --no-cache-dir -e .

# Create directory for config persistence
RUN mkdir -p /app/data

# Set environment
ENV PYTHONUNBUFFERED=1

# Default entrypoint
ENTRYPOINT ["job-hunter"]
