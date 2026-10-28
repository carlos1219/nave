# Imagen del contenedor de fetch. Los dos ARG no tienen valor por defecto: la compilación
# falla si no se aportan, y deben ser referencias fijadas por digest (CLAUDE.md §15).
ARG PYTHON_IMAGE
FROM ${PYTHON_IMAGE}
RUN apk add --no-cache git
COPY pathguard.py nave_fetch.py /opt/nave/
RUN printf '#!/bin/sh\nexec python3 -E -s /opt/nave/nave_fetch.py "$@"\n' > /usr/local/bin/nave-fetch \
    && chmod 0755 /usr/local/bin/nave-fetch
USER 65532:65532
ENTRYPOINT []
