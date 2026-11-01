# Imagen de Trivy. TRIVY_IMAGE debe estar fijada por digest.
ARG TRIVY_IMAGE
FROM ${TRIVY_IMAGE}
# --chmod: el modo no depende del checkout (en Windows git no guarda +x; F7-34).
COPY --chmod=0555 nave-scan /usr/local/bin/nave-scan
COPY trivy.yaml trivy-secret.yaml empty.trivyignore /etc/nave/
USER 65532:65532
ENTRYPOINT []
