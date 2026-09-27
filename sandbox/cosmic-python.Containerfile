# Test image for the Cosmic Python target (https://github.com/cosmicpython/code).
# It contains the dependencies only. The repository copy is mounted read-only at /work at
# run time, so every check runs against the agent's current code.
#
# Build (context = the target repository):
#   podman build -f sandbox/cosmic-python.Containerfile -t harness-cosmic-python code
FROM docker.io/library/python:3.9-slim

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

ENV PYTHONPATH=/work/src \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN useradd --create-home runner
USER runner
WORKDIR /work
