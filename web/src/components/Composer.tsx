import { CarOutlined, EnvironmentOutlined, NodeIndexOutlined, QuestionCircleOutlined, TeamOutlined } from '@ant-design/icons';
import { Sender, Suggestion } from '@ant-design/x';
import { useState } from 'react';

export interface TaskPreset {
  key: string;
  icon: React.ReactNode;
  label: string;
  description: string;
  prompt: string;
  systems: string[];
}

/** 内置的常用任务（规则模式能识别的指令）；接入真实模型后同样可用（就是普通的自然语言指令）。 */
export const TASK_PRESETS: TaskPreset[] = [
  {
    key: 'regroup',
    icon: <TeamOutlined />,
    label: '重组司机组',
    description: '按当前位置把华东区明天早班的司机分成 5 组，确认后写回调度系统',
    prompt: '把华东区明天早班的司机按当前位置重新分成 5 组，每组不超过 8 人，先给我看地图和名单，确认后同步到调度系统',
    systems: ['tms', 'fleet', 'analytics', 'dispatch'],
  },
  {
    key: 'stops',
    icon: <EnvironmentOutlined />,
    label: '挂车停留地图',
    description: '过去 24 小时停留超过 2 小时的挂车，在车队地图上按客户着色',
    prompt: '在地图上显示过去 24 小时停留超过 2 小时的挂车，按客户着色',
    systems: ['fleet', 'results'],
  },
  {
    key: 'track',
    icon: <NodeIndexOutlined />,
    label: '轨迹回放',
    description: '调用车队地图台自带的回放功能，播放司机 D0012 今天的轨迹',
    prompt: '回放司机 D0012 今天的轨迹',
    systems: ['fleet'],
  },
  {
    key: 'locate',
    icon: <CarOutlined />,
    label: '定位司机',
    description: '在车队地图上定位并选中司机 D0012',
    prompt: '在地图上定位 D0012',
    systems: ['fleet'],
  },
  {
    key: 'help',
    icon: <QuestionCircleOutlined />,
    label: '能做什么',
    description: '介绍已接入的系统和可以发出的指令',
    prompt: '介绍一下你能做什么',
    systems: [],
  },
];

interface ComposerProps {
  running: boolean;
  disabled: boolean;
  suggestions: string[];
  onSend: (text: string) => void;
  onStop: () => void;
}

export function Composer({ running, disabled, suggestions, onSend, onStop }: ComposerProps) {
  const [value, setValue] = useState('');
  const submit = (text: string) => {
    if (!text.trim() || running) return;
    onSend(text);
    setValue('');
  };

  return (
    <div className="bt-composer">
      {suggestions.length > 0 && !running && (
        <div className="bt-followups" aria-label="接下来可以">
          {suggestions.map((s) => (
            <button key={s} type="button" className="bt-followup" onClick={() => submit(s)}>
              {s}
            </button>
          ))}
        </div>
      )}
      <Suggestion
        items={TASK_PRESETS.map((t) => ({
          value: t.prompt,
          icon: t.icon,
          label: (
            <span className="bt-sugg">
              <span className="bt-sugg-label">{t.label}</span>
              <span className="bt-sugg-desc">{t.description}</span>
            </span>
          ),
        }))}
        onSelect={(v) => setValue(v)}
        block
      >
        {({ onTrigger, onKeyDown }) => (
          <Sender
            className="bt-sender"
            value={value}
            loading={running}
            disabled={disabled}
            autoSize={{ minRows: 1, maxRows: 6 }}
            placeholder={running ? '任务执行中，可以随时停止' : '描述你的任务，输入 / 选择常用任务'}
            onChange={(v) => {
              if (v === '/') onTrigger();
              else if (!v.startsWith('/')) onTrigger(false);
              setValue(v);
            }}
            onKeyDown={onKeyDown}
            onSubmit={submit}
            onCancel={onStop}
          />
        )}
      </Suggestion>
      <div className="bt-composer-hint">Enter 发送，Shift + Enter 换行。写入业务系统前都会先请你确认。</div>
    </div>
  );
}
