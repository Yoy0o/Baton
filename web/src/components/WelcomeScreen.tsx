import { Prompts, Welcome } from '@ant-design/x';
import type { CSSProperties } from 'react';
import { bridgeTools, useWorkbench } from '../context';
import { TASK_PRESETS } from './Composer';
import { LogoMark } from './Logo';

/** 新对话的空白页：说明能做什么，并给出可以直接发送的任务。 */
export function WelcomeScreen({ onPick }: { onPick: (prompt: string) => void }) {
  const { config, systemsMeta, bridges } = useWorkbench();
  const systems = config?.systems || [];

  return (
    <div className="bt-welcome">
      <Welcome
        variant="borderless"
        icon={<LogoMark size={44} />}
        title="今天要调度什么？"
        description="用一句话描述任务，我会在下面这些系统之间查询、计算、在地图上展示；需要写入业务系统时，先请你确认。"
      />
      <div className="bt-welcome-systems" aria-label="已接入的系统">
        {systems.map((s) => {
          const ui = s.embed_url ? bridgeTools(bridges, s.system).length > 0 : null;
          return (
            <span key={s.system} className="bt-sys-pill" style={{ '--c': s.color } as CSSProperties}>
              <i />
              {s.title}
              <em>
                {s.api_tools} 个接口{ui === null ? '' : ui ? '，界面已接入' : '，界面未打开'}
              </em>
            </span>
          );
        })}
      </div>
      <Prompts
        className="bt-presets"
        title="试试这些任务"
        wrap
        items={TASK_PRESETS.slice(0, 4).map((t) => ({
          key: t.key,
          icon: t.icon,
          label: t.label,
          description: (
            <span className="bt-preset-desc">
              {t.description}
              <span className="bt-preset-route" aria-hidden>
                {t.systems.map((s) => (
                  <i key={s} style={{ '--c': systemsMeta[s]?.color || '#64748B' } as CSSProperties} title={systemsMeta[s]?.title} />
                ))}
              </span>
            </span>
          ),
        }))}
        onItemClick={({ data }) => {
          const preset = TASK_PRESETS.find((t) => t.key === data.key);
          if (preset) onPick(preset.prompt);
        }}
      />
    </div>
  );
}
