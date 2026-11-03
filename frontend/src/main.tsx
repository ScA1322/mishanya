import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { applyPalette, applyTheme, readPalette, readTheme } from "./ui";
import "./styles.css";

applyTheme(readTheme());
applyPalette(readPalette());

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
