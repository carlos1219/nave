# Imagen de Semgrep. PYTHON_IMAGE debe ser una referencia fijada por digest; SEMGREP_VERSION
# debe coincidir con NAVE_RUNNER_VERSION_SEMGREP y con la versión esperada por el worker.
ARG PYTHON_IMAGE
FROM ${PYTHON_IMAGE}
ARG SEMGREP_VERSION
# requirements.lock: semgrep y dependencias con hashes (pip-compile --generate-hashes).
COPY requirements.lock /tmp/requirements.lock
RUN test -n "${SEMGREP_VERSION}" \
    && pip install --no-cache-dir --require-hashes -r /tmp/requirements.lock \
    && rm /tmp/requirements.lock \
    && test "$(semgrep --version)" = "${SEMGREP_VERSION}"
COPY rules /rules
COPY nave_oversized.py /usr/local/lib/nave/nave_oversized.py
COPY nave-scan /usr/local/bin/nave-scan
ENV SEMGREP_SEND_METRICS=off SEMGREP_ENABLE_VERSION_CHECK=0 PYTHONDONTWRITEBYTECODE=1
USER 65532:65532
ENTRYPOINT []
