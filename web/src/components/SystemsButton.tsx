import { ApiOutlined, ExperimentOutlined, RobotOutlined } from '@ant-design/icons';
import { Button, Popover, Tag, Tooltip } from 'antd';
import type { CSSProperties } from 'react';
import { bridgeTools, useWorkbench } from '../context';

/** 顶栏：已接入系统的状态（接口数、界面是否已接入、加载错误）。 */
export function SystemsButton() {
  const { config, bridges } = useWorkbench();
  if (!config) return null;
  const errors = config.load_errors || {};
  const hasError = Object.keys(errors).length > 0;

  const content = (
    <div className="bt-systems">
      {config.systems.map((s) => {
        const connected = bridgeTools(bridges, s.system);
        return (
          <div key={s.system} className="bt-systems-row" style={{ '--c': s.color } as CSSProperties}>
            <i className="bt-systems-bar" />
            <div className="bt-systems-main">
              <div className="bt-systems-title">
                {s.title}
                <code>{s.system}</code>
              </div>
              <div className="bt-systems-desc">{s.description}</div>
              <div className="bt-systems-stat">
                <span>{s.api_tools} 个接口工具</span>
                {s.embed_url ? (
                  connected.length ? (
                    <span className="is-ok">界面已接入，{connected.length} 个界面工具</span>
                  ) : (
                    <span className="is-muted">界面未打开</span>
                  )
                ) : (
                  <span className="is-muted">无界面接入</span>
                )}
              </div>
              {errors[s.system]?.map((e) => (
                <div key={e} className="bt-systems-error">
                  {e}
                </div>
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );

  return (
    <Popover content={content} title="已接入的系统" trigger="click" placement="bottomRight">
      <Button type="text" className="bt-systems-btn" aria-label="已接入的系统">
        <ApiOutlined />
        <span className="bt-systems-dots" aria-hidden>
          {config.systems.map((s) => (
            <i key={s.system} style={{ '--c': s.color } as CSSProperties} />
          ))}
        </span>
        <span className="bt-hide-sm">{config.systems.length} 个系统</span>
        {hasError && <Tag color="error">有错误</Tag>}
      </Button>
    </Popover>
  );
}

export function ModeTag() {
  const { config } = useWorkbench();
  if (!config) return null;
  if (config.mode === 'demo') {
    return (
      <Tooltip title="未配置模型：用规则脚本代替模型规划，只识别演示指令，调用链路与真实模型相同。在 .env 中配置 LLM_BASE_URL、LLM_MODEL 后重启即可接入模型。">
        <Tag icon={<ExperimentOutlined />} className="bt-mode-tag">
          演示模式
        </Tag>
      </Tooltip>
    );
  }
  return (
    <Tooltip title="当前使用的模型">
      <Tag icon={<RobotOutlined />} className="bt-mode-tag">
        {config.planner}
      </Tag>
    </Tooltip>
  );
}
