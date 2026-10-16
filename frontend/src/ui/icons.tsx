import type { Tab } from "../App";

/** Line icons for the tabs, drawn on a 24 px grid like Technic parts (stroke = currentColor). */
const PATHS: Record<Tab, string> = {
  start: "M8 5v14l11-7z",
  parts: "M3 10h18v9H3zM6 10V7h4v3M14 10V7h4v3",
  construct: "M14.5 4.5a4 4 0 0 0-5 5L4 15l5 5 5.5-5.5a4 4 0 0 0 5-5l-2.5 2.5-2.5-.5-.5-2.5z",
  tracks: "M4 19c0-6 4-6 7-8s3-6 9-6M4 19h3M17 5h3",
  simulate: "M12 3 20 7.5v9L12 21l-8-4.5v-9zM12 12l8-4.5M12 12 4 7.5M12 12v9",
  train: "M4 19h16M6 16l4-5 3 3 5-7M15 7h3v3",
  live: "M12 13a1 1 0 1 0 0-.01M8.5 9.5a5 5 0 0 0 0 7M15.5 9.5a5 5 0 0 1 0 7M5.5 6.5a9 9 0 0 0 0 13M18.5 6.5a9 9 0 0 1 0 13",
  race: "M5 21V4M5 4h12l-2 4 2 4H5",
  replay: "M4 12a8 8 0 1 0 2.4-5.7M4 4v4h4M11 9v6l4-3z",
  scans: "M4 8V5a1 1 0 0 1 1-1h3M16 4h3a1 1 0 0 1 1 1v3M20 16v3a1 1 0 0 1-1 1h-3M8 20H5a1 1 0 0 1-1-1v-3M4 12h16",
  team: "M9 11a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM3 20a6 6 0 0 1 12 0M16 5a3 3 0 0 1 0 6M18 14a5 5 0 0 1 3 6",
};

export function TabIcon({ tab }: { tab: Tab }) {
  return (
    <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" fill="none" stroke="currentColor"
         strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
      <path d={PATHS[tab]} />
    </svg>
  );
}

/** Mark: a 2x2 LEGO brick seen from above, studs in Technic red. */
export function AppMark() {
  return (
    <svg viewBox="0 0 32 32" width="30" height="30" aria-hidden="true">
      <rect x="1" y="1" width="30" height="30" rx="4" fill="#c91a09" />
      <rect x="1" y="1" width="30" height="30" rx="4" fill="none" stroke="#7a0f05" strokeWidth="2" />
      {[9, 23].map((x) => [9, 23].map((y) => (
        <g key={`${x}-${y}`}>
          <circle cx={x} cy={y} r="5.2" fill="#a91606" />
          <circle cx={x - 0.6} cy={y - 0.6} r="4.4" fill="#e0301c" />
        </g>
      )))}
    </svg>
  );
}
