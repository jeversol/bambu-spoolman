import { apiRequest } from "../api";

export function backendRequest<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  return apiRequest<T>(
    path,
    init,
    process.env.API_SERVER_URL || "http://127.0.0.1:50052",
  );
}
