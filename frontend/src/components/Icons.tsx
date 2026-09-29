// Inline icons (no external assets). Decorative: always paired with visible text.

import type { ReactNode } from 'react'

function Svg({ children, size = 16 }: { children: ReactNode; size?: number }) {
  return (
    <svg
      className="icon"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {children}
    </svg>
  )
}

type P = { size?: number }

export const IconRule = ({ size }: P) => (
  <Svg size={size}>
    <path d="M12 3v18M5 7h14M7 7l-3 7a3 3 0 0 0 6 0L7 7zM17 7l-3 7a3 3 0 0 0 6 0l-3-7zM8 21h8" />
  </Svg>
)
export const IconMl = ({ size }: P) => (
  <Svg size={size}>
    <path d="M4 19V5M4 19h16M8 15l3-4 3 2 5-6" />
  </Svg>
)
export const IconSpark = ({ size }: P) => (
  <Svg size={size}>
    <path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8zM19 17l.7 1.8 1.8.7-1.8.7L19 22l-.7-1.8-1.8-.7 1.8-.7z" />
  </Svg>
)
export const IconTool = ({ size }: P) => (
  <Svg size={size}>
    <path d="M14.7 6.3a4 4 0 0 0-5.4 5.2L3 17.8V21h3.2l6.3-6.3a4 4 0 0 0 5.2-5.4l-2.6 2.6-2.4-.6-.6-2.4z" />
  </Svg>
)
export const IconHuman = ({ size }: P) => (
  <Svg size={size}>
    <circle cx="12" cy="8" r="4" />
    <path d="M4 21a8 8 0 0 1 16 0" />
  </Svg>
)
export const IconTemplate = ({ size }: P) => (
  <Svg size={size}>
    <path d="M6 3h9l4 4v14H6zM14 3v5h5M9 13h7M9 17h5" />
  </Svg>
)
export const IconShield = ({ size }: P) => (
  <Svg size={size}>
    <path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6zM8.5 12l2.5 2.5 4.5-5" />
  </Svg>
)
export const IconQuote = ({ size }: P) => (
  <Svg size={size}>
    <path d="M4 5h16v11H9l-5 4z" />
  </Svg>
)
export const IconAlert = ({ size }: P) => (
  <Svg size={size}>
    <path d="M12 3l10 18H2zM12 10v4M12 17.5v.5" />
  </Svg>
)
export const IconClock = ({ size }: P) => (
  <Svg size={size}>
    <circle cx="12" cy="12" r="9" />
    <path d="M12 7v5l3 2" />
  </Svg>
)
export const IconCheck = ({ size }: P) => (
  <Svg size={size}>
    <path d="M5 12.5l4.5 4.5L19 7" />
  </Svg>
)
export const IconX = ({ size }: P) => (
  <Svg size={size}>
    <path d="M6 6l12 12M18 6L6 18" />
  </Svg>
)
export const IconSend = ({ size }: P) => (
  <Svg size={size}>
    <path d="M4 12l16-8-6 16-2.5-6.5z" />
  </Svg>
)
export const IconBack = ({ size }: P) => (
  <Svg size={size}>
    <path d="M15 5l-7 7 7 7" />
  </Svg>
)
export const IconRefresh = ({ size }: P) => (
  <Svg size={size}>
    <path d="M20 11a8 8 0 0 0-14.3-4.9L4 8M4 4v4h4M4 13a8 8 0 0 0 14.3 4.9L20 16M20 20v-4h-4" />
  </Svg>
)
export const IconChevron = ({ size }: P) => (
  <Svg size={size}>
    <path d="M6 15l6-6 6 6" />
  </Svg>
)
export const IconCopy = ({ size }: P) => (
  <Svg size={size}>
    <rect x="8" y="8" width="12" height="12" rx="2" />
    <path d="M16 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3" />
  </Svg>
)
export const IconLock = ({ size }: P) => (
  <Svg size={size}>
    <rect x="5" y="11" width="14" height="10" rx="2" />
    <path d="M8 11V8a4 4 0 0 1 8 0v3" />
  </Svg>
)
export const IconHand = ({ size }: P) => (
  <Svg size={size}>
    <path d="M8 13V5.5a1.5 1.5 0 0 1 3 0V12M11 11V4.5a1.5 1.5 0 0 1 3 0V12M14 11.5V6a1.5 1.5 0 0 1 3 0v8a7 7 0 0 1-7 7h-.5A6.5 6.5 0 0 1 4 16.4L2.8 13a1.5 1.5 0 0 1 2.7-1.3L8 15" />
  </Svg>
)
export const IconInbox = ({ size }: P) => (
  <Svg size={size}>
    <path d="M4 13h4l1.5 3h5L16 13h4M5.5 5h13L21 13v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-5z" />
  </Svg>
)
export const IconPulse = ({ size }: P) => (
  <Svg size={size}>
    <path d="M3 12h4l2-6 4 12 2-6h6" />
  </Svg>
)
export const IconChart = ({ size }: P) => (
  <Svg size={size}>
    <path d="M4 20V10M10 20V4M16 20v-7M22 20H2" />
  </Svg>
)
export const IconScale = ({ size }: P) => (
  <Svg size={size}>
    <path d="M12 3v18M7 21h10M5 7h14M5 7l-3 6a3 3 0 0 0 6 0L5 7zM19 7l-3 6a3 3 0 0 0 6 0l-3-6z" />
  </Svg>
)
export const IconLogout = ({ size }: P) => (
  <Svg size={size}>
    <path d="M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3M10 17l-5-5 5-5M5 12h11" />
  </Svg>
)
export const IconSwitch = ({ size }: P) => (
  <Svg size={size}>
    <rect x="2" y="7" width="20" height="10" rx="5" />
    <circle cx="16" cy="12" r="3" />
  </Svg>
)
