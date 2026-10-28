import XMarkdown, { type ComponentProps } from '@ant-design/x-markdown';
import { memo } from 'react';
import { isResultId } from '../format';
import { ResultChip } from './ResultChip';

function textOf(children: unknown): string {
  if (typeof children === 'string') return children;
  if (Array.isArray(children)) return children.map(textOf).join('');
  return '';
}

/** 行内代码里的 result_id 渲染成可点击的结果引用，其他代码原样显示。 */
function Code(props: ComponentProps) {
  const { children, block, className } = props;
  const text = textOf(children).trim();
  if (!block && isResultId(text)) return <ResultChip id={text} />;
  return <code className={className}>{children}</code>;
}

const COMPONENTS = { code: Code };

export const Markdown = memo(function Markdown({ content, streaming }: { content: string; streaming?: boolean }) {
  return (
    <XMarkdown
      className="bt-md"
      content={content}
      components={COMPONENTS}
      escapeRawHtml
      openLinksInNewTab
      streaming={{ hasNextChunk: Boolean(streaming), tail: Boolean(streaming) }}
    />
  );
});
