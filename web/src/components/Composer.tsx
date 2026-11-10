import { Sender, Suggestion } from '@ant-design/x';
import { useState } from 'react';
import { useTaskPresets } from '../presets';

interface ComposerProps {
  running: boolean;
  disabled: boolean;
  suggestions: string[];
  onSend: (text: string) => void;
  onStop: () => void;
}

export function Composer({ running, disabled, suggestions, onSend, onStop }: ComposerProps) {
  const [value, setValue] = useState('');
  const presets = useTaskPresets();
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
        items={presets.map((t) => ({
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
