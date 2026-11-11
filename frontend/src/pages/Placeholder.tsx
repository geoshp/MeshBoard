export function Placeholder({ title }: { title: string }) {
  return (
    <div className="page">
      <div className="page-title">{title}</div>
      <div className="card muted">…</div>
    </div>
  );
}
