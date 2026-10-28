import { ApiError } from "@/shared/api";

/** `null` si fue una cancelación pedida por nosotros; si no, siempre un `ApiError` de forma fija. */
export function asApiError(error: unknown): ApiError | null {
  if (error instanceof ApiError) return error;
  if (error instanceof DOMException && error.name === "AbortError") return null;
  return new ApiError({
    status: 0,
    type: "urn:nave:client:unexpected",
    title: "Error inesperado en la aplicación",
  });
}
