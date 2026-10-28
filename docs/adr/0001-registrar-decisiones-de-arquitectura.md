# 0001 — Registrar las decisiones de arquitectura con ADRs

- **Estado:** Aceptado
- **Fecha:** 2026-10-11
- **Sección de CLAUDE.md afectada:** §0, §6, §23

## Contexto

CLAUDE.md fija que toda decisión estructural se registra y que las decisiones abiertas de §23 se resuelven antes de implementar. Hace falta un formato y un lugar.

## Decisión

Se usan ADRs en `docs/adr/`, numerados `NNNN-titulo-en-kebab-case.md`, con la plantilla `0000-plantilla.md`. Un ADR aceptado no se edita: se sustituye por otro nuevo que lo referencia. Cada ADR que cambie una regla actualiza CLAUDE.md en el mismo commit.

## Consecuencias

ADRs pendientes en fase 0 (§23):

| Nº | Tema | Estado |
|---|---|---|
| 0002 | Mecanismo de contenedor efímero por escaneo sin `docker.sock` | PENDIENTE |
| 0003 | Motor de políticas: propio vs. OPA | PENDIENTE |
| 0004 | Proveedor OIDC y modelo de organizaciones | PENDIENTE |
| 0005 | Polling con backoff vs. SSE | PENDIENTE |
| 0006 | Retención de findings, logs y workspaces | PENDIENTE |
