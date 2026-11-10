import { useQuery } from "@tanstack/react-query";
import { apiRequest } from "@/lib/api";

export function AccountingNotice() {
  const query = useQuery({
    queryKey: ["accounting-status"],
    queryFn: ({ signal }) =>
      apiRequest<{
        warning: string | null;
      }>("/accounting", { signal }),
    refetchInterval: 5000,
  });
  if (query.data?.warning) {
    return (
      <div role="alert" className="mx-auto max-w-6xl p-4 text-destructive">
        {query.data.warning}
      </div>
    );
  }
  return query.error ? (
    <div role="alert" className="mx-auto max-w-6xl p-4 text-destructive">
      Accounting status is unavailable. Check the service before relying on usage
      totals.
    </div>
  ) : null;
}
