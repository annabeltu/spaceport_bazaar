FROM mcr.microsoft.com/devcontainers/python:1-3.12-bookworm

# The practice server dynamically links against libgcc_s.so.1. Bookworm ships
# glibc 2.36, which is newer than its required glibc 2.34.
RUN rm -f /etc/apt/sources.list.d/yarn.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends libgcc-s1 protobuf-compiler \
    && rm -rf /var/lib/apt/lists/*
