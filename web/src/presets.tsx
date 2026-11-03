import {
  AppstoreOutlined,
  CarOutlined,
  EnvironmentOutlined,
  NodeIndexOutlined,
  QuestionCircleOutlined,
  SwapOutlined,
  SyncOutlined,
  TeamOutlined,
} from '@ant-design/icons';
import { useMemo } from 'react';
import type { ReactNode } from 'react';
import type { TaskPresetInfo } from './api';
import { useWorkbench } from './context';

export interface TaskPreset {
  key: string;
  icon: ReactNode;
  label: string;
  description: string;
  prompt: string;
  systems: string[];
}

/** 剧本里 icon 字段可用的图标名；未知名字用通用图标。 */
const ICONS: Record<string, ReactNode> = {
  team: <TeamOutlined />,
  environment: <EnvironmentOutlined />,
  'node-index': <NodeIndexOutlined />,
  car: <CarOutlined />,
  swap: <SwapOutlined />,
  sync: <SyncOutlined />,
};

const HELP_PRESET: TaskPreset = {
  key: 'help',
  icon: <QuestionCircleOutlined />,
  label: '能做什么',
  description: '介绍已接入的系统和可以发出的指令',
  prompt: '介绍一下你能做什么',
  systems: [],
};

function toPreset(p: TaskPresetInfo): TaskPreset {
  return {
    key: p.id,
    icon: (p.icon && ICONS[p.icon]) || <AppstoreOutlined />,
    label: p.title,
    description: p.description || p.prompt,
    prompt: p.prompt,
    systems: p.systems,
  };
}

/**
 * 常用任务：来自各系统场景剧本（/api/config 的 presets），接入新系统、新增剧本后自动出现，最后附上“能做什么”。
 * 规则模式只认识这些指令；接入模型后它们就是普通的自然语言指令。
 */
export function useTaskPresets(): TaskPreset[] {
  const { config } = useWorkbench();
  return useMemo(() => [...(config?.presets || []).map(toPreset), HELP_PRESET], [config]);
}
