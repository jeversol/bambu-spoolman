import { backendRequest } from "./server/api";

type Info = {
  spoolmanUrl: string;
  spoolmanValid: boolean;
  features: { trayLocking: boolean };
};

export async function supportsTrayLocking() {
  const info = await backendRequest<Info>("/info");
  return info.features.trayLocking;
}
