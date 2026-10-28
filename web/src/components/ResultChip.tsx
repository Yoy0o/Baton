import { EnvironmentOutlined, PartitionOutlined, TableOutlined } from '@ant-design/icons';
import { Tooltip } from 'antd';
import { useWorkbench } from '../context';

const ICON: Record<string, React.ReactNode> = {
  feature_collection: <EnvironmentOutlined />,
  group_plan: <PartitionOutlined />,
};

/** 结果引用：点击后在右侧工作区的“结果”页打开。 */
export function ResultChip({ id, type, colorBy, title }: { id: string; type?: string; colorBy?: string | null; title?: string }) {
  const { openResult } = useWorkbench();
  return (
    <Tooltip title="在工作区查看这个结果">
      <button
        type="button"
        className="bt-result-chip"
        onClick={(e) => {
          e.stopPropagation(); // 在可折叠的步骤标题里时，不要顺带折叠/展开
          openResult({ resultId: id, colorBy, title });
        }}
      >
        {(type && ICON[type]) || <TableOutlined />}
        <span>{id}</span>
      </button>
    </Tooltip>
  );
}

/** 把文字里出现的结果编号（res_xxxxxxxx）渲染成可点击的结果引用。 */
export function LinkedText({ text }: { text: string }) {
  const parts = text.split(/(res_[0-9a-f]{8})/g);
  return <>{parts.map((part, i) => (i % 2 === 1 ? <ResultChip key={i} id={part} /> : part))}</>;
}
