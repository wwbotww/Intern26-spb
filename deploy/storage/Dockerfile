# Requires an existing local baseline; no dependency downloads in this drill.
FROM intern26-spb-assistant-agent-demo:0.3.6

USER 0
RUN install -d -m 0700 -o 10001 -g 10001 /var/lib/spb-runtime /var/lib/spb-backups /var/lib/spb-restored
COPY apps/assistant-api/src /opt/storage-drill-src
ENV PYTHONPATH=/opt/storage-drill-src
USER 10001:10001
HEALTHCHECK NONE
ENTRYPOINT ["/app/.venv/bin/python", "-m", "spb_assistant_api.storage_cli"]
CMD ["--help"]
