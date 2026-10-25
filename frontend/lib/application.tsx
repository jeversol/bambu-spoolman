import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  type ReactNode,
} from "react";
import {
  QueryClient,
  QueryClientProvider,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { useLocation } from "react-router";
import { apiRequest } from "./api";
import { parseSpool, type Spool } from "./spool-model";
import type { Settings } from "./settings";
import type { PrinterStatus } from "./types";
import { Button } from "@/components/ui/button";

type ApplicationData = {
  settings: Settings;
  printerSettings: PrinterStatus | null;
  spools: Spool[];
  rfidEnabled: boolean;
};
const Data = createContext<ApplicationData | null>(null);
const queryKey = ["application"];
const client = new QueryClient({
  defaultOptions: {
    queries: {
      retry: false,
      refetchOnWindowFocus: false,
      refetchOnReconnect: false,
    },
  },
});

export function useRefresh(cancelRefetch = true) {
  const queries = useQueryClient();
  return useCallback(() => {
    void queries.invalidateQueries({ queryKey }, { cancelRefetch });
  }, [queries, cancelRefetch]);
}

export function useApplicationData() {
  const value = useContext(Data);
  if (!value) throw new Error("Application data is unavailable");
  return value;
}

function ApplicationBoundary({ children }: { children: ReactNode }) {
  const location = useLocation();
  const refresh = useRefresh();
  useEffect(() => {
    refresh();
  }, [location.key, refresh]);
  useEffect(() => {
    window.addEventListener("bambu-spoolman:changed", refresh);
    return () => window.removeEventListener("bambu-spoolman:changed", refresh);
  }, [refresh]);
  const query = useQuery({
    queryKey,
    queryFn: async ({ signal }) => {
      const [settings, printer, inventory, info] = await Promise.all([
        apiRequest<Settings>("/settings", { signal }),
        apiRequest<{ connected: boolean; status: PrinterStatus }>("/printer", {
          signal,
        }),
        apiRequest<{ spools: unknown[] }>("/spools", { signal }),
        apiRequest<{ features: { trayLocking: boolean } }>("/info", { signal }),
      ]);
      return {
        settings,
        printerSettings: printer.connected ? printer.status : null,
        spools: inventory.spools.map(parseSpool),
        rfidEnabled: info.features.trayLocking,
      };
    },
  });
  return (
    <>
      {query.error && (
        <div role="alert" className="mx-auto max-w-6xl p-4 text-destructive">
          Could not refresh application data: {query.error.message}{" "}
          <Button variant="outline" onClick={refresh}>
            Try again
          </Button>
        </div>
      )}
      {query.data ? (
        <Data.Provider value={query.data}>{children}</Data.Provider>
      ) : (
        !query.error && (
          <p role="status" className="p-6">
            Loading spool mapping…
          </p>
        )
      )}
    </>
  );
}
export function ApplicationProvider({ children }: { children: ReactNode }) {
  return (
    <QueryClientProvider client={client}>
      <ApplicationBoundary>{children}</ApplicationBoundary>
    </QueryClientProvider>
  );
}
