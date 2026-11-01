// Tabs of the console's right panel, in Alt+1..5 order.
export const TABS = ['evidence', 'risk', 'actions', 'history', 'trace'] as const
export type TabId = (typeof TABS)[number]
