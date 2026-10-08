import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import "./i18n";
import { useSettings } from "./store/settings";
import i18n from "./i18n";
import "./styles.css";

void i18n.changeLanguage(useSettings.getState().language);

const root = document.getElementById("root");
if (root) {
  createRoot(root).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
}
