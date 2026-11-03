// Значки - свои SVG (линия 1.8, цвет - currentColor), чтобы не тянуть библиотеку иконок.

import type { ReactElement } from "react";

const base = {
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.8,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
};

export const Icon = {
  calendar: (): ReactElement => (
    <svg {...base}>
      <rect x="3.5" y="5" width="17" height="15" rx="3" />
      <path d="M3.5 10h17M8 3v4M16 3v4" />
    </svg>
  ),
  ticket: (): ReactElement => (
    <svg {...base}>
      <path d="M4 7.5A2.5 2.5 0 0 1 6.5 5h11A2.5 2.5 0 0 1 20 7.5v2a2.5 2.5 0 0 0 0 5v2a2.5 2.5 0 0 1-2.5 2.5h-11A2.5 2.5 0 0 1 4 16.5v-2a2.5 2.5 0 0 0 0-5z" />
      <path d="M14 5.5v13" strokeDasharray="2 2.5" />
    </svg>
  ),
  user: (): ReactElement => (
    <svg {...base}>
      <circle cx="12" cy="8.5" r="3.8" />
      <path d="M4.5 20c1.3-3.6 4.2-5.5 7.5-5.5s6.2 1.9 7.5 5.5" />
    </svg>
  ),
  info: (): ReactElement => (
    <svg {...base}>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 11v5.5M12 7.8v.2" />
    </svg>
  ),
  pin: (): ReactElement => (
    <svg {...base}>
      <path d="M12 21s-6.5-6-6.5-11a6.5 6.5 0 0 1 13 0c0 5-6.5 11-6.5 11z" />
      <circle cx="12" cy="10" r="2.4" />
    </svg>
  ),
  clock: (): ReactElement => (
    <svg {...base}>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 7.5V12l3 2" />
    </svg>
  ),
  people: (): ReactElement => (
    <svg {...base}>
      <circle cx="9" cy="9" r="3.2" />
      <path d="M3.5 19c.9-3 3-4.6 5.5-4.6s4.6 1.6 5.5 4.6" />
      <circle cx="17" cy="9.5" r="2.6" />
      <path d="M16 14.6c2.2.1 3.8 1.5 4.5 4" />
    </svg>
  ),
  chevron: (): ReactElement => (
    <svg {...base} className="chevron">
      <path d="M9.5 6l6 6-6 6" />
    </svg>
  ),
  back: (): ReactElement => (
    <svg {...base} strokeWidth={2.2}>
      <path d="M14.5 6l-6 6 6 6" />
    </svg>
  ),
  check: (): ReactElement => (
    <svg {...base} strokeWidth={2.6}>
      <path d="M5.5 12.5l4.2 4.2 8.8-9.4" />
    </svg>
  ),
  book: (): ReactElement => (
    <svg {...base}>
      <path d="M5 4.5h9.5A3.5 3.5 0 0 1 18 8v11.5H8.5A3.5 3.5 0 0 1 5 16z" />
      <path d="M5 16a3.5 3.5 0 0 1 3.5-3.5H18M9 8h5" />
    </svg>
  ),
  chat: (): ReactElement => (
    <svg {...base}>
      <path d="M5 18.5V7a2.5 2.5 0 0 1 2.5-2.5h9A2.5 2.5 0 0 1 19 7v6.5a2.5 2.5 0 0 1-2.5 2.5H9z" />
      <path d="M9 9.5h6M9 12.5h4" />
    </svg>
  ),
  card: (): ReactElement => (
    <svg {...base}>
      <rect x="3" y="5.5" width="18" height="13" rx="2.5" />
      <path d="M3 10h18M7 15h3" />
    </svg>
  ),
  file: (): ReactElement => (
    <svg {...base}>
      <path d="M13.5 3.5H7.5A2.5 2.5 0 0 0 5 6v12a2.5 2.5 0 0 0 2.5 2.5h9A2.5 2.5 0 0 0 19 18V9z" />
      <path d="M13.5 3.5V9H19M9 13.5h6M9 16.5h4" />
    </svg>
  ),
  phone: (): ReactElement => (
    <svg {...base}>
      <rect x="7" y="2.5" width="10" height="19" rx="2.5" />
      <path d="M11 18.5h2" />
    </svg>
  ),
  bell: (): ReactElement => (
    <svg {...base}>
      <path d="M6.5 16.5V11a5.5 5.5 0 0 1 11 0v5.5l1.5 1.5H5z" />
      <path d="M10 20.5a2 2 0 0 0 4 0" />
    </svg>
  ),
  gift: (): ReactElement => (
    <svg {...base}>
      <rect x="4" y="9" width="16" height="11" rx="2" />
      <path d="M3 9h18M12 9v11M12 9S10.5 4 8 4.5 7 8.5 12 9zM12 9s1.5-5 4-4.5S17 8.5 12 9z" />
    </svg>
  ),
  share: (): ReactElement => (
    <svg {...base}>
      <path d="M12 15V4M8 7.5L12 3.5l4 4" />
      <path d="M6 11v7.5A2 2 0 0 0 8 20.5h8a2 2 0 0 0 2-2V11" />
    </svg>
  ),
  tooth: (): ReactElement => (
    <svg viewBox="0 0 24 24" fill="#fff">
      <path d="M7.5 3.5C5 3.5 3.5 5.5 3.5 8.5c0 3.4 1.4 4.4 2.3 8.1.5 2.3 1 4.2 2.4 4.2 1.9 0 1.5-4.3 3.3-5.6.3-.2 1-.2 1.2 0 1.8 1.3 1.4 5.6 3.3 5.6 1.4 0 1.9-1.9 2.4-4.2.9-3.7 2.3-4.7 2.3-8.1 0-3-1.5-5-4-5-1.9 0-2.9.9-4.5.9s-2.6-.9-4.5-.9z" />
    </svg>
  ),
  wave: (): ReactElement => (
    <svg {...base}>
      <path d="M3 9c3 0 3-2 6-2s3 2 6 2 3-2 6-2M3 15c3 0 3-2 6-2s3 2 6 2 3-2 6-2" />
    </svg>
  ),
  receipt: (): ReactElement => (
    <svg {...base}>
      <path d="M6 3.5h12v17l-2.4-1.6-2.4 1.6-2.4-1.6-2.4 1.6L6 20.5z" />
      <path d="M9 8h6M9 11.5h6M9 15h3.5" />
    </svg>
  ),
  layers: (): ReactElement => (
    <svg {...base}>
      <path d="M12 3.5l8.5 4.5L12 12.5 3.5 8z" />
      <path d="M3.5 12L12 16.5 20.5 12M3.5 16L12 20.5 20.5 16" />
    </svg>
  ),
  key: (): ReactElement => (
    <svg {...base}>
      <circle cx="8" cy="15" r="4" />
      <path d="M11 12l8.5-8.5M16.5 6.5l2.5 2.5M14 9l2 2" />
    </svg>
  ),
  search: (): ReactElement => (
    <svg {...base}>
      <circle cx="11" cy="11" r="6.5" />
      <path d="M16 16l4.5 4.5" />
    </svg>
  ),
  plus: (): ReactElement => (
    <svg {...base} strokeWidth={2.2}>
      <path d="M12 5v14M5 12h14" />
    </svg>
  ),
  pencil: (): ReactElement => (
    <svg {...base}>
      <path d="M15.5 5.5l3 3L8 19H5v-3z" />
      <path d="M13.5 7.5l3 3" />
    </svg>
  ),
  box: (): ReactElement => (
    <svg {...base}>
      <rect x="3.5" y="4.5" width="17" height="4.5" rx="1.5" />
      <path d="M5 9v9a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V9M10 13h4" />
    </svg>
  ),
  trash: (): ReactElement => (
    <svg {...base}>
      <path d="M4.5 7h15M9.5 7V4.5h5V7M6.5 7l1 12.5h9l1-12.5M10 11v5M14 11v5" />
    </svg>
  ),
  copy: (): ReactElement => (
    <svg {...base}>
      <rect x="8.5" y="8.5" width="11" height="11" rx="2.5" />
      <path d="M15.5 8.5V6.5a2 2 0 0 0-2-2h-7a2 2 0 0 0-2 2v7a2 2 0 0 0 2 2h2" />
    </svg>
  ),
  send: (): ReactElement => (
    <svg {...base}>
      <path d="M20.5 3.5L10 14M20.5 3.5l-6.5 17-4-6.5-6.5-4z" />
    </svg>
  ),
  image: (): ReactElement => (
    <svg {...base}>
      <rect x="3.5" y="4.5" width="17" height="15" rx="3" />
      <circle cx="9" cy="10" r="1.8" />
      <path d="M20.5 15.5l-5-5L6 19.5" />
    </svg>
  ),
  upload: (): ReactElement => (
    <svg {...base}>
      <path d="M12 15.5V4M7.5 8.5L12 4l4.5 4.5M5 15v3.5A2 2 0 0 0 7 20.5h10a2 2 0 0 0 2-2V15" />
    </svg>
  ),
  link: (): ReactElement => (
    <svg {...base}>
      <path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7L11.5 6.8" />
      <path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1.5-1.5" />
    </svg>
  ),
  money: (): ReactElement => (
    <svg {...base}>
      <rect x="2.5" y="6" width="19" height="12" rx="2.5" />
      <circle cx="12" cy="12" r="2.6" />
      <path d="M6 9.5v5M18 9.5v5" />
    </svg>
  ),
  close: (): ReactElement => (
    <svg {...base} strokeWidth={2.2}>
      <path d="M6.5 6.5l11 11M17.5 6.5l-11 11" />
    </svg>
  ),
  refresh: (): ReactElement => (
    <svg {...base}>
      <path d="M19.5 12a7.5 7.5 0 1 1-2.2-5.3M19.5 4.5v4h-4" />
    </svg>
  ),
};
