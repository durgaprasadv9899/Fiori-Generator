// Device frame (desktop / tablet / phone) around an iframe with animated resize.
import { motion } from "framer-motion";
import "./designers.css";

export type Device = "desktop" | "tablet" | "phone";
const SIZES: Record<Device, { w: number | string; h: number }> = {
  desktop: { w: "100%", h: 660 },
  tablet: { w: 820, h: 640 },
  phone: { w: 390, h: 700 },
};

export function DeviceFrame({ device, html, title }: { device: Device; html: string; title?: string }) {
  const s = SIZES[device];
  return (
    <div className="ev-device-stage">
      <motion.div
        className={`ev-device ev-device--${device}`}
        animate={{ width: s.w, height: s.h }}
        initial={false}
        transition={{ type: "spring", stiffness: 160, damping: 22 }}
      >
        {device === "desktop" ? (
          <div className="ev-device__bar"><span /><span /><span /><div className="ev-device__url">{title || "preview"}</div></div>
        ) : (
          <div className="ev-device__notch" />
        )}
        <iframe className="ev-device__screen" title="App preview" srcDoc={html} sandbox="allow-scripts allow-same-origin allow-popups allow-forms" />
      </motion.div>
    </div>
  );
}
