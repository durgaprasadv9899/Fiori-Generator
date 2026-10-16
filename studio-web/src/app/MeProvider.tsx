// Signed-in user, AI status and workspace info (GET api/me), plus the UI theme (light/dark).
import React, { createContext, useCallback, useContext, useEffect, useState } from "react";
import { setTheme } from "@ui5/webcomponents-base/dist/config/Theme.js";
import { attachBoot } from "@ui5/webcomponents-base/dist/Boot.js";
import { api } from "../api/client";
import type { Me } from "../api/types";

interface Ctx {
  me: Me | null;
  error: string;
  reload: () => void;
  dark: boolean;
  toggleDark: () => void;
  can: (permission: string) => boolean;
}
const MeContext = createContext<Ctx>(null as unknown as Ctx);

export function MeProvider({ children }: { children: React.ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [error, setError] = useState("");
  const [dark, setDark] = useState(() => {
    try { return localStorage.getItem("evoke.theme") === "dark"; } catch { return false; }
  });
  const reload = useCallback(() => {
    api.get<Me>("api/me").then((m) => { setMe(m); setError(""); }).catch((e) => setError(e.message));
  }, []);
  useEffect(reload, [reload]);
  useEffect(() => {
    // set after UI5 has booted — before boot, boot would re-apply its default (light) theme
    attachBoot(() => setTheme(dark ? "sap_horizon_dark" : "sap_horizon"));
    document.documentElement.dataset.theme = dark ? "dark" : "light";
    try { localStorage.setItem("evoke.theme", dark ? "dark" : "light"); } catch { /* ignore */ }
  }, [dark]);
  const can = (p: string) => {
    if (!me) return false;
    if (me.permissions) return me.permissions.includes(p);
    return me.user.roles.includes("admin") || me.user.roles.includes("developer");
  };
  return (
    <MeContext.Provider value={{ me, error, reload, dark, toggleDark: () => setDark((d) => !d), can }}>
      {children}
    </MeContext.Provider>
  );
}

export const useMe = () => useContext(MeContext);
