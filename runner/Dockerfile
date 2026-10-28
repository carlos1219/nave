# Imagen del scan-runner. Los tres ARG son referencias fijadas por digest, sin valor por
# defecto: la compilación falla si falta alguna (CLAUDE.md §15).
ARG PYTHON_IMAGE
ARG DOCKER_CLI_IMAGE
FROM ${DOCKER_CLI_IMAGE} AS docker-cli
FROM ${PYTHON_IMAGE}
COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker
# Sin dependencias en ejecucion: el paquete se copia a site-packages (python -I ignora
# PYTHONPATH), sin instalar nada ni resolver paquetes en la compilacion.
COPY src/nave_runner /usr/local/lib/python3.12/site-packages/nave_runner
USER 65532:65532
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
ENTRYPOINT ["python", "-I", "-m", "nave_runner"]
