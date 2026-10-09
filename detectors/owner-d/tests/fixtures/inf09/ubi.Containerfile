# Synthetic INF-09 fixture: RHEL UBI image.
FROM registry.access.redhat.com/ubi9/ubi:9.4
RUN dnf install -y procps-ng
