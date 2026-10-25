import { apiRequest } from "@/lib/api";

export type UpdateTrayAssignmentActionData = {
  error: string | null;
};

export async function updateTrayAssignment(
  trayId: number,
  spoolId: number,
): Promise<UpdateTrayAssignmentActionData> {
  try {
    await apiRequest<void>(`/trays/${trayId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ spoolId }),
    });
    return { error: null };
  } catch (error) {
    return {
      error:
        error instanceof Error ? error.message : "An unknown error occurred",
    };
  }
}
