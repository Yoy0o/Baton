import { createContext, useContext } from 'react';
import type { AppConfig, Bridges, FeedbackValue, SystemMeta } from './api';

export interface OpenResultArgs {
  resultId: string;
  colorBy?: string | null;
  title?: string;
}

export interface WorkbenchContextValue {
  config: AppConfig | null;
  clientId: string;
  conversationId: string | null;
  systemsMeta: Record<string, SystemMeta>;
  bridges: Bridges;
  running: boolean;
  feedback: Record<string, FeedbackValue>;
  openResult: (args: OpenResultArgs) => void;
  send: (text: string) => void;
  regenerate: () => void;
  decide: (confirmId: string, approved: boolean) => void;
  setFeedback: (runId: string, value: FeedbackValue | null) => void;
}

export const WorkbenchContext = createContext<WorkbenchContextValue | null>(null);

export function useWorkbench(): WorkbenchContextValue {
  const ctx = useContext(WorkbenchContext);
  if (!ctx) throw new Error('WorkbenchContext 未提供');
  return ctx;
}

/** 某个系统当前是否有已接入的页面，以及页面注册了哪些界面工具。 */
export function bridgeTools(bridges: Bridges, system: string): string[] {
  return [...new Set((bridges[system] || []).flatMap((c) => c.tools))];
}
