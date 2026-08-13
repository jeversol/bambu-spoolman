export enum MultiColorDirection {
  LONGITUDINAL = "longitudinal",
  COAXIAL = "coaxial",
}

export type PrinterTray = {
  id: string;
  remain?: number;
  tray_color?: string;
  tray_info_idx?: string;
  tray_sub_brands?: string;
  tray_type?: string;
  tray_uuid?: string;
};

// Partial type definition for printer status
export type PrinterStatus = {
  print: {
    ams?: {
      tray_exist_bits?: string | number;
      ams?: {
        id: string;
        tray: PrinterTray[];
      }[];
    };
    vt_tray?: PrinterTray;
  };
};
