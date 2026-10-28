/**
 * 视觉基调：调度台。
 * - 主色：信号青（操作、焦点、当前会话）。
 * - 琥珀色只用于写操作与确认，任何“会改业务数据”的东西都带它。
 * - 每个接入系统有自己的标识色（来自接入清单 color），像地铁线路色一样贯穿执行过程、页签和工具目录。
 */
import { theme, type ThemeConfig } from 'antd';
import type { CSSProperties } from 'react';

export const FONT_FAMILY =
  '-apple-system, BlinkMacSystemFont, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans SC", "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif';
export const FONT_MONO = '"JetBrains Mono", "SFMono-Regular", "Cascadia Mono", Menlo, Consolas, "Liberation Mono", monospace';

/**
 * 主色色阶手工给出：antd 按算法从深青色推导出的浅色（如 colorPrimaryBg）偏灰发闷，选中态、浅底按钮会显脏。
 */
const PRIMARY_SCALE = {
  light: {
    colorPrimaryBg: '#E6F2F1',
    colorPrimaryBgHover: '#D2E9E6',
    colorPrimaryBorder: '#A4D3CE',
    colorPrimaryBorderHover: '#72BAB3',
    colorPrimaryHover: '#1E8F8C',
    colorPrimaryActive: '#0A6463',
    colorPrimaryTextHover: '#1E8F8C',
    colorPrimaryText: '#0E7C7B',
    colorPrimaryTextActive: '#0A6463',
  },
  dark: {
    colorPrimaryBg: '#14302F',
    colorPrimaryBgHover: '#183B3A',
    colorPrimaryBorder: '#1F5653',
    colorPrimaryBorderHover: '#25726E',
    colorPrimaryHover: '#3CBDB5',
    colorPrimaryActive: '#1E8A84',
    colorPrimaryTextHover: '#4CC7BF',
    colorPrimaryText: '#36B5AD',
    colorPrimaryTextActive: '#1E8A84',
  },
};

const LIGHT = {
  primary: '#0E7C7B',
  layout: '#F2F4F6',
  container: '#FFFFFF',
  sidebar: '#F7F8FA',
  write: '#B45309',
  writeBg: '#FFF8EB',
  writeBorder: '#F2C98B',
  userBubble: '#E3F2F1',
};

const DARK = {
  primary: '#26A69F',
  layout: '#0F1417',
  container: '#161D21',
  sidebar: '#12181B',
  write: '#E3A33F',
  writeBg: 'rgba(227, 163, 63, 0.10)',
  writeBorder: 'rgba(227, 163, 63, 0.38)',
  userBubble: 'rgba(38, 166, 159, 0.16)',
};

export function buildTheme(dark: boolean): ThemeConfig {
  const p = dark ? DARK : LIGHT;
  return {
    algorithm: dark ? theme.darkAlgorithm : theme.defaultAlgorithm,
    token: {
      colorPrimary: p.primary,
      ...(dark ? PRIMARY_SCALE.dark : PRIMARY_SCALE.light),
      colorInfo: p.primary,
      colorLink: p.primary,
      colorSuccess: dark ? '#3DAE7A' : '#1F8A4C',
      colorWarning: p.write,
      colorError: dark ? '#EF6A5F' : '#CF3A31',
      colorBgLayout: p.layout,
      colorBgContainer: p.container,
      borderRadius: 8,
      fontFamily: FONT_FAMILY,
      fontFamilyCode: FONT_MONO,
      fontSize: 14,
    },
    components: {
      Table: { cellPaddingBlockSM: 6, cellPaddingInlineSM: 8 },
      Tabs: { horizontalItemPadding: '12px 4px', horizontalItemGutter: 20 },
    },
  };
}

/** 自定义 CSS 变量：从当前主题 token 生成，挂在应用根节点上。 */
export function cssVars(dark: boolean, token: ReturnType<typeof theme.useToken>['token']): CSSProperties {
  const p = dark ? DARK : LIGHT;
  return {
    '--bt-primary': token.colorPrimary,
    '--bt-primary-bg': token.colorPrimaryBg,
    '--bt-text': token.colorText,
    '--bt-text-2': token.colorTextSecondary,
    '--bt-text-3': token.colorTextTertiary,
    '--bt-text-4': token.colorTextQuaternary,
    '--bt-border': token.colorBorderSecondary,
    '--bt-border-strong': token.colorBorder,
    '--bt-bg-layout': p.layout,
    '--bt-bg-container': p.container,
    '--bt-bg-sidebar': p.sidebar,
    '--bt-bg-fill': token.colorFillQuaternary,
    '--bt-bg-fill-2': token.colorFillTertiary,
    '--bt-success': token.colorSuccess,
    '--bt-error': token.colorError,
    '--bt-write': p.write,
    '--bt-write-bg': p.writeBg,
    '--bt-write-border': p.writeBorder,
    '--bt-write-on': dark ? '#1C1206' : '#FFFFFF',
    '--bt-user-bubble': p.userBubble,
    '--bt-font': FONT_FAMILY,
    '--bt-mono': FONT_MONO,
  } as CSSProperties;
}
