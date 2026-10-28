/** Baton 标识：一支指挥棒——握柄在左下，棒尖指向右上。 */
export function LogoMark({ size = 28, title }: { size?: number; title?: string }) {
  return (
    <svg
      className="bt-logo"
      width={size}
      height={size}
      viewBox="0 0 32 32"
      role={title ? 'img' : undefined}
      aria-label={title}
      aria-hidden={title ? undefined : true}
    >
      <rect width="32" height="32" rx="8" fill="var(--bt-primary)" />
      <path d="M10.5 21.5 23 9" stroke="#fff" strokeWidth="2.6" strokeLinecap="round" />
      <circle cx="9.6" cy="22.4" r="3.4" fill="#fff" />
      <circle cx="23.6" cy="8.4" r="1.6" fill="#BFF0EA" />
    </svg>
  );
}
