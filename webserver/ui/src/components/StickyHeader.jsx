export default function StickyHeader({
  as: Tag = 'div',
  className = '',
  children,
  ...props
}) {
  const mergedClassName = ['sticky-section-header', className]
    .filter(Boolean)
    .join(' ');
  return (
    <Tag className={mergedClassName} {...props}>
      {children}
    </Tag>
  );
}
