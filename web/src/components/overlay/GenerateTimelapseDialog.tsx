import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { FrigateConfig } from "@/types/frigateConfig";
import { GenerateTimelapseResponse } from "@/types/timelapse";
import axios from "axios";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import useSWR from "swr";

type GenerateTimelapseDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onGenerated?: () => void;
};

function toEpochSeconds(value: string): number | null {
  if (!value) {
    return null;
  }

  const ms = new Date(value).getTime();
  return Number.isNaN(ms) ? null : Math.floor(ms / 1000);
}

export default function GenerateTimelapseDialog({
  open,
  onOpenChange,
  onGenerated,
}: GenerateTimelapseDialogProps) {
  const { t } = useTranslation(["views/exports"]);
  const { data: config } = useSWR<FrigateConfig>("config");

  const [camera, setCamera] = useState<string>("");
  const [startDate, setStartDate] = useState<string>("");
  const [endDate, setEndDate] = useState<string>("");
  const [name, setName] = useState<string>("");
  const [speed, setSpeed] = useState<string>("3600");
  const [fps, setFps] = useState<string>("30");
  const [isSubmitting, setIsSubmitting] = useState(false);

  const cameras = useMemo<string[]>(() => {
    if (!config) {
      return [];
    }

    return Object.values(config.cameras)
      .filter((cam) => cam.timelapse?.enabled)
      .map((cam) => cam.name);
  }, [config]);

  const onSubmit = async () => {
    const start = toEpochSeconds(startDate);
    const end = toEpochSeconds(endDate);

    if (!camera || start == null || end == null) {
      return;
    }

    if (end <= start) {
      toast.error(t("timelapse.invalidRange"), { position: "top-center" });
      return;
    }

    const speedValue = Number(speed);
    const fpsValue = Number(fps);

    if (!Number.isFinite(speedValue) || speedValue <= 0 || fpsValue < 1) {
      toast.error(t("timelapse.invalidSpeed"), { position: "top-center" });
      return;
    }

    setIsSubmitting(true);

    try {
      const { data } = await axios.post<GenerateTimelapseResponse>(
        `timelapse/${camera}/generate/start/${start}/end/${end}`,
        null,
        {
          params: {
            speed: speedValue,
            fps: fpsValue,
            ...(name ? { name } : {}),
          },
        },
      );

      if (data.success) {
        toast.success(t("toast.success.timelapseStarted"), {
          position: "top-center",
        });
        onGenerated?.();
        onOpenChange(false);
      } else {
        toast.error(
          t("toast.error.timelapseFailed", { errorMessage: data.message }),
          { position: "top-center" },
        );
      }
    } catch (error) {
      const errorMessage =
        axios.isAxiosError(error) && error.response?.data?.message
          ? error.response.data.message
          : "Unknown error";
      toast.error(t("toast.error.timelapseFailed", { errorMessage }), {
        position: "top-center",
      });
    } finally {
      setIsSubmitting(false);
    }
  };

  const canSubmit =
    camera !== "" && startDate !== "" && endDate !== "" && !isSubmitting;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("timelapse.title")}</DialogTitle>
          <DialogDescription>{t("timelapse.description")}</DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="timelapse-camera">{t("timelapse.camera")}</Label>
            <Select value={camera} onValueChange={setCamera}>
              <SelectTrigger id="timelapse-camera">
                <SelectValue placeholder={t("timelapse.cameraPlaceholder")} />
              </SelectTrigger>
              <SelectContent>
                {cameras.map((cam) => (
                  <SelectItem key={cam} value={cam}>
                    {cam.replaceAll("_", " ")}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="timelapse-start">{t("timelapse.startDate")}</Label>
            <Input
              id="timelapse-start"
              type="datetime-local"
              value={startDate}
              onChange={(e) => setStartDate(e.target.value)}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="timelapse-end">{t("timelapse.endDate")}</Label>
            <Input
              id="timelapse-end"
              type="datetime-local"
              value={endDate}
              onChange={(e) => setEndDate(e.target.value)}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="timelapse-speed">{t("timelapse.speed")}</Label>
            <Input
              id="timelapse-speed"
              type="number"
              min={1}
              value={speed}
              onChange={(e) => setSpeed(e.target.value)}
            />
            <p className="text-xs text-muted-foreground">
              {t("timelapse.speedHint")}
            </p>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="timelapse-fps">{t("timelapse.fps")}</Label>
            <Input
              id="timelapse-fps"
              type="number"
              min={1}
              value={fps}
              onChange={(e) => setFps(e.target.value)}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="timelapse-name">{t("timelapse.name")}</Label>
            <Input
              id="timelapse-name"
              value={name}
              placeholder={t("timelapse.namePlaceholder")}
              onChange={(e) => setName(e.target.value)}
            />
          </div>
        </div>
        <DialogFooter>
          <Button
            variant="select"
            disabled={!canSubmit}
            onClick={onSubmit}
            aria-label={t("timelapse.generate")}
          >
            {t("timelapse.generate")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
