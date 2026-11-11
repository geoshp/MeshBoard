// Встроенные SVG-иконки (без внешних CDN).
const P = (d: string) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <path d={d} />
  </svg>
);
export const IconChannels = () => P("M4 9h16M4 15h16M10 3L8 21M16 3l-2 18");
export const IconDm = () => P("M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z");
export const IconRepeater = () => P("M12 13v8M8 21h8M12 13a2 2 0 1 0 0-4 2 2 0 0 0 0 4zM7.8 6.8a6 6 0 0 0 0 8.4M16.2 6.8a6 6 0 0 1 0 8.4M5 4a10 10 0 0 0 0 14M19 4a10 10 0 0 1 0 14");
export const IconCharts = () => P("M4 20V10M10 20V4M16 20v-7M22 20H2");
export const IconMap = () => P("M9 4L3 6v14l6-2 6 2 6-2V4l-6 2-6-2zM9 4v14M15 6v14");
export const IconNode = () => P("M5 8h14v10H5zM8 8V5h8v3M9 13h.01M12 13h.01M15 13h.01");
export const IconBack = () => P("M15 18l-6-6 6-6");
export const IconSend = () => P("M4 12l16-8-6 16-2.5-6.5L4 12z");
export const IconRoute = () => P("M6 19a2 2 0 1 0 0-4 2 2 0 0 0 0 4zM18 9a2 2 0 1 0 0-4 2 2 0 0 0 0 4zM8 17h7a3 3 0 0 0 0-6H9a3 3 0 0 1 0-6h7");
export const Logo = () => (
  <svg viewBox="0 0 32 32">
    <g stroke="#2ee6a6" strokeWidth="2.2" fill="none">
      <circle cx="9" cy="22" r="3" />
      <circle cx="23" cy="22" r="3" />
      <circle cx="16" cy="9" r="3" />
      <path d="M11 20l4-8M21 20l-4-8M12 22h8" />
    </g>
  </svg>
);
