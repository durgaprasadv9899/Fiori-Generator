import React from "react";
import ReactDOM from "react-dom/client";
import { HashRouter } from "react-router-dom";
import { ThemeProvider } from "@ui5/webcomponents-react";
import "@ui5/webcomponents-react/dist/Assets.js";
import "@ui5/webcomponents-icons/dist/AllIcons.js";
import "./app/global.css";
import { JobProvider } from "./jobs/JobProvider";
import { MeProvider } from "./app/MeProvider";
import { AppRoutes } from "./app/routes";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ThemeProvider>
      <HashRouter>
        <MeProvider>
          <JobProvider>
            <AppRoutes />
          </JobProvider>
        </MeProvider>
      </HashRouter>
    </ThemeProvider>
  </React.StrictMode>
);
