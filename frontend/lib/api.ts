export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

export async function apiRequest<T>(
  path: string,
  init: RequestInit = {},
  base = "",
): Promise<T> {
  try {
    const response = await fetch(`${base}/api/v1${path}`, {
      ...init,
      cache: "no-store",
      redirect: "error",
      signal: init.signal
        ? AbortSignal.any([init.signal, AbortSignal.timeout(120_000)])
        : AbortSignal.timeout(120_000),
    });
    if (!response.ok) {
      let message = `Request failed (${response.status})`;
      try {
        const body = await response.json();
        if (typeof body.error === "string") message = body.error;
      } catch {
        // An upstream proxy may return an HTML error instead of API JSON.
      }
      throw new ApiError(message, response.status);
    }
    return response.status === 204 ? (undefined as T) : response.json();
  } finally {
    // Failed writes may have partially applied. Refresh reads in either case.
    if (typeof window !== "undefined" && init.method && init.method !== "GET") {
      window.dispatchEvent(new Event("bambu-spoolman:changed"));
    }
  }
}
