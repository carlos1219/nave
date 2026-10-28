/** Error de la API (RFC 7807) o de transporte. Nunca contiene texto del servidor más allá de
 * campos de forma fija: el `title` se valida y el resto de la respuesta se descarta. */
export class ApiError extends Error {
  readonly status: number;
  readonly type: string;
  readonly requestId: string | null;
  /** Segundos que el servidor pide esperar (429/503), acotados. */
  readonly retryAfter: number | null;

  constructor(init: {
    status: number;
    type: string;
    title: string;
    requestId?: string | null;
    retryAfter?: number | null;
  }) {
    super(init.title);
    this.name = "ApiError";
    this.status = init.status;
    this.type = init.type;
    this.requestId = init.requestId ?? null;
    this.retryAfter = init.retryAfter ?? null;
  }

  get unauthorized(): boolean {
    return this.status === 401;
  }

  get forbidden(): boolean {
    return this.status === 403;
  }

  get notFound(): boolean {
    return this.status === 404;
  }

  get tooManyRequests(): boolean {
    return this.status === 429;
  }

  /** Fallos que tiene sentido reintentar con espera. */
  get transient(): boolean {
    return this.status === 0 || this.status === 429 || this.status === 503;
  }
}

/** Argumento del llamante que nunca llegaría a la red (identificador mal formado, etc.). */
export class ClientInputError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ClientInputError";
  }
}
