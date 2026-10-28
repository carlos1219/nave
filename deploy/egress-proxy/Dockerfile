# Smokescreen no publica imagen oficial: se compila desde el modulo Go. GO_IMAGE y
# SMOKESCREEN_VERSION no tienen valor por defecto (en produccion: digest y tag/commit fijos).
ARG GO_IMAGE
FROM ${GO_IMAGE} AS build
ARG SMOKESCREEN_VERSION
ENV CGO_ENABLED=0 GOFLAGS=-trimpath
# Se compila dentro de un modulo propio para elevar dependencias transitivas con CVE (logrus,
# x/net, x/text) sin esperar a una version nueva de Smokescreen; Dependabot (docker) no las ve.
ARG LOGRUS_VERSION=v1.9.3
ARG XNET_VERSION=v0.60.0
ARG XTEXT_VERSION=v0.42.0
WORKDIR /m
RUN test -n "${SMOKESCREEN_VERSION}" && go mod init nave.local/egress \
 && go get github.com/stripe/smokescreen@${SMOKESCREEN_VERSION} \
      github.com/sirupsen/logrus@${LOGRUS_VERSION} golang.org/x/net@${XNET_VERSION} golang.org/x/text@${XTEXT_VERSION} \
 && go build -o /go/bin/smokescreen github.com/stripe/smokescreen
# La imagen final no tiene shell ni `nc`: la sonda del healthcheck se compila aparte.
COPY healthcheck.go /src/healthcheck.go
RUN go build -o /go/bin/healthcheck /src/healthcheck.go

FROM scratch
COPY --from=build /go/bin/smokescreen /smokescreen
COPY --from=build /go/bin/healthcheck /healthcheck
USER 65532:65532
ENTRYPOINT ["/smokescreen"]
