"use client";

import { type IDetectedBarcode, Scanner } from "@yudiel/react-qr-scanner";
import { AlertCircle, SearchIcon } from "lucide-react";
import { useRouter } from "next/navigation";
import {
  useActionState,
  useMemo,
  useRef,
  useState,
  useTransition,
} from "react";
import { useCameraAvailable } from "@/lib/hooks/useCameraAvailable";
import type { Spool } from "@/lib/proto/bambu_spoolman/grpc/spoolman";
import { getSpoolIdFromQrValue } from "@/lib/qr";
import { Alert } from "../ui/alert";
import { Button, ButtonLoading } from "../ui/button";
import {
  InputGroup,
  InputGroupAddon,
  InputGroupInput,
} from "../ui/input-group";
import {
  type UpdateTrayAssignmentActionData,
  updateTrayAssignment,
} from "./actions";
import { SpoolRadioGroup } from "./SpoolRadioGroup";

type Props = {
  trayId: number;
  spool: Spool | null;
  allSpools: Spool[];
  selectedSpools: number[];
};

type QrScannerProps = {
  onScan: (result: IDetectedBarcode[]) => void;
  cancelScan: () => void;
};

function QrScanner(props: QrScannerProps) {
  return (
    <>
      <Scanner
        onScan={props.onScan}
        formats={["qr_code"]}
        components={{
          torch: false,
        }}
      />
      <Button variant="destructive" className="mt-4" onClick={props.cancelScan}>
        Cancel Scanning
      </Button>
    </>
  );
}

export function TrayConfigForm(props: Props) {
  const [selectedSpool, setSelectedSpool] = useState(
    props.spool?.id.toString() ?? "",
  );
  const [changed, setChanged] = useState(false);
  const [qrScanning, setQrScanning] = useState(false);
  const [qrScanError, setQrScanError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const scanHandledRef = useRef(false);
  const [isQrPending, startQrTransition] = useTransition();
  const cameraAvailable = useCameraAvailable();
  const router = useRouter();

  const [state, formAction, isPending] = useActionState(
    async (_prev: UpdateTrayAssignmentActionData) => {
      const result = await updateTrayAssignment(
        props.trayId,
        Number(selectedSpool),
      );

      if (!result.error) {
        router.refresh();
      }

      return result;
    },
    {
      error: null,
    },
  );

  const handleScan = (result: IDetectedBarcode[]) => {
    if (result.length === 0) {
      setQrScanError("No QR code detected. Please try again.");
      return;
    }
    if (result.length > 1) {
      setQrScanError(
        "Multiple QR codes detected. Please ensure only one QR code is visible to the camera.",
      );
      return;
    }
    const spoolId = getSpoolIdFromQrValue(result[0].rawValue);
    if (!spoolId || !props.allSpools.some((spool) => spool.id === spoolId)) {
      setQrScanError("That QR code does not identify an available spool.");
      return;
    }

    if (scanHandledRef.current) return;
    scanHandledRef.current = true;

    setSelectedSpool(spoolId.toString());
    setChanged(true);
    setQrScanning(false);
    setQrScanError(null);

    startQrTransition(async () => {
      const assignment = await updateTrayAssignment(props.trayId, spoolId);
      if (assignment.error) {
        setQrScanError(assignment.error);
        return;
      }

      setChanged(false);
      router.refresh();
    });
  };

  const updating = isPending || isQrPending;

  const filteredSpools = useMemo(() => {
    const terms = searchQuery.trim().toLowerCase().split(/\s+/).filter(Boolean);
    if (terms.length === 0) {
      return props.allSpools;
    }

    return props.allSpools.filter((spool) => {
      const searchable = [
        spool.id.toString(),
        spool.filament?.name,
        spool.filament?.vendor?.name,
        spool.filament?.material,
        spool.filament?.colorHex,
        spool.filament?.multiColorHexes,
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();

      return terms.every((term) => searchable.includes(term));
    });
  }, [searchQuery, props.allSpools]);

  return (
    <>
      {qrScanError && (
        <Alert variant="destructive" className="mb-5">
          <AlertCircle />
          {qrScanError}
        </Alert>
      )}
      {qrScanning ? (
        <QrScanner
          onScan={handleScan}
          cancelScan={() => setQrScanning(false)}
        />
      ) : (
        <>
          {cameraAvailable && (
            <Button
              className="w-full mb-2"
              onClick={() => {
                scanHandledRef.current = false;
                setQrScanError(null);
                setQrScanning(true);
              }}
            >
              Scan QR Code
            </Button>
          )}
          <InputGroup className="w-full mb-3">
            <InputGroupInput
              placeholder="Search ID, name, vendor, material, or color"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
            />
            <InputGroupAddon>
              <SearchIcon />
            </InputGroupAddon>
          </InputGroup>

          <form action={formAction}>
            {state?.error && (
              <Alert variant="destructive" className="mb-5">
                <AlertCircle />
                {state?.error}
              </Alert>
            )}
            <SpoolRadioGroup
              spools={filteredSpools}
              value={selectedSpool}
              onValueChange={(e) => {
                setSelectedSpool(e);
                setChanged(true);
              }}
              disabled={updating}
              selected={props.selectedSpools}
              initialSpool={props.spool}
            />
            <Button
              variant="default"
              className="mt-4 float-right"
              type="submit"
              disabled={updating || !changed}
            >
              <ButtonLoading loading={updating} />
              Update
            </Button>
          </form>
        </>
      )}
    </>
  );
}
