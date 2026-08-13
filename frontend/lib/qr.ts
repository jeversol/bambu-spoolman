const SPOOLMAN_QR_REGEX = /^web\+spoolman:s-(\d+)$/i;

export function getSpoolIdFromQrValue(rawValue: string): number | null {
  const value = rawValue.trim();
  const spoolmanMatch = value.match(SPOOLMAN_QR_REGEX);
  if (spoolmanMatch) return Number(spoolmanMatch[1]);

  try {
    const url = new URL(value);
    if (url.protocol !== "http:" && url.protocol !== "https:") return null;

    const pathParts = url.pathname.split("/").filter(Boolean);
    const lastPart = pathParts.at(-1);
    return lastPart && /^\d+$/.test(lastPart) ? Number(lastPart) : null;
  } catch {
    return null;
  }
}
