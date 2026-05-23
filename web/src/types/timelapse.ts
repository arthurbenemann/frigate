export type TimelapseSegment = {
  id: string;
  camera: string;
  path: string;
  start_time: number;
  end_time: number;
  duration: number;
};

export type GenerateTimelapseResponse = {
  success: boolean;
  message: string;
  export_id?: string | null;
};
