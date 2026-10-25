import { cacheLife, cacheTag } from "next/cache";
import { ApiError } from "./api";
import { backendRequest } from "./server/api";
import { parseSpool } from "./spool-model";

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
    return parseSpool(response);
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
  return response.spools.map((spool) => parseSpool(spool));
}
