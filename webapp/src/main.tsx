import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import "./styles.css";
import "./admin/admin.css";
import { openStartRoute } from "./router";
import { initTelegram } from "./telegram";

initTelegram();
openStartRoute();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
