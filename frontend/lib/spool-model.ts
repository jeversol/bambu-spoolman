// The HTTP boundary owns conversion from Spoolman's snake_case JSON. Keep this
// presentation model independent of backend transports and generated code.
export type Filament = {
  name: string;
  material: string;
  colorHex: string;
  multiColorDirection: string;
  multiColorHexes: string;
  vendor?: { name: string; extra?: Record<string, unknown> };
  extra?: Record<string, unknown>;
};

export type Spool = {
  id: number;
  initialWeight: number;
  remainingWeight: number;
  remainingLength: number;
  usedLength: number;
  filament?: Filament;
  extra?: Record<string, unknown>;
};

function object(value: unknown): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("Invalid Spoolman object");
  }
  return value as Record<string, unknown>;
}

function weight(value: unknown): number {
  if (value === null || value === undefined) return 0;
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new Error("Invalid Spoolman weight or length");
  }
  return value;
}

function text(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value !== "string") throw new Error("Invalid Spoolman text");
  return value;
}

function extra(value: unknown): Record<string, unknown> | undefined {
  return value === null || value === undefined ? undefined : object(value);
}

function filament(value: unknown): Filament | undefined {
  if (value === null || value === undefined) return undefined;
  const data = object(value);
  const vendor = data.vendor == null ? undefined : object(data.vendor);
  return {
    name: text(data.name),
    material: text(data.material),
    colorHex: text(data.color_hex),
    multiColorDirection: text(data.multi_color_direction),
    multiColorHexes: text(data.multi_color_hexes),
    vendor: vendor
      ? { name: text(vendor.name), extra: extra(vendor.extra) }
      : undefined,
    extra: extra(data.extra),
  };
}

export function parseSpool(value: unknown): Spool {
  const data = object(value);
  if (
    typeof data.id !== "number" ||
    !Number.isSafeInteger(data.id) ||
    data.id <= 0
  ) {
    throw new Error("Invalid Spoolman spool ID");
  }
  return {
    id: data.id,
    initialWeight: weight(data.initial_weight),
    remainingWeight: weight(data.remaining_weight),
    remainingLength: weight(data.remaining_length),
    usedLength: weight(data.used_length),
    filament: filament(data.filament),
    extra: extra(data.extra),
  };
}
