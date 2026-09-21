# The practice server ships as a prebuilt Linux program that needs
# glibc 2.34 or newer plus libgcc_s.so.1.
#
# Debian bookworm ships glibc 2.36, so it qualifies.
# Alpine would NOT work: it uses musl instead of glibc, and the server would
# fail to start with a confusing "not found" error.
FROM mcr.microsoft.com/devcontainers/python:3.12-bookworm

# Install Python packages while still root, because they go into the
# system-wide Python that every user can read.
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --root-user-action=ignore -r /tmp/requirements.txt

# Everything from here on -- your terminal, your client, and the downloaded
# server binary -- runs as the ordinary "vscode" user this base image
# provides, not as root. If a downloaded program misbehaves, it can't modify
# the system inside the container.
USER vscode
