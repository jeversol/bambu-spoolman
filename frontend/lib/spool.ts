import { cacheLife, cacheTag } from "next/cache";
import { ApiError } from "./api";
import { backendRequest } from "./server/api";
import { Spool } from "./proto/bambu_spoolman/grpc/spoolman";

// Preserve the existing UI model during the transport migration. Spoolman's
// raw HTTP fields use snake_case; custom extra-field keys must remain untouched.
function uiFields(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(uiFields);
  if (value !== null && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value).map(([key, child]) => [
        key.replace(/_([a-z])/g, (_, letter: string) => letter.toUpperCase()),
        key === "extra" ? child : uiFields(child),
      ]),
    );
  }
  return value;
}

function getCacheTag(id: string) {
  return `spool-${id}`;
}

export async function getSpool(id: string) {
  "use cache";
  cacheLife("seconds");
  cacheTag(getCacheTag(id));
  cacheTag("spools");

  try {
    const response = await backendRequest<unknown>(
      `/spools/${encodeURIComponent(id)}`,
    );
    return Spool.fromJSON(uiFields(response));
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null;
    throw error;
  }
}

export async function getAllSpools() {
  "use cache";
  cacheLife("seconds");
  cacheTag("all-spools");

  const response = await backendRequest<{ spools: unknown[] }>("/spools");
  return response.spools.map((spool) => Spool.fromJSON(uiFields(spool)));
}
